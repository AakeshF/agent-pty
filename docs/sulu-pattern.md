# Sulu pattern

The helmsman to Kirk's commander. Where the [Captain Kirk pattern](captain-kirk-pattern.md) has the captain steer each pane by hand, and [Spock](spock-pattern.md) reports the fleet's state, Sulu takes a *backlog of work* and routes it to whatever panes are free — so the captain hands over a list of jobs instead of hand-feeding one command per pane per turn.

## The shape

Sulu is a dispatcher. Give it a list of commands and a pool of candidate panes; it finds the idle ones (via `Spock.assess`), assigns one command to each, runs them framed with a done-marker (via `mesh.send_with_done`), collects the replies, and — when there are more commands than free panes — queues the overflow and assigns it as panes free up. The captain gets back one dict: command → reply.

agent-pty's core is a transport; mesh is a commander's toolkit; Spock is the instrument panel; Sulu is the autopilot for the helm. Like mesh and Spock, it is opt-in and composes on the existing primitives — it changes no core, mesh, or Spock signature.

## How it relates to driving panes by hand

|  | Captain attention per job | Idle-pane discovery | Overflow handling | Result collection |
|---|---|---|---|---|
| Captain assigns each pane manually | one turn per assignment + one to collect | manual (read N screens) | manual bookkeeping | manual scrape |
| `Sulu.dispatch` (this) | one call for the whole backlog | automatic (`Spock.assess`) | automatic queue | structured dict |

The honest framing: when the captain has **one** job and **one** pane, just send it. Sulu earns its place once you have a *backlog* and a *pool* — the cost of the captain personally assigning, polling, and collecting from each pane every turn is exactly the [orchestrator-becomes-the-bottleneck cost](captain-kirk-pattern.md#the-honest-costs).

## Where it earns its place

- **Backlog fan-out.** N independent, self-terminating jobs and M idle panes (N may exceed M). Sulu packs the work onto the pool and drains the queue without the captain babysitting the assignment loop. This is the direct mitigation of the [orchestrator bottleneck](captain-kirk-pattern.md#the-honest-costs).
- **Pool utilization.** Panes that finish early are re-polled and reused, so a fast job doesn't leave a pane idle while a slow one is still running.
- **Structured collection.** One dict back, keyed by command, instead of the captain scraping N screens — the same [screen-scraping cost](captain-kirk-pattern.md#the-honest-costs) mesh's done-marker convention exists to contain.

It deliberately leans on its crewmates for the hard parts: `Spock.assess` decides which panes are idle, and `mesh.send_with_done` does the framing and reply extraction. Sulu adds only the routing/queueing layer on top.

## The honest costs

- **Idle-detection is Spock's double-sample.** A pane that happens to be quiet *during the settle window* reads as idle even mid-task; Sulu may pile a job onto a pane that is actually still working. Sulu mitigates the within-a-dispatch race by excluding panes it has already handed work to, but it cannot see work the captain (or another Sulu) started elsewhere.
- **Reply extraction is mesh's done-marker scraping.** Same false-positive/negative profile: if a command's own output contains the marker, or the marker never arrives within the per-command budget, the reply is empty. Sulu is for fast, deterministic, self-terminating shell work — not for prompts that might hang or echo the sentinel.
- **A timed-out job maps to `""`.** A command that never finds a free pane within `timeout`, or whose marker never lands, returns an empty string rather than raising — the backlog completes partially instead of crashing. Callers must check for `""`.
- **Sequential within a free batch.** Each `dispatch` runs the commands assigned in one poll cycle to completion before re-polling; it is not a fully-async scheduler. It keeps the pool busy, but it is a best-effort packer, not a real-time job runner.
- **Duplicate command strings collapse.** The result dict is keyed by command text; two identical commands share one entry (last completion wins).

These are real. Sulu exists to take the captain out of the per-job assignment loop, not to pretend the underlying heuristics are oracles.

## Module shape — `agent_pty.sulu`

Lives alongside the core, mesh, and Spock in the same package. New module file `agent_pty/sulu.py`. A `Sulu` namespace class mirrors `Pty`, `Mesh`, and `Spock` (a `staticmethod` wrapper over the module-level `dispatch`). Parallel MCP tooling under the `sulu_*` namespace is exposed by the same `agent-pty-mcp` server.

**Actuator (by design).** Unlike Spock, Sulu *sends keystrokes* — it is an actuator. It composes on `mesh.send_with_done` (which calls `io.send`) for actuation and `spock.assess` (read-only) for discovery.

## Public API

```python
Sulu.dispatch(
    commands: list[str],
    names: list[str] | None = None,
    done_marker: str = "<<END>>",
    timeout: float = 60.0,
    poll: float = 0.2,
) -> dict[str, str]
    # commands : shell commands (or prompts) to run, one per pane-slot.
    # names    : candidate panes (None -> all managed sessions).
    # Assign each command to an IDLE pane (Spock.assess -> state=="idle") and
    # run it via mesh.send_with_done framed so its output ends with done_marker.
    # More commands than idle panes -> queue and assign as panes free up
    # (re-poll Spock every `poll`s up to `timeout`). Returns command -> reply;
    # a command that never gets a free pane within `timeout` maps to "".
```

### How a command is framed

For a shell pane Sulu sends `f"{command}; printf '%s\\n' {shlex.quote(done_marker)}\n"`, so the marker is printed on its own line only after the command finishes. `mesh.send_with_done` then waits for the marker and returns the text bounded by the prompt and the marker, with the marker excluded.

### The assignment loop

1. Poll `Spock.assess(names)` for panes with `state == "idle"`, excluding any pane already handed work in this dispatch.
2. For each free pane, pop a command off the queue and run it to completion with `mesh.send_with_done` (bounded by the remaining time budget).
3. Repeat — re-polling Spock each cycle — until the queue is empty or `timeout` elapses.
4. Return the `command -> reply` dict; unassigned/never-finished commands keep their `""` default.

## Status

Pattern documented and **implemented 2026-06-13 as M11**. Composes on M6 (mesh) and M7 (Spock) and the frozen core (M1–M5); changes no existing signature.
