# Build plan

Five milestones, smallest useful slice first. Each milestone has concrete acceptance tests that run against a real tmux server — no mocks for the tmux integration surface, since the whole point of this project is empirical correctness against tmux's actual behavior.

## Stack decisions

- **Language:** Python 3.11+
- **tmux wrapper:** `libtmux` (handles socket management, send-keys encoding, capture-pane parsing)
- **Test runner:** `pytest`
- **Package manager:** `uv`
- **Layout:**
  ```
  agent_pty/
    __init__.py        public API re-exports
    session.py         spawn/kill/list (M1)
    io.py              send/snapshot (M2)
    wait.py            wait_for (M3)
    keys.py            named-key parser (M4)
    cli.py             optional CLI for manual testing
  tests/
    test_session.py
    test_io.py
    test_wait.py
    test_keys.py
    test_integration.py  drives real REPLs/TUIs end-to-end
  ```

## Cross-cutting rules

- **Socket:** use tmux's default socket so the human can `tmux attach -t <name>` directly. Prefix all session names with `agent-pty-` for hygiene; the public API takes the unprefixed name.
- **Public API only:** tests touch only the documented surface. No reaching into internals.
- **No future-proofing:** if a milestone needs a class hierarchy to ship, the design is wrong.
- **No carried debt:** every milestone ships with its tests passing and no `TODO` left behind.
- **Module size:** keep each module under ~150 lines. If it grows past that, the abstraction is wrong.

---

## M1 — Session lifecycle

**Goal:** create, list, and destroy named PTY sessions.

**API:**
```python
Pty.spawn(name: str, cmd: str | None = None, cwd: str | None = None,
          cols: int = 80, rows: int = 24) -> SessionHandle
Pty.kill(name: str) -> None
Pty.list() -> list[str]
```

**Acceptance tests (`tests/test_session.py`):**
1. `spawn("t1")` creates a tmux session; `tmux ls` shows `agent-pty-t1`.
2. `spawn("t1")` twice raises `SessionExistsError` on the second call.
3. `kill("t1")` removes it; `tmux ls` no longer shows it.
4. `kill("nope")` raises `SessionNotFoundError`.
5. `list()` returns `["t1", "t2"]` after two spawns; returns `[]` after killing both.
6. `spawn("t1", cmd="sleep 60", cwd="/tmp")` — pane runs sleep, in `/tmp`.
7. Custom `cols=120, rows=40` is reflected in tmux's pane dimensions.

**Cleanup:** test fixtures kill any leftover `agent-pty-*` sessions on teardown.

---

## M2 — Read & write

**Goal:** drive a session by sending text and reading the rendered screen.

**API:**
```python
Pty.send(name: str, text: str) -> None        # literal text only at M2
Pty.snapshot(name: str) -> str                # current rendered screen, plain text
```

**Acceptance tests (`tests/test_io.py`):**
1. `send("t1", "echo hello\n")` — `snapshot` eventually contains `"hello"`.
2. `snapshot` returns plain text, not raw ANSI escape codes (verify no `\x1b[`).
3. `snapshot` reflects current screen state, not stdout history: send `"clear\necho two\n"`, snapshot contains `"two"` and not `"hello"` from step 1.
4. `snapshot` of a freshly-spawned shell session returns the prompt.
5. Sending to a non-existent session raises `SessionNotFoundError`.

**Note:** "eventually contains" is a polling helper local to the test — `wait_for` doesn't exist yet at M2.

---

## M3 — Wait primitive

**Goal:** efficiently block until a pattern appears in the screen buffer.

**API:**
```python
Pty.wait_for(name: str, pattern: str | re.Pattern,
             timeout: float = 10.0) -> str   # returns snapshot at match time
```

**Acceptance tests (`tests/test_wait.py`):**
1. Send a command that prints after 0.5s; `wait_for` returns within 0.6s.
2. Pattern doesn't match within timeout → `TimeoutError`.
3. Drives Python REPL end-to-end: spawn `python3`, `wait_for(">>> ")`, send `"print(2+2)\n"`, `wait_for(r"^4$", re.MULTILINE)`, returns snapshot with `4`.
4. Returned snapshot contains the matched pattern.
5. Latency check: from match-becomes-true to return is <150ms (i.e. real wait, not 1s polling).

**Implementation note:** poll `capture-pane` at ~50ms interval. Not push-based, but cheap enough; if this becomes a bottleneck later we revisit with `tmux pipe-pane`.

---

## M4 — Named keys

**Goal:** send special keys (Enter, Esc, arrows, Ctrl-x) so TUIs and REPLs are usable.

**API:** `Pty.send` is extended; literal text and `<Key>` tokens are mixed freely.

**Recognized tokens** (case-insensitive inside angle brackets):
`<Enter>` `<CR>` `<Esc>` `<Tab>` `<BS>` `<Space>` `<Up>` `<Down>` `<Left>` `<Right>` `<Home>` `<End>` `<PgUp>` `<PgDn>` `<Del>` `<F1>`–`<F12>` `<C-x>` `<S-x>` `<M-x>` (Ctrl/Shift/Meta with any letter)

Literal `<` is escaped as `<<`.

**Acceptance tests (`tests/test_keys.py`):**
1. Drive vim end-to-end:
   ```python
   Pty.spawn("v", "vim /tmp/agent_pty_test.txt")
   Pty.wait_for("v", "VIM")            # vim splash or empty buffer signal
   Pty.send("v", "ihello world<Esc>:wq<Enter>")
   Pty.wait_for_session_end("v")        # or sleep + assert exited
   assert open("/tmp/agent_pty_test.txt").read().strip() == "hello world"
   ```
2. Python REPL with history: `send("p", "x = 1\n")`, then `send("p", "<Up>")`, snapshot shows `x = 1` on prompt line.
3. `<C-c>` interrupts a `sleep 60`: spawn shell, send `"sleep 60\n"`, wait briefly, send `"<C-c>"`, prompt returns within 1s.
4. `<<` in send produces literal `<` on screen.
5. Unknown token (`<Asdf>`) raises `KeyParseError`.

---

## M5 — Tool schema + packaging

**Goal:** installable, distributable, ready to wire into a Claude Code tool integration.

**Deliverables:**
1. `pyproject.toml` — installable via `uv pip install -e .` and `pip install .`
2. JSON schema describing the public API (for downstream tool integration)
3. README at repo root with quickstart: install → spawn → send → snapshot → kill
4. Example script: `examples/drive_python_repl.py` that exercises every public method
5. Minimal CLI (`agent-pty spawn|send|snapshot|kill|list`) for manual use and debugging

**Acceptance:**
1. Fresh venv: `uv venv && uv pip install -e .` succeeds, `python -c "import agent_pty"` works.
2. CLI roundtrip: `agent-pty spawn t1 && agent-pty send t1 "echo hi\n" && agent-pty snapshot t1 | grep hi && agent-pty kill t1`.
3. JSON schema validates against actual API signatures (test parses it and compares to inspect.signature).
4. Example script runs clean against a real Python REPL.

---

## Out of scope (explicit non-goals)

- Window/pane multiplexing surface (tmux's job; agent gets one screen per session)
- Resize-after-spawn (set on spawn, immutable for v1)
- Output streaming via callbacks/events (snapshot + wait_for is enough for v1)
- Authentication, multi-user, network-attached sessions (local sockets only)
- Replacing `Bash` for stateless one-shot commands

## Risk register

| Risk | Mitigation |
|---|---|
| `capture-pane` output differs across tmux versions | Test against tmux 3.5+ (your 3.6a is fine); pin minimum in pyproject |
| Curses redraws miss snapshot timing | `wait_for` solves the only case where this matters in practice |
| Race: `send` returns before keys are processed | `send` is fire-and-forget; pair with `wait_for` for sync points (this is the documented contract) |
| Session names collide with user's other tmux sessions | `agent-pty-` prefix on the actual tmux name |
