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

## M7 — Spock: fleet analysis & advisory

**Goal:** give the captain a read-only "science officer" — one cheap call that observes the whole fleet and returns a structured logical assessment, so supervision doesn't mean re-reading N raw screens every turn. The complement to mesh: mesh *commands* panes, Spock *observes* them. Lives in `agent_pty/spock.py`; opt-in, composes on mesh and the core, changes no existing signature. See the [Spock pattern](spock-pattern.md) for framing and honest costs.

**Design principles:**
- **Read-only.** Spock never sends a keystroke and never mutates a pane. `spock.py` imports nothing that writes — no `io.send`, `Pty.send`, `mesh.pipe`, or `tmux send-keys`. A reviewer greps for this.
- **Composes on mesh.** Reuses `io.snapshot`, `session.list_sessions`, `session.SessionNotFoundError`, and `mesh.detect_blocked` — does not duplicate the blocked-prompt regexes.
- **Deterministic.** State is derived by a fixed precedence (dead > blocked > busy > idle) with a single shared settle window. Same inputs, same report.

**API (sketch):**
```python
@dataclass
class PaneReport:   name: str; state: str; hint: str | None; digest: str
@dataclass
class FleetReport:  panes: list[PaneReport]; deadlock: bool; summary: str
@dataclass
class Advisory:     name: str; priority: int; reason: str; action_hint: str

Spock.assess(names: list[str] | None = None) -> FleetReport
    # names=None -> all managed sessions; given -> only those (unmanaged => "dead").
    # Single shared settle window: all first snapshots, sleep once, all second snapshots.

Spock.diagnose(name: str) -> PaneReport
    # deep single-pane analysis; same state logic, its own settle window.

Spock.recommend(names: list[str] | None = None) -> list[Advisory]
    # assess() mapped to advisories, sorted by (priority, name) ascending.
```

State precedence (first match wins): **dead** (not managed / `SessionNotFoundError`) > **blocked** (`mesh.detect_blocked` returns a hint) > **busy** (screen changed across the settle window) > **idle** (unchanged, not blocked). `digest` = last non-empty line of the second snapshot, stripped. `deadlock = (≥1 blocked) and (no busy)`. `SETTLE_INTERVAL = 0.15`.

**Acceptance tests (`tests/test_spock.py`):**

1. **Idle vs. busy.** A quiet shell pane (at its prompt) reports `state=="idle"`; a pane running a screen-changing command (e.g. `yes` or a counting loop) reports `state=="busy"` within one settle window.
2. **Blocked.** A pane blocked on input (e.g. `sudo -k -S true` or a `[y/N]` script) reports `state=="blocked"` with a non-empty `hint`, matching what `mesh.detect_blocked` returns.
3. **Dead via diagnose.** `diagnose("never-spawned")` returns `PaneReport(name, "dead", None, "")`; killing a managed pane then assessing it reports `"dead"`.
4. **Deadlock flag.** A fleet with one blocked pane and no busy pane has `deadlock is True` and a summary starting `DEADLOCK`; adding a busy pane flips `deadlock` to `False`.
5. **Recommend ordering.** Given blocked + dead + idle + busy panes, `recommend()` returns advisories sorted by `(priority, name)` with blocked first (`priority==0`) and busy last (`priority==3`).
6. **Digest.** `digest` equals the last non-empty line of the rendered screen, trimmed; a freshly-cleared/blank or dead pane yields `""`.
7. **Read-only invariant.** Static check: `spock.py` source contains no `send`/`send-keys`/`pipe` write calls; and a behavioral check that running `assess`/`diagnose`/`recommend` against live panes leaves their snapshots unchanged.
8. **Names filter.** `assess(["a"])` reports only pane `a` even when `b` is also managed; a name in the list that isn't managed is reported `"dead"`; pane order follows the input list (and `list_sessions()` order when `names is None`).
9. **MCP surface parity.** Each `Spock.*` method has a matching `spock_*` MCP tool registered by `agent-pty-mcp`, returning JSON-able dicts/lists (dataclasses converted). Smoke test over JSON-RPC stdio.

**Implementation notes:**
- Idle/busy is a stateless double-sample, explicitly best-effort: a pane quiet *during* the 0.15s window reads idle even mid-task; a blinking cursor/clock reads busy. Signal, not guarantee.
- Blocked state inherits mesh's heuristic limits verbatim — Spock calls `mesh.detect_blocked` and re-derives nothing.
- The fleet sleeps **once** per `assess`, not per pane, so a large fleet still pays a single `SETTLE_INTERVAL` of latency.
- Keep `spock.py` under ~150 lines (project module-size rule); it is achievable well under.

**Out of scope for M7:**
- Acting on the assessment (responding to prompts, respawning dead panes) — that is captain/mesh logic; Spock only advises.
- Stateful trend tracking across calls (each assess is point-in-time; a stale report can mislead).
- Tuning/per-pane override of `SETTLE_INTERVAL` (one module constant for v1).

---

## M8 — Uhura: communications protocol

**Goal:** turn the captain's brittle "write an end-with-marker prompt, then screen-scrape" loop into a clean framed request/response, and add a fleet broadcast. Where mesh gave *done-detection* (the matching half), Uhura gives the *handshake* (the framing half) and an optional typed (JSON) reply. Lives in `agent_pty/uhura.py`; opt-in actuator, composes on `mesh.send_with_done`, changes no core or mesh signature. See the [Uhura pattern](uhura-pattern.md).

**Design principles:**
- **Never reimplement done-detection.** `ask` appends one standard instruction line ("end your reply with `<<END>>` on its own line") and delegates the send-and-wait — plus literal-`<` round-tripping — to `mesh.send_with_done`.
- **Typed when asked.** `want_json` extracts the first JSON object/array (fenced ` ```json ` block, else a balanced, string-aware `{...}`/`[...]` span) and `json.loads` it; on failure returns `{"_raw", "_error"}` rather than raising.
- **Graceful degradation.** `broadcast` swallows every per-pane failure (timeout, `SessionNotFoundError`, …) into `""`, so the returned dict always has one key per requested name; one bad pane never sinks the batch.

**API (sketch):**
```python
Uhura.ask(name, request, done_marker="<<END>>", timeout=60.0, want_json=False) -> str | dict | list
    # frame request + standard marker instruction -> mesh.send_with_done -> reply.
    # want_json: extract+parse the first JSON object/array; {"_raw","_error"} on failure.

Uhura.broadcast(names, request, done_marker="<<END>>", timeout=60.0) -> dict[str, str]
    # same framed request to each name concurrently (one thread/pane), keyed by name;
    # a timed-out or gone pane maps to "".
```

**Acceptance tests (`tests/test_uhura.py`):**
1. **Framed reply.** `ask` against a shell-stub pane returns the reply bounded by the framing and marker.
2. **Marker + instruction noise stripped.** The returned string excludes the marker and the echoed instruction line, trimmed.
3. **Namespace parity.** `Uhura.ask` is the module-level function (staticmethod wrapper).
4. **`want_json` — bare object.** Parses a bare `{...}` in the reply into a dict.
5. **`want_json` — fenced block.** Parses a ` ```json ` fenced block.
6. **`want_json` — object inside prose.** Extracts the first balanced object embedded in surrounding text.
7. **`want_json` — parse failure.** A reply with no/invalid JSON returns `{"_raw", "_error"}` rather than raising.
8. **Broadcast keying.** `broadcast` returns replies keyed by name; a timed-out pane maps to `""`; one key per input name.
9. **Real `claude` (manual).** `@pytest.mark.manual`: frame a request to a real `claude` CLI and get a structured reply.

---

## M9 — Scotty: resilience & budget

**Goal:** keep an unattended fleet *running and affordable*. Mesh emits a `died` lifecycle event but nothing acts on it, and tmux is amnesiac about how a pane was spawned. Scotty keeps a registry of respawn specs so a dead pane comes back as it was, runs a background Supervisor that auto-repairs within a restart budget, and answers the cheapest resource question (`over_budget`). Lives in `agent_pty/scotty.py`; opt-in actuator (it `spawn`s, never types), changes no core/mesh/Spock signature. See the [Scotty pattern](scotty-pattern.md).

**Design principles:**
- **Restore the spec, not the session.** A respawned pane is a brand-new process from its recorded `cmd`/`cwd`/geometry — in-memory state and scrollback are gone by design.
- **Bounded churn.** `restarts_max` caps the classic crash-loop: past budget Scotty leaves the pane dead for the captain (or Spock) to surface.
- **Composes on `session` only.** Imports `session.spawn`/`session.list_sessions`; never `io.send` or any keystroke path.

**API (sketch):**
```python
@dataclass
class Spec:  name; cmd; cwd; cols; rows; restarts=0

Scotty.register(name, cmd=None, cwd=None, cols=80, rows=24) -> None   # record/replace recipe; re-register resets restarts
Scotty.forget(name) -> None                                          # drop spec (no-op if unknown)
Scotty.repair(name) -> str    # registered+dead -> respawn, restarts+=1; alive -> no-op; unregistered -> ValueError
Scotty.status() -> list[Spec] # independent copies of the registry
Scotty.supervise(restarts_max=3, poll=0.5) -> Supervisor  # daemon thread; auto-repairs; context-manager, weakref-finalized
Scotty.over_budget(max_panes) -> bool   # len(list_sessions()) > max_panes (strict)
```

Recovery precedence (first match wins): **unregistered** → `ValueError` > **alive** → no-op > **dead** → respawn.

**Acceptance tests (`tests/test_scotty.py`):**
1. **Register records spec.** `register` then `status` reflects the spec.
2. **Status copies.** `status` returns independent copies, safe to mutate.
3. **Re-register resets restarts.** Re-registering a name replaces the spec and zeroes `restarts`.
4. **Forget drops it.** `forget` removes the spec (no-op if unknown).
5. **Repair respawns a killed pane.** A pane killed outside Scotty's knowledge is respawned by `repair`, `restarts` increments.
6. **Repair alive is a no-op; unregistered raises** `ValueError`.
7. **Supervisor auto-repairs.** A killed registered pane is respawned by the Supervisor within one poll window.
8. **Supervisor respects the budget.** A pane past `restarts_max` is abandoned, not respawned forever.
9. **Supervisor context manager stops the thread** on `__exit__`.
10. **`over_budget`** is strict `len(list_sessions()) > max_panes` (at-budget is not over).

---

## M10 — Prime Directive: policy / auto-approval

**Goal:** act on the deadlock signal Spock only *names*. Given a blocked pane, consult a `Policy` and auto-approve, auto-deny, or escalate to the human — answering the routine `y/n` churn locally so only `escalate` costs the captain a turn. The deferred Kirk-doc open question ("permission auto-approval, both behind a flag"), answered. Lives in `agent_pty/prime_directive.py`; opt-in actuator (the only policy-tier module that sends keys), composes on `mesh.detect_blocked` (read) + `io.send` (actuate). See the [Prime Directive pattern](prime_directive-pattern.md).

**Design principles:**
- **Hard-coded secrets floor.** A hint containing `password`/`passphrase`/`2fa`/`verification`/`secret` always resolves to `escalate`, checked *before* any policy rule. No policy can override it — the override is in code, not config.
- **Conservative by default.** `policy=None` and `Policy.conservative()` escalate everything; automation is opt-in per policy.
- **Best-effort actuation.** `approve`/`deny` are fire-and-forget keystrokes with no read-back; a *missed* prompt is the safe failure (`none`, do nothing).

**API (sketch):**
```python
@dataclass
class Policy:
    rules: dict[str, str]      # case-insensitive substring of hint -> "approve"|"deny"|"escalate"
    default: str = "escalate"
    @staticmethod
    def conservative() -> Policy   # escalate EVERYTHING
    @staticmethod
    def permissive() -> Policy     # approve y/n / continue / approval; secrets still escalate

PrimeDirective.resolve(name, policy=None) -> str   # "none"|"approve"|"deny"|"escalate"
PrimeDirective.enforce(name, policy=None, approve_keys="y<Enter>", deny_keys="n<Enter>") -> str
```

Decision precedence: **none** (not blocked) > **escalate** (secret override) > **policy rule** (first substring match) > **policy.default**.

**Acceptance tests (`tests/test_prime_directive.py`):**
1. **Conservative resolves `escalate`** on a y/N prompt and **enforce does not answer** it.
2. **Default policy is conservative** (`policy=None`).
3. **Permissive resolves `approve`** and **enforce completes the `read`** (the prompt unblocks).
4. **Deny enforce sends "n".**
5. **Secret prompt overrides permissive to `escalate`** and **enforce does not answer** a password prompt.
6. **Unblocked pane resolves `none`** and enforce does nothing.
7. **`default` applies when no rule matches.**
8. **Namespace exposes `Policy`** (`PrimeDirective.Policy`).

---

## M11 — Sulu: helm dispatcher

**Goal:** take a *backlog* of self-terminating jobs and a *pool* of panes, route each command to a free pane, run it framed with a done-marker, queue the overflow, and return one `command -> reply` dict — so the captain hands over a list instead of hand-feeding one command per pane per turn. Lives in `agent_pty/sulu.py`; opt-in actuator, composes on `spock.assess` (idle discovery) + `mesh.send_with_done` (framing/extraction). See the [Sulu pattern](sulu-pattern.md).

**Design principles:**
- **Lean on crewmates.** Spock decides which panes are idle; mesh frames and extracts the reply. Sulu adds only the routing/queueing layer.
- **Pool utilization with a within-dispatch race guard.** Excludes panes it has already handed work to this dispatch; re-polls as panes free up.
- **Partial completion over crashing.** A command that never finds a free pane (or whose marker never lands) within `timeout` maps to `""`; duplicate command strings collapse to one key.

**API (sketch):**
```python
Sulu.dispatch(commands, names=None, done_marker="<<END>>", timeout=60.0, poll=0.2) -> dict[str, str]
    # assign each command to an idle pane (Spock.assess state=="idle"); run via
    # mesh.send_with_done framed so output ends with done_marker; queue overflow,
    # re-poll Spock every `poll`s up to `timeout`. command -> reply; "" if never run.
```

Framing per command: `f"{command}; printf '%s\\n' {shlex.quote(done_marker)}\n"`.

**Acceptance tests (`tests/test_sulu.py`):**
1. **Two commands, two panes** — both assigned, both replies collected.
2. **More commands than panes queues** — overflow drains as panes free up.
3. **`names=None` uses all managed sessions** for the pool.
4. **No free pane times out to `""`** rather than raising.
5. **Custom `done_marker`** is honored.
6. **Namespace `Sulu.dispatch`** is the module-level function.

---

## M12 — Captain's Log: flight recorder

**Goal:** witness, don't drive. Snapshot watched sessions on a poll loop and append each *changed* screen to a transcript — in memory and optionally to a `jsonl` file — so a multi-agent run is auditable and replayable after the fact, off the captain's context. Lives in `agent_pty/captains_log.py`; **read-only** (its only side effect is writing the file), composes on `io.snapshot` + `session.list_sessions`. See the [Captain's Log pattern](captains_log-pattern.md).

**Design principles:**
- **Read-only invariant (hard).** Imports nothing that sends keystrokes — no `io.send`, `Pty.send`, `mesh.pipe`, or `tmux send-keys`. A reviewer greps for this.
- **Changed-only dedup.** A pane is recorded only when its rendered screen differs from its last capture, so a quiet fleet doesn't bloat the log every tick.
- **Crash-tolerant replay.** `replay` parses `jsonl` back into `LogEntry` objects in capture order, skipping blank/garbled lines defensively.

**API (sketch):**
```python
@dataclass
class LogEntry:  timestamp; name; screen

class Recorder:   # daemon thread; .entries (copy), stop()/close(), context manager

CaptainsLog.start(names=None, path=None, interval=0.5) -> Recorder
    # names=None -> all managed sessions re-resolved each tick (newborns picked up);
    # path -> also append one JSON line per entry.
CaptainsLog.replay(path) -> list[LogEntry]
```

**Acceptance tests (`tests/test_captains_log.py`):**
1. **Records a changed screen to memory.**
2. **Records to file and replays** — `jsonl` round-trips back to `LogEntry` objects (tmp file).
3. **Idle pane is deduped** — an unchanged screen produces no new entry.
4. **A new change after idle logs again.**
5. **Context manager records and stops** the thread on exit.
6. **Read-only check** — recording leaves panes' snapshots unchanged (never types).
7. **`names=None` records all managed sessions.**
8. **Namespace exposes** `start`/`replay` and the `LogEntry`/`Recorder` types.

---

## M13 — Red Alert: escalation siren

**Goal:** push a notification to the human the moment the fleet needs them. Spock computes a deadlock flag and names dead panes, but a report only helps if someone reads it. Red Alert watches the fleet on a background thread and, on a *new* problem, fires a side-effecting notifier (desktop toast via `notify-send`, stderr fallback, or any `callable(str)`). Lives in `agent_pty/red_alert.py`; opt-in, **read-only on panes**, composes on `spock.assess`. See the [Red Alert pattern](red_alert-pattern.md).

**Design principles:**
- **Owns one decision: when to bother a human.** It never re-derives the deadlock/blocked heuristics — `deadlock` is exactly Spock's flag, `death` is exactly "not in `list_sessions` / `snapshot` raised."
- **Consecutive-identical dedup.** A persisting deadlock fires once; a return to health resets state; a fresh problem re-alerts.
- **Best-effort, non-fatal notifier.** `notify-send` failures fall back to stderr; a broken custom notifier is swallowed so it can't kill the watcher.

**API (sketch):**
```python
@dataclass
class Alert:  kind  # "deadlock"|"death"
              detail; names

RedAlert.check(names=None) -> Alert | None     # deadlock preferred over death; None when fine
RedAlert.notify(message, notifier=None) -> None
RedAlert.watch(names=None, notifier=None, poll=0.5) -> Alerter   # background; notify on a NEW alert; context manager
```

**Acceptance tests (`tests/test_red_alert.py`):**
1. **`check` returns a `deadlock` alert** for a blocked-only fleet.
2. **`check` prefers `deadlock` over `death`** when both hold.
3. **`check` returns `death`** for an unmanaged name and **after a kill.**
4. **`check` returns `None`** for an idle/healthy fleet.
5. **`notify` calls a custom notifier** with the message.
6. **`watch` fires on a deadlock** via the notifier.
7. **`watch` dedups consecutive identical alerts** (fires once).
8. **Alerter context manager** stops the thread on exit.
9. **Read-only check** — the watcher never types into panes.
10. **Namespace + `Alert` dataclass shape.**

---

## M14 — Holodeck: sandboxed worktree stage

**Goal:** the safe substrate for the **worktree swarm** — N sub-agents, one per `git worktree`, each editing/building/committing in genuine filesystem isolation while the captain merges afterward. Each simulation gets a fresh worktree (its own checkout, optional branch, own dir) and a pane spawned inside it. Lives in `agent_pty/holodeck.py`; opt-in actuator that shells out to `git` (it builds/strikes the stage; it does **not** drive pane contents — that's mesh's job), composes on `session.spawn`/`kill`. See the [Holodeck pattern](holodeck-pattern.md).

**Design principles:**
- **Isolation per simulation.** `git -C <base> worktree add [-b <branch> | --detach] <dir>`, then spawn the pane in that dir. A registry maps `name -> Simulation(name, worktree, base, branch)` so `create`/`destroy`/`list` share one view.
- **Forced teardown is not reversible.** `destroy` runs `git worktree remove --force` — uncommitted work in the worktree is discarded; the captain must commit before destroying.
- **Best-effort cleanup, transparent failures.** Falls back to `rmtree` + `git worktree prune` if git refuses; a failed `create` rolls back the worktree before re-raising so nothing leaks.

**API (sketch):**
```python
@dataclass
class Simulation:  name; worktree; base; branch

Holodeck.create(name, base=None, branch=None, cmd=None, cols=80, rows=24) -> str
    # base must be a git repo (else RuntimeError); mkdtemp worktree (prefix
    # "agent-pty-holo-"); git worktree add; spawn pane in it; register. Rolls back on spawn failure.
Holodeck.destroy(name) -> None    # kill pane (ignore dead) -> git worktree remove --force -> deregister
Holodeck.list() -> list[str]      # active simulation names, sorted
```

**Acceptance tests (`tests/test_holodeck.py`):**
1. **`create` makes a worktree and a pane** (against an ad-hoc temp git repo).
2. **`create` returns the name.**
3. **Pane cwd is the worktree.**
4. **`create(branch=...)` makes that branch.**
5. **`destroy` removes pane and worktree.**
6. **`list` tracks active simulations.**
7. **`create` outside a git repo raises** `RuntimeError`.
8. **Simulations are isolated** — a file written in one worktree is not visible in another.

---

## M15 — Bones: health pathology

**Goal:** diagnose *sickness* in a still-running pane — the failure modes that look like work but aren't. Where Spock reports coarse `state`, Bones returns a `Diagnosis` listing concrete *symptoms* (errors / thrashing / hung), separating a thrashing loop from a healthy build and a "frozen mid-task" pane from "done and waiting." Lives in `agent_pty/bones.py`; **read-only** (NEVER types), composes on `io.snapshot` + `session.list_sessions`. See the [Bones pattern](bones-pattern.md).

**Design principles:**
- **Read-only invariant (hard).** No `io.send`/`Pty.send`/`mesh.pipe`/`send-keys`. Composes only on read-only primitives. A reviewer greps for this.
- **Screen heuristics, point-in-time.** Every detector reads the rendered screen, not process state; confirm before acting on a single sample.
- **Deterministic symptom set.** Thresholds are module constants (`SETTLE_INTERVAL`, `THRASH_REPEATS`, the error-signature regexes, the prompt-ending set).

**API (sketch):**
```python
@dataclass
class Diagnosis:  name; healthy  # == (symptoms == [])
                  symptoms       # subset of {"dead","errors","thrashing","hung"}, stable order

Bones.examine(name) -> Diagnosis
Bones.triage(names=None) -> list[Diagnosis]   # sickest-first: dead worst, then descending symptom count, ties by name
```

Symptom detection: **dead** (unmanaged / `SessionNotFoundError`; stands alone) > **errors** (case-insensitive signature match: traceback/fatal/panic/segfault/`error:`/exception/command not found) | **thrashing** (a visible non-empty line repeated > `THRASH_REPEATS`) | **hung** (unchanged across the settle window and bottom line not ending in `$`/`#`/`>>>`/`>`).

**Acceptance tests (`tests/test_bones.py`):**
1. **Quiescent prompt is healthy** (no symptoms).
2. **Python traceback reports `errors`.**
3. **Command-not-found reports `errors`.**
4. **A thrashing pane reports `thrashing`.**
5. **Unmanaged name is `dead`.**
6. **Killed pane is `dead`.**
7. **`triage` sorts sickest-first.**
8. **`triage(None)` covers all managed sessions.**
9. **Read-only check** — examining a pane never types into it.

---

## M16 — Transporter: context checkpoint / restore

**Goal:** checkpoint a pane's *visible context* (rendered screen + recoverable spawn spec) to JSON, then restore it into a fresh pane later — survive a crash, a reboot, or a context-window reset. Explicitly **not** process migration: restore spawns a new pane running the same command in the same dir and hands the captured screen back as context. Lives in `agent_pty/transporter.py`; opt-in actuator (`beam_in` spawns; never types), composes on `io.snapshot` + pane metadata (read) + `session.spawn`. See the [Transporter pattern](transporter-pattern.md).

**Design principles:**
- **Photo, not a process.** Only the visible screen is captured; in-memory state, scrollback beyond the screen, env mutations, and children do not survive.
- **Caller owns re-injection.** `beam_in` deliberately does **not** type the captured screen back (replaying output as keystrokes would be guessing) — read `load(path).screen` and feed it back as context yourself.
- **Best-effort `cmd`, reliable `cwd`/geometry.** tmux exposes the current foreground command, not the original argv; pass `cmd=` to `beam_in` to restore the real one.

**API (sketch):**
```python
@dataclass
class Checkpoint:  name; screen; cmd; cwd; cols; rows; timestamp

Transporter.beam_out(name, path) -> str    # snapshot + metadata -> Checkpoint JSON; SessionNotFoundError if dead
Transporter.load(path) -> Checkpoint       # round-trip the file (also accepts a bare, unwrapped checkpoint)
Transporter.beam_in(name, path, cmd=None, cols=None, rows=None) -> str
    # spawn a NEW pane from the stored/overridden spec; reuse stored cwd. Does NOT auto-inject the screen.
```

**Acceptance tests (`tests/test_transporter.py`):**
1. **`beam_out` writes the screen** including a known token (tmp file).
2. **`beam_out` records geometry** (cols/rows).
3. **`beam_out` on a dead pane raises** `SessionNotFoundError`.
4. **`load` round-trips the checkpoint.**
5. **`beam_in` spawns a new pane.**
6. **`beam_in` does not replay the old screen** as keystrokes.
7. **`beam_in(cmd=...)` overrides the stored cmd.**

---

## M17 — Worf: adversarial review

**Goal:** clean-room adversarial review in one call. `review` spins up a *fresh* reviewer pane (no shared context with the target), captures the target's screen, asks the reviewer (via `mesh.send_with_done`) for a verdict, and returns it — leaving the reviewer pane running for follow-ups. A critic with amnesia is harder than the author grading their own homework. Lives in `agent_pty/worf.py`; opt-in actuator (spawns + drives the reviewer pane it owns; reads the target read-only), composes on `session.spawn`/`kill` + `io.snapshot` + `mesh.send_with_done`. See the [Worf pattern](worf-pattern.md).

**Design principles:**
- **Independence is the point.** The reviewer never saw how the work was produced, only the artifact (the target's screen, or its last `lines` non-empty lines).
- **Cheap for the captain.** Only the verdict crosses back; the captain never re-reads the target. Pass a different `reviewer_cmd` for a cross-model second opinion.
- **Keep-or-dismiss lifecycle.** The reviewer pane is left running for follow-up questions; `dismiss` tears it down.

**API (sketch):**
```python
Worf.review(target_name, instruction, reviewer_name="worf-reviewer",
            reviewer_cmd=None, done_marker="<<END>>", timeout=60.0, lines=None) -> str
    # spawn an independent reviewer; capture target (full screen or last `lines` non-empty lines);
    # ask via mesh.send_with_done bounded by done_marker; return the verdict. Reviewer LEFT RUNNING.
Worf.dismiss(reviewer_name) -> None   # kill the reviewer pane
```

**Acceptance tests (`tests/test_worf.py`):**
1. **`review` returns a stub verdict** (shell reviewer, no real LLM).
2. **`review` spawns an independent reviewer pane** (no shared context with the target).
3. **`dismiss` kills the reviewer pane.**
4. **`dismiss` on an unknown pane raises** (`SessionNotFoundError` → `ValueError` at the MCP edge).
5. **`review(lines=N)`** still returns a verdict from the last N non-empty lines.
6. **Default `reviewer_cmd=None` spawns a plain shell** reviewer.
7. **Real `claude` reviewer (manual).** `@pytest.mark.manual`: spin up a real `claude` reviewer pane and get a verdict.

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
