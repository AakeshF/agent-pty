# Scotty pattern

The chief engineer to Kirk's commander. Where the [Captain Kirk pattern](captain-kirk-pattern.md) drives panes and [Spock](spock-pattern.md) observes them, Scotty *keeps them running and keeps them affordable*. It closes two loops the rest of the fleet leaves open: crash-recovery and resource budget.

## The shape

Mesh emits a `died` [lifecycle event](captain-kirk-pattern.md) when a pane vanishes — but nothing in the fleet acts on it, and tmux is stateless about *how* a pane was spawned, so even a captain who notices the death has no recipe to bring it back. Scotty keeps a module-level **registry** of supervised specs (the exact `spawn` arguments), so a dead pane can be respawned as it was; a background **Supervisor** does this automatically up to a restart budget. It also answers the cheapest resource question — `over_budget(max_panes)` — so the captain can throttle before the [orchestrator-bottleneck / token-economics](captain-kirk-pattern.md#the-honest-costs) failure mode bites.

agent-pty's core is the transport; mesh is the commander; Spock is the instrument panel; Scotty is the engine room. Like mesh and Spock it is opt-in and composes on existing primitives — it changes no core, mesh, or Spock signature.

Unlike Spock (read-only), Scotty is an **actuator**: `repair` and the Supervisor call `session.spawn`. It does not, however, send keystrokes — a respawned pane starts fresh from its `cmd`.

## How it relates to the bare lifecycle event

|  | Detects death | Knows the spawn recipe | Brings the pane back | Bounds churn | Resource signal |
|---|---|---|---|---|---|
| `mesh.lifecycle_events` (`died`) | yes | no | no | n/a | no |
| Manual captain recovery | via the event | only if the captain remembered it | by hand, costing tokens | manual | manual |
| `Scotty.repair` / `Supervisor` (this) | via `list_sessions` | yes — the registry | automatically | `restarts_max` budget | `over_budget` |

The honest framing: when you run **one** pane and you'll notice if it dies, you don't need Scotty. It earns its place for *unattended, long-horizon* fleets — the [long-horizon babysitting](captain-kirk-pattern.md#where-it-earns-its-place) and [live-ops console](captain-kirk-pattern.md#where-it-earns-its-place) shapes, where a pane can crash at 3am and the captain isn't looking.

## Where it earns its place

- **Crash-recovery that survives tmux's amnesia.** tmux can list panes but cannot recreate a dead one — it never stored the `cmd`/`cwd`/size. The registry is the one place that recipe lives, so `repair` can respawn the pane exactly as spawned. This directly closes the gap mesh's `died` event opens but doesn't fill.
- **Unattended supervision.** The `Supervisor` is the automation: a daemon thread polling `list_sessions()` that respawns any registered pane that goes missing, no captain turn required. Built like `mesh.Subscription` (stoppable, context-manager, `weakref.finalize`).
- **Bounded restart churn.** `restarts_max` stops the classic crash-loop: a pane whose `cmd` dies on spawn would otherwise be respawned forever, burning CPU and masking the real failure. Past budget, Scotty leaves it dead and lets the captain (or Spock) surface it.
- **A throttle for token economics.** `over_budget(max_panes)` is one cheap comparison the captain can check before spawning the next sub-agent, mitigating the [token-economics / orchestrator-bottleneck cost](captain-kirk-pattern.md#the-honest-costs).

It pairs naturally with Spock: Spock *names* a `dead` pane in its advisories; Scotty *repairs* the ones it was told to supervise. Spock recommends, Scotty acts.

## The honest costs

- **It restores the spec, not the session.** A respawned pane is a brand-new process. In-memory state, scrollback, environment mutations, and any half-typed input from the dead pane are gone. Scotty brings back *a pane spawned the same way*, not the pane that died. For a stateful agent CLI, that means lost conversation context unless the agent persists it itself.
- **The registry is the source of truth, and it's in-memory.** If the orchestrator process restarts, the registry is empty until something re-registers. Scotty supervises what it's been told about — nothing more.
- **Polling, not events.** The Supervisor polls `list_sessions()` on an interval (default 0.5s), so a crashed pane is down for up to one poll before repair. Cheap, but non-zero; tune `poll` against how fast a death must be caught.
- **Restart budgets cap recovery, not just churn.** A flaky-but-recoverable pane that legitimately needs four restarts will be abandoned at `restarts_max=3`. The budget can't tell a crash-loop from bad luck; pick it knowing that.
- **`over_budget` counts, it doesn't weigh.** It's `len(list_sessions()) > max_panes` — a blunt pane count, blind to which panes are expensive. A useful guardrail, not a cost model.

These are real. Scotty exists to make an unattended fleet *survivable and affordable*, not to pretend a respawn is a resurrection.

## Module shape — `agent_pty.scotty`

Lives alongside the core, mesh, and Spock in the same package. New module file `agent_pty/scotty.py`. A `Scotty` namespace class mirrors `Pty`, `Mesh`, and `Spock` (`staticmethod` wrappers over module-level functions). Parallel MCP tools under the `scotty_*` namespace, exposed by the same `agent-pty-mcp` server.

It composes on `session.spawn` and `session.list_sessions` only; it never imports `io.send`, `Pty.send`, `mesh.pipe`, or otherwise types into a pane.

## Public API

```python
@dataclass
class Spec:
    name: str
    cmd: str | None
    cwd: str | None
    cols: int
    rows: int
    restarts: int = 0   # times Scotty has respawned this pane

Scotty.register(name, cmd=None, cwd=None, cols=80, rows=24) -> None
    # Record (or replace) the respawn recipe. Re-registering resets restarts.

Scotty.forget(name) -> None
    # Drop a spec from the registry. No-op if unknown.

Scotty.repair(name) -> str
    # registered + dead  -> respawn from spec, restarts += 1, return name
    # registered + alive -> no-op, return name
    # unregistered       -> raise ValueError

Scotty.status() -> list[Spec]
    # Independent copies of the registry — safe to keep/mutate.

Scotty.supervise(restarts_max=3, poll=0.5) -> Supervisor
    # Stoppable daemon thread; auto-repairs registered panes that go missing,
    # up to restarts_max. .close()/.stop(), context-manager, weakref-finalized.

Scotty.over_budget(max_panes) -> bool
    # len(list_sessions()) > max_panes  (strict: at-budget is not over).
```

### Recovery precedence (what `repair` decides)

Checked in this order — first match wins:

1. **unregistered** — no spec to repair from -> `ValueError`.
2. **alive** — already in `list_sessions()` -> no-op, return `name`.
3. **dead** — registered and missing -> `session.spawn(spec...)`, `restarts += 1`, return `name`.

The Supervisor applies the same logic per poll, additionally skipping any pane whose `restarts >= restarts_max` so an unspawnable `cmd` can't crash-loop forever.

## Status

Pattern documented and **implemented** 2026-06-13 as M9. Composes on M6 (mesh), M7 (Spock), and the frozen core (M1–M5); changes no existing signature.
