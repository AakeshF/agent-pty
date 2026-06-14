# Spock pattern

The read-only science officer to Kirk's commander. Where the [Captain Kirk pattern](captain-kirk-pattern.md) drives panes — sending keys, piping, steering — Spock only *observes* the fleet and returns a structured logical assessment, so the captain doesn't burn tokens reading N raw screens.

## The shape

Kirk acts; Spock reports. Spock takes the whole fleet of agent-pty panes, samples each one, and returns a deterministic, token-cheap report: per-pane state (`dead`/`blocked`/`idle`/`busy`), a one-line digest, a fleet-wide deadlock flag, and a prioritized list of advisories. It never sends a keystroke and never mutates a pane. It is the layer that turns "the captain re-reads six screens every turn" into "the captain reads one paragraph."

agent-pty's core is a transport; mesh is a commander's toolkit; Spock is the instrument panel. Like mesh, it is opt-in and composes on the existing primitives — it changes no core or mesh signature.

## How it relates to reading screens directly

|  | Token cost per check | Structured output | Deadlock signal | Attention triage |
|---|---|---|---|---|
| Captain reads N raw screens | N full screens every turn | no — freeform, ANSI-laced | manual, easy to miss | manual |
| `Spock.assess` / `recommend` (this) | one report (~N short lines) | yes — dataclasses, JSON-able over MCP | computed (`deadlock`) | computed (`priority`) |

The honest framing: when the captain drives **one** pane, just read it. Spock earns its place once you have a *fleet* — the cost of N screens × every supervision turn is exactly the "orchestrator becomes the bottleneck" failure mode from the Kirk doc.

## Where it earns its place

- **Deadlock detection.** The single highest-value signal. If at least one pane is blocked on a prompt and *nothing* is busy, the whole fleet is stalled waiting on the captain or human — and the captain may not notice for several turns. Spock names it in one field. This directly mitigates the [permission-deadlocks cost](captain-kirk-pattern.md#the-honest-costs).
- **Prioritized attention triage.** `recommend` sorts panes by urgency (blocked → dead → idle → busy) so the captain spends tokens on the pane that needs them, not on the three that are quietly working.
- **Cheap fleet status.** One `assess` replaces N `snapshot` reads. Mitigates the [token-economics and orchestrator-bottleneck costs](captain-kirk-pattern.md#the-honest-costs).
- **Idle = "likely finished" signal.** A pane whose screen stopped changing is probably done — a cue to collect output and dispatch the next instruction, without scraping for a sentinel.

It deliberately leans on mesh for the hard part: it calls `mesh.detect_blocked` rather than re-deriving the blocked-prompt heuristic, so brittle [screen-scraping](captain-kirk-pattern.md#the-honest-costs) lives in exactly one place.

## The honest costs

- **Idle/busy is a best-effort double-sample.** Spock snapshots, waits `SETTLE_INTERVAL` (0.15s), snapshots again: changed → busy, unchanged → idle. A pane that happens to be quiet *during that window* reads as idle even mid-task (e.g. waiting on a network call); a pane with a blinking cursor or clock reads as busy forever. It's a signal, not a guarantee.
- **Blocked detection inherits mesh's limits.** Spock's `blocked` state is exactly whatever `mesh.detect_blocked` decides — same regex heuristic, same false positives (a `read -p "Continue?"` script) and false negatives (custom prompts).
- **A stale assessment can mislead.** The report is a point-in-time sample. A pane can unblock, finish, or crash the instant after `assess` returns. Treat it as a recent observation, never a lock.
- **Spock adds latency.** Every `assess`/`diagnose` pays one shared `SETTLE_INTERVAL` settle window (the fleet sleeps *once*, not per-pane). Cheap, but non-zero — don't call it in a tight loop.

These are real. Spock exists to bring the cost of supervising a fleet down to manageable, not to pretend the heuristics are oracles.

## Module shape — `agent_pty.spock`

Lives alongside the core and mesh in the same package. New module file `agent_pty/spock.py`. A `Spock` namespace class mirrors `Pty` and `Mesh` (`staticmethod` wrappers over module-level functions). Parallel MCP tools under the `spock_*` namespace, exposed by the same `agent-pty-mcp` server.

**Read-only invariant (hard).** `spock.py` imports nothing that sends keystrokes. It never calls `agent_pty.io.send`, `Pty.send`, `mesh.pipe`, or `tmux send-keys`. It composes only on read-only primitives: `io.snapshot`, `session.list_sessions`, `session.SessionNotFoundError`, and `mesh.detect_blocked`. A reviewer can grep for this.

## Public API

```python
@dataclass
class PaneReport:
    name: str
    state: str          # "dead" | "blocked" | "idle" | "busy"
    hint: str | None    # blocked-prompt hint when state=="blocked", else None
    digest: str         # last non-empty line of the rendered screen, trimmed ("" if blank/dead)

@dataclass
class FleetReport:
    panes: list[PaneReport]
    deadlock: bool      # True iff (>=1 pane blocked) AND (no pane busy)
    summary: str        # one-line synthesis

@dataclass
class Advisory:
    name: str
    priority: int       # 0=most urgent: blocked=0, dead=1, idle=2, busy=3
    reason: str         # human/LLM readable
    action_hint: str    # ADVISORY ONLY — Spock never acts

Spock.assess(names: list[str] | None = None) -> FleetReport
    # names=None -> all managed sessions; names given -> only those.
    # A name not currently managed is reported as state="dead".
    # Pane order follows the input order (or list_sessions() order).

Spock.diagnose(name: str) -> PaneReport
    # Deep single-pane analysis; same state logic, its own settle window.

Spock.recommend(names: list[str] | None = None) -> list[Advisory]
    # assess() mapped to advisories, sorted by (priority, name) ascending.
```

### State precedence (deterministic, read-only)

Checked in this order — first match wins: **dead > blocked > busy > idle**.

1. **dead** — name not in `list_sessions()`, or `snapshot` raises `SessionNotFoundError`.
2. **blocked** — `mesh.detect_blocked(name)` returns a non-empty hint (`hint` = that string).
3. **busy** — the rendered screen *changed* across the shared settle window.
4. **idle** — screen unchanged across the settle window and not blocked.

`digest` is the last non-empty line of the *second* snapshot, `.strip()`ed; `""` if none or dead.

For a fleet, `assess` takes all first snapshots, sleeps **once** for `SETTLE_INTERVAL`, then takes all second snapshots — a single shared window, never one sleep per pane.

### Deadlock

`deadlock = (any pane is "blocked") and (no pane is "busy")`. The fleet is waiting on the captain or human and nothing is making progress. Example summaries:

```
4 panes: 1 blocked, 1 idle, 2 busy.
DEADLOCK — b1 blocked on password prompt, nothing progressing (3 panes).
```

## Status

Pattern documented and **implemented** 2026-06-13 as M7 in the [build plan](build-plan.md#m7--spock-fleet-analysis--advisory). Composes on M6 (mesh) and the frozen core (M1–M5); changes no existing signature.
