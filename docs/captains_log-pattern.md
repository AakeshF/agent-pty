# Captain's Log pattern

The fleet's flight recorder. Where the [Captain Kirk pattern](captain-kirk-pattern.md) drives panes and the [Spock pattern](spock-pattern.md) analyses them, Captain's Log only *witnesses*: it snapshots watched sessions on a poll loop and appends each changed screen to a transcript — in memory and, optionally, to a `jsonl` file — so a multi-agent run can be audited and replayed after the fact.

## The shape

Kirk acts; Spock reports; Captain's Log records. It takes a set of agent-pty panes, samples each one on an interval, and writes an append-only transcript of what each pane actually showed and when. It never sends a keystroke and never mutates a pane. It is the layer that turns "what did pane 3 do four turns ago?" — a question the captain otherwise can't answer without having paid tokens to read every screen every turn — into "open the log."

agent-pty's core is a transport; mesh is a commander's toolkit; Spock is the instrument panel; Captain's Log is the black box. Like mesh and Spock, it is opt-in and composes on the existing read-only primitives — it changes no core, mesh, or Spock signature.

## Which gap it addresses

The [Kirk doc's honest costs](captain-kirk-pattern.md#the-honest-costs) name two failures this targets directly:

- **Token economics compound.** Reading N screens every turn just to keep a mental record of the run is exactly the orchestrator-bottleneck cost. A recorder captures the timeline *once*, in the background, off the captain's context — the captain reads the transcript only when it needs to, and replay costs nothing at run time.
- **No structured handshake.** Sub-agent output is freeform and ephemeral; once the screen scrolls, it's gone. A persisted, timestamped, machine-readable (`jsonl`) transcript gives post-hoc structure to an otherwise unrecoverable run: audit, debugging, regression diffing, and deterministic replay.

## How it relates to reading screens directly

|  | Captures history | Off the captain's context | Replayable | Dedup of idle frames |
|---|---|---|---|---|
| Captain reads N raw screens each turn | only what it re-reads, in its context | no — every read costs tokens | no | n/a |
| `tmux` scrollback | bounded, per-pane, ANSI-laced | yes | manual | no |
| `CaptainsLog.start` / `replay` (this) | full timeline, all watched panes | yes — background thread | yes (`replay`) | yes (changed-only) |

The honest framing: if you only ever need *the current* state of one pane, just `snapshot` it. Captain's Log earns its place when you need the *timeline* of a *fleet* — for audit, for debugging a run that already finished, or for replaying it deterministically without re-running the agents.

## Where it earns its place

- **Audit trail.** A timestamped record of what each pane showed, for after-action review of an autonomous run — including runs that went wrong.
- **Deterministic replay.** `replay(path)` parses the `jsonl` back into `LogEntry` objects in capture order, so a UI or test can step through a run without a live tmux server.
- **Debugging the orchestrator.** When a multi-agent run misbehaves, the transcript shows which pane changed when — the evidence the captain's own context has long since dropped.
- **Cheap, background, deduped.** It runs on a daemon thread and only records a pane when its screen *changed* since the last capture, so a quiet fleet doesn't bloat the log every tick.

It deliberately leans on the same read-only primitives Spock uses (`io.snapshot`, `session.list_sessions`) rather than re-deriving capture logic, and it models its background thread on `mesh.Subscription` / the lifecycle monitor.

## The honest costs

- **Interval-granular, not keystroke-exact.** It samples every `interval` seconds (default 0.5s). A screen that flips and flips back inside one window is recorded once or not at all, and the timeline resolution is the poll interval. It's a transcript, not a TTY-faithful asciinema recording.
- **Dedup is screen-equality, not semantic.** Two captures with identical rendered screens collapse to one entry; a redraw that changes a single status-bar character counts as a change. Best-effort, like the rest of the bridge crew.
- **Disk + memory grow with change volume.** A busy fleet that changes every tick produces an entry every tick. The dedup helps with *idle* panes, not *busy* ones; a long, churny run can produce a large log.
- **A crash mid-write loses the tail.** Writes are line-buffered append; if the process dies, the last (partial) line may be lost. `replay` skips blank/garbled lines defensively, but the very end of a crashed run may be missing.

These are real. Captain's Log exists to make a fleet's history *recoverable and cheap*, not to be a perfect terminal recorder.

## Module shape — `agent_pty.captains_log`

Lives alongside the core, mesh, and spock in the same package. New module file `agent_pty/captains_log.py`. A `CaptainsLog` namespace class mirrors `Pty`, `Mesh`, and `Spock` (it exposes `start`/`replay` and the `LogEntry`/`Recorder` types). Parallel MCP tools under the `captains_log_*` namespace, exposed by the same `agent-pty-mcp` server.

**Read-only invariant (hard).** `captains_log.py` imports nothing that sends keystrokes. It never calls `agent_pty.io.send`, `Pty.send`, `mesh.pipe`, or `tmux send-keys`. It composes only on read-only primitives: `io.snapshot`, `session.list_sessions`, and `session.SessionNotFoundError`. Its only side effect is writing the transcript file. A reviewer can grep for this.

## Public API

```python
@dataclass
class LogEntry:
    timestamp: float    # time.monotonic() at capture
    name: str           # session name (PREFIX-stripped)
    screen: str         # the rendered screen at capture time

class Recorder:
    # Background thread; snapshots watched sessions on a poll loop and appends
    # a LogEntry whenever a pane's screen CHANGED since its last capture.
    entries -> list[LogEntry]   # property: a copy of the entries captured so far
    stop() / close()            # stop the background thread (aliases)
    __enter__ / __exit__        # usable as a context manager

CaptainsLog.start(names=None, path=None, interval=0.5) -> Recorder
    # names=None -> all managed sessions, re-resolved each tick (newborns picked up);
    # a list pins the watch set. path given -> also append jsonl, one entry per line.

CaptainsLog.replay(path) -> list[LogEntry]
    # parse a jsonl transcript back into LogEntry objects, in capture order.
```

### Recording loop (deterministic, read-only)

Each tick, for every watched session:

1. `snapshot(name)` — read the rendered screen (skip if `SessionNotFoundError`).
2. If the screen equals the last recorded screen for that pane → **skip** (dedup).
3. Otherwise append `LogEntry(monotonic, name, screen)` to memory and, if a path was given, write one JSON line to the file.

`names=None` re-resolves `list_sessions()` each tick, so a session born after recording started is picked up automatically; a pinned `names` list watches exactly those names and silently skips any not-yet-managed name.

## Status

Pattern documented and **implemented** 2026-06-13 as M12. Composes on the read-only primitives (M1–M5) and mirrors the mesh (M6) / Spock (M7) module shape; changes no existing signature.
