# Build plan

Six milestones. M1–M5 ship the core PTY transport (already complete). M6 adds the **mesh** layer — orchestration over multiple sessions, the load-bearing primitive for the [Captain Kirk pattern](captain-kirk-pattern.md). Each milestone has concrete acceptance tests that run against a real tmux server — no mocks for the tmux integration surface, since the whole point of this project is empirical correctness against tmux's actual behavior.

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
    schema.py          JSON schema for tool integration (M5)
    mcp.py             MCP server exposing pty_* tools (M5)
    mesh.py            orchestration layer over multiple sessions (M6)
  tests/
    test_session.py
    test_io.py
    test_wait.py
    test_keys.py
    test_mesh.py
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

## M6 — Mesh: orchestration across sessions

**Goal:** make the [Captain Kirk pattern](captain-kirk-pattern.md) — one agent driving N agents in other panes — practical, not just possible. Adds done-detection, push events, blocked-on-prompt detection, incremental snapshots, cross-pane piping, and lifecycle notifications. Lives in `agent_pty/mesh.py`; opt-in, no impact on the core API.

**Design principles:**
- Core API (M1–M5) is frozen. Mesh composes on top, never changes core signatures.
- Sentinel framing in v1 (callers prompt sub-agents to terminate replies with a marker). Designed so a future structured-output mode in agent CLIs can replace sentinels without breaking callers.
- Async by nature (subscriptions, lifecycle events). Provide a minimal blocking shim where parity with the core matters.

**API (sketch — to be refined during M6 design before any code):**
```python
Mesh.send_with_done(name: str, text: str,
                    done_marker: str = "<<END>>",
                    timeout: float = 60.0) -> str
    # send → wait for marker → return reply text bounded by it

Mesh.subscribe(name: str, pattern: str | re.Pattern) -> AsyncIterator[str]
    # yields snapshot each time pattern hits; cancellable

Mesh.detect_blocked(name: str) -> str | None
    # returns hint ("password:", "[y/n]", "Allow tool?") or None

Mesh.snapshot_since(name: str, marker: str) -> str
    # text appended after the most recent occurrence of marker

Mesh.pipe(from_name: str, to_name: str,
          region: str = "last_reply") -> None
    # copy region from one session into another's input stream

Mesh.lifecycle_events() -> AsyncIterator[Event]
    # session_born, session_died, session_idle, session_busy
```

**Acceptance tests (`tests/test_mesh.py`):**

1. **Done-detection round-trip.** Spawn a shell, send `printf 'reply text\n<<END>>\n'` framed via `send_with_done`. Returned string equals `"reply text"` (sentinel stripped, leading/trailing whitespace trimmed). Subsequent unrelated output on screen does not leak into the return.
2. **Subscription latency.** Subscribe to `"ERROR"` in pane A; 200ms later send a line containing `"ERROR"`. Subscription yields within 250ms of the send (proves push, not >150ms-interval polling).
3. **Subscription cancellation.** Cancelling an active subscription stops yielding within 100ms; tmux pipe is torn down (verify with `tmux list-panes -F`).
4. **Blocked detection — sudo.** Spawn `sudo -k -S true`; within 500ms `detect_blocked` returns a string containing `"password"`.
5. **Blocked detection — y/n prompt.** Spawn a shell, run a script that prints `"Continue? [y/N]"` and reads stdin; `detect_blocked` returns a hint mentioning the prompt within 500ms.
6. **Blocked detection — false positive guard.** A session running `top` (busy redrawing, no input expected) returns `None` from `detect_blocked`.
7. **Snapshot since.** Send 5 commands, capture marker via the most recent prompt, send 5 more; `snapshot_since(marker)` returns only the latter 5's output, with no overlap from the first batch.
8. **Pipe between panes.** Spawn panes A and B. Run `echo hi` in A; call `pipe("a", "b")`. Pane B's screen contains `"hi"` within 250ms. The captain's process never read `"hi"` itself (verifies the bypass — assert via instrumentation in the mesh layer).
9. **Lifecycle: birth + death.** Subscribe to `lifecycle_events()`; spawn pane C; expect `session_born("c")` event. Externally `tmux kill-session -t agent-pty-c`; expect `session_died("c")` within 500ms.
10. **Lifecycle: idle/busy heuristic.** Spawn a shell pane, no activity for 2s → `session_idle("x")` fires. Send a long-running command → `session_busy("x")` fires within 500ms.
11. **MCP surface parity.** Each Python `Mesh.*` method has a matching `mesh_*` MCP tool registered by `agent-pty-mcp`. Smoke test: drive each tool over JSON-RPC stdio.
12. **Captain-Kirk integration (manual / opt-in).** Drive a real `claude` instance in a sub-pane: `send_with_done` using sentinel convention returns a non-empty reply. Marked `@pytest.mark.manual` because it requires a working `claude` install and an API key; not in default CI.

**Implementation notes:**
- Subscriptions are backed by a per-subscription background thread polling `capture-pane` at 25ms (4× faster than `wait_for`'s 50ms loop). This delivers the push-style ergonomics callers want — an iterator that yields when patterns hit — without the engineering cost of `tmux pipe-pane` plumbing for v1. If 25ms latency proves insufficient or the polling cost shows up under many concurrent subscriptions, the implementation switches to `tmux pipe-pane` later without changing the public API.
- Blocked detection is heuristic (regex over the bottom 3 non-empty lines of the screen) and explicitly best-effort. False positives possible (e.g. a `read -p "Continue?"` script). False negatives possible (custom prompts).
- The pipe primitive uses `tmux send-keys` to inject the source region into the destination; the captain's tokens never see the payload as a return value. Newlines in the payload become Enter presses on the destination — caller is responsible for sanitization.
- Lifecycle events: a single shared `_LifecycleMonitor` thread polls `list_sessions()` and `snapshot()` at 500ms cadence and fans events out to all open `LifecycleStream` listeners. Idle threshold = 2.0s of unchanged screen.
- `<<` in the public API: any input text containing literal `<` characters round-trips correctly. `send_with_done` and `pipe` escape `<` → `<<` before calling `Pty.send`, so the named-key parser doesn't fire on user content. This is invisible to callers — they pass plain strings.

**Out of scope for M6:**
- Permission auto-approval policy (mesh detects blocked state; what to do about it is captain logic)
- Recursion depth enforcement (handled by env var convention, not the mesh API)
- Cross-machine / network mesh (local tmux only, same as the core)

---

## Out of scope (explicit non-goals)

- Window/pane multiplexing surface (tmux's job; agent gets one screen per session)
- Resize-after-spawn (set on spawn, immutable for v1)
- Authentication, multi-user, network-attached sessions (local sockets only)
- Replacing `Bash` for stateless one-shot commands

(Output streaming / push events were a v1 non-goal; promoted to **M6** as the load-bearing primitive for orchestration.)

## Risk register

| Risk | Mitigation |
|---|---|
| `capture-pane` output differs across tmux versions | Test against tmux 3.5+ (your 3.6a is fine); pin minimum in pyproject |
| Curses redraws miss snapshot timing | `wait_for` solves the only case where this matters in practice |
| Race: `send` returns before keys are processed | `send` is fire-and-forget; pair with `wait_for` for sync points (this is the documented contract) |
| Session names collide with user's other tmux sessions | `agent-pty-` prefix on the actual tmux name |
| **M6:** sub-agent ignores sentinel convention, `send_with_done` hangs to timeout | Document expected sentinel; `send_with_done` always returns within `timeout`; design API so structured-output replacement is a non-breaking change |
| **M6:** `tmux pipe-pane` semantics shift across versions | Pin behavior to tmux ≥3.5; integration test on supported floor in CI |
| **M6:** scope creep — mesh swallows the whole project | Core API frozen by contract. Mesh lives in its own module and MCP namespace. Any change to `Pty.*` requires explicit milestone, not a side effect of mesh work |
| **M6:** captain-kirk works in demos, falls apart at scale (token cost, brittleness) | Acceptance tests #2/#8/#9 cover the realistic-load failure modes; honest cost section in [captain-kirk-pattern.md](captain-kirk-pattern.md) sets expectations |
