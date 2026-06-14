# Transporter pattern

Checkpoint a pane's *visible context* to a file, then restore it into a fresh pane later. Where the [Captain Kirk pattern](captain-kirk-pattern.md) drives a live fleet, the Transporter is the save/load slot: it lets the captain freeze what a pane *looked like* and bring it back after a crash, a reboot, or a context-window reset.

## The honest scope (read this first)

tmux cannot migrate a live process. There is no supported way to freeze a running program's memory, file descriptors, and child processes and re-materialize them elsewhere. So the Transporter is **not** process migration. It is a **context checkpoint**: the rendered screen plus the spawn spec we can recover (cmd, cwd, geometry), written to JSON, and later used to *spawn a new pane*.

Restore gives you a clean pane running the same command in the same directory, plus the captured screen text handed back to the caller as context. It does **not** give you the old process's in-memory state, scrollback beyond the visible screen, environment mutations, or any child processes. Naming it "Transporter" is a wink at Star Trek; the device in the show reassembles you atom-for-atom. This one reassembles a *photograph of you* and starts a fresh body in the same spot. Believe the docstring, not the name.

## The shape

```
beam_out(name, path)  ->  reads snapshot + tmux metadata, writes Checkpoint JSON
load(path)            ->  Checkpoint dataclass (round-trips the file)
beam_in(name, path)   ->  session.spawn a NEW pane from the stored/overridden spec
```

`beam_in` deliberately does **not** type the captured screen back into the new pane. Replaying old output as keystrokes would be guessing — those lines would run as whatever they parse to (an `rm`, a `y`, a stray Enter). Instead the screen is returned via `load(path).screen` for the caller — typically an LLM agent — to feed back as context however it judges safe. This is the same "don't guess, hand it to the model" stance as `mesh.pipe`'s caveat about fire-and-forget injection.

agent-pty's core is a transport; mesh is a commander's toolkit; Spock is the instrument panel; the Transporter is the save/restore slot. Like the others it is opt-in and composes on the existing primitives (`io.snapshot`, `session.spawn`) — it changes no core, mesh, or Spock signature.

## Where it earns its place

- **Survive a crash or restart.** A long-lived agent pane dies (OOM, host reboot, tmux server kill). With a recent checkpoint the captain can respawn the pane in the right directory with the right command and re-prime the new agent with "here is where you were." Directly mitigates the [orchestrator-bottleneck / lost-work cost](captain-kirk-pattern.md#the-honest-costs): a crash no longer means starting from a blank screen.
- **Context-window handoff.** When a driving LLM's own context fills up, a checkpoint is a compact, JSON-able artifact it can store and reload — far cheaper than re-reading and re-summarizing raw screens every turn (the [token-economics cost](captain-kirk-pattern.md#the-honest-costs)).
- **Branch / experiment.** `beam_out` once, `beam_in` into two differently-named panes, override `cmd` on one. You get two fresh panes seeded from the same captured backdrop without re-deriving the spec by hand.
- **Audit / replay.** The checkpoint is a timestamped, plain-JSON record of exactly what a pane showed and how it was configured — greppable, diffable, committable.

The honest framing: if a pane is short-lived or trivially re-creatable, just respawn it — you don't need a checkpoint. The Transporter earns its place when *what was on the screen* is expensive to reconstruct (a long agent transcript, a half-finished diagnosis) and you want it back as context after the live process is gone.

## The honest costs

- **It is not process migration.** Said three times because it's the whole caveat. The restored pane is a fresh start; nothing in-flight survives.
- **`cmd` is best-effort.** tmux exposes the *current foreground command* (`pane_current_command`), not the original spawn argv — so a checkpointed `bash` pane records `"bash"`, not the rich `cmd` you spawned it with. Pass `cmd=` to `beam_in` to restore the real one. `cwd` and geometry are reliable.
- **Only the visible screen is captured.** `snapshot` returns the rendered pane, not full scrollback. Anything scrolled off is gone from the checkpoint.
- **The screen is a point-in-time photo.** Between `beam_out` and `beam_in` the world moves on; the captured directory may no longer hold the same files, the command may behave differently. Treat a restore as "resume with a remembered backdrop," never "nothing changed."
- **Caller owns re-injection safety.** Because `beam_in` won't type the old screen back, continuity is the caller's job — and that's the safe default. Auto-replay was rejected on purpose.

These are real. The Transporter exists to make "the pane is gone but I still need what it knew" survivable, not to pretend a screenshot is a live process.

## Module shape — `agent_pty.transporter`

Lives alongside the core, mesh, and Spock in the same package. New module file `agent_pty/transporter.py`. A `Transporter` namespace class mirrors `Pty`/`Mesh`/`Spock` (`staticmethod` wrappers over module-level functions) and re-exports the `Checkpoint` dataclass. It is an **actuator**: `beam_in` calls `session.spawn`. It composes only on `io.snapshot` (read), `io._get_pane` (read, for metadata), and `session.spawn` — it never sends keystrokes.

## Public API

```python
@dataclass
class Checkpoint:
    name: str
    screen: str
    cmd: str | None       # best-effort: tmux's current foreground command
    cwd: str | None        # pane's current path (reliable)
    cols: int | None
    rows: int | None
    timestamp: float

Transporter.beam_out(name: str, path: str) -> str
    # snapshot(name) + record metadata; write Checkpoint JSON to path; return path.
    # Raises SessionNotFoundError if the pane is dead.

Transporter.load(path: str) -> Checkpoint
    # Round-trip the file back into a Checkpoint.

Transporter.beam_in(name, path, cmd=None, cols=None, rows=None) -> str
    # load the Checkpoint and session.spawn a NEW pane named `name`; return `name`.
    # cmd/cols/rows args override the stored spec; stored cwd is reused.
    # Does NOT auto-inject the captured screen — read load(path).screen and feed it
    # back as context yourself.
```

### On-disk format

```json
{
  "version": 1,
  "checkpoint": {
    "name": "agent-1",
    "screen": "...rendered pane text...",
    "cmd": "bash",
    "cwd": "/home/me/project",
    "cols": 100,
    "rows": 30,
    "timestamp": 1781740800.0
  }
}
```

`load` also accepts a bare checkpoint object (no `version` wrapper) for forward tolerance.

## Status

Pattern documented and **implemented** 2026-06-13 as M16. Composes on the frozen core (M1–M5) and changes no existing signature.
