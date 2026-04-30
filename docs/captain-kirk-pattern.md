# Captain Kirk pattern

Where agent-pty is going next: orchestrator-driven multi-agent terminals, built as an opt-in module on top of the core API.

## The shape

One agent — the captain — drives N other agents, each running in its own tmux pane via agent-pty. The sub-agents are persistent (state survives across captain turns), observable (the captain reads their screens), steerable (the captain can interrupt and redirect mid-task), and shareable (the human can `tmux attach` to any of them).

agent-pty was built for vim and gdb. Its highest-leverage use case turns out to be a TUI that happens to be another agent. We treat that as part of the project's identity, not a downstream concern.

## How it relates to existing primitives

|  | Mid-flight steering | Persistent context | Push events | User can intervene | Heterogeneous configs |
|---|---|---|---|---|---|
| Stateless `Bash` | — | — | — | n/a | n/a |
| One-shot subagent (`Agent` tool) | no | no — one prompt, one summary | n/a | no | per-spawn |
| agent-pty + mesh (this) | yes | yes | yes | yes (`tmux attach`) | yes |

The honest framing: a one-shot subagent tool covers ~80% of "spawn a sub-agent" use cases more cleanly. Mesh is for the remaining 20% where the work is **iterative + observable + steerable**, not just parallelizable.

## Where it earns its place

- **Worktree swarm** — N sub-agents, one per `git worktree`, captain coordinates non-overlapping changes and synthesizes a merge plan.
- **Adversarial review** — implementer pane + independent reviewer pane (no shared context). Captain mediates. Stronger than self-review.
- **A/B implementations** — same prompt, two models or two strategies. Captain diffs and picks or merges.
- **Live ops console** — log-tail pane + `kubectl` pane + `psql` pane. Captain is SRE; panes are hands.
- **Long-horizon babysitting** — slow build/migrate in pane 1, fixer in pane 2, log-scraper in pane 3. Captain dispatches as state evolves.

## The honest costs

- **Token economics compound.** Every sub-agent has its own context window; every screen the captain reads costs tokens. A 6-agent swarm can burn 10× faster than one agent serial. Often the serial version finishes before the parallel version pays off setup.
- **Screen-scraping is brittle** vs. receiving a structured string. The sub-agent's reply is mixed with status bars, partial redraws, ANSI artifacts.
- **Permission deadlocks.** Sub-agent hits a permission prompt and blocks silently. Captain doesn't notice until next operation.
- **No structured handshake** unless we provide one. Sub-agents return freeform; the captain has to parse.
- **Orchestrator becomes the bottleneck.** Captain's supervision tokens can exceed the cost of doing the work directly.

These are real. The mesh module exists to bring them down to manageable, not to pretend they don't exist.

## What the core lacks for this to be practical

The core (`spawn/send/snapshot/wait_for/list/kill`) is solid as a transport. The protocol layer above the transport doesn't exist yet. Ranked by leverage:

1. **Done-detection / message framing.** Single biggest gap. `wait_for` needs a literal substring, but a sub-agent's reply ends with what? Need either a sentinel convention (prompted) or structured output from the sub-agent itself.
2. **Push events instead of polling.** `wait_for` is synchronous on one session. Mesh needs subscriptions: "notify me when pane 3 emits X while I keep driving pane 1."
3. **Blocked-on-prompt signal.** First-class detection of "this pane is awaiting input" — not inferred by polling.
4. **Incremental snapshots.** Return only what's appended since a marker, not the whole screen. Saves token cost when the captain is peeking often.
5. **Cross-pane data movement that bypasses the captain.** Today routing pane-to-pane round-trips through the captain's context, expensive for large artifacts.
6. **Lifecycle notifications.** Today a session crash is detected only when the next operation errors.

Items 1 and 2 are load-bearing. The rest are nice-to-haves.

## Module shape — `agent_pty.mesh`

Lives alongside the core API in the same package. Opt-in; core users never import it. New module file `agent_pty/mesh.py`. Parallel MCP tools under the `mesh_*` namespace, exposed by the same `agent-pty-mcp` server (no second binary to install).

Sketched API (subject to refinement during M6 design):

```python
Mesh.send_with_done(name, text, done_marker="<<END>>", timeout=60) -> str
Mesh.subscribe(name, pattern) -> AsyncIterator[str]
Mesh.detect_blocked(name) -> str | None       # returns hint about what's blocking, or None
Mesh.snapshot_since(name, marker) -> str
Mesh.pipe(from_name, to_name, region="last_reply") -> None
Mesh.lifecycle_events() -> AsyncIterator[Event]
```

Sentinel framing in v1: callers prompt sub-agents to terminate replies with a marker; mesh handles the matching. If `claude` (or other agent CLIs) later ship a structured interactive output mode, `send_with_done` switches to consuming that without breaking callers.

## Open questions

- **Async vs. blocking API.** The core is blocking. Mesh inherently wants async (subscriptions, lifecycle events). Decide whether to expose a blocking shim for parity with the core or commit to `asyncio` for the mesh module.
- **Recursion depth.** A sub-agent can spawn its own mesh. How does the captain bound depth? Probably an env var or config setting.
- **Permission auto-approval.** Mesh detects a sub-agent is blocked on a permission prompt. Does it forward to the captain (slow, costs tokens) or auto-approve from a policy file (faster, security-sensitive)? Both, behind a flag.
- **What does `claude` need from us, if anything.** Sentinel convention is buildable today. A first-class structured-output mode would be cleaner. Not a blocker for v1.

## Status

Pattern documented and **implemented** 2026-04-29 as M6 in the [build plan](build-plan.md). All 12 acceptance tests pass against a real tmux server. Core (M1–M5) is unchanged.

Implementation choices worth knowing about:
- Subscriptions are backed by per-subscription background threads polling `capture-pane` at 25ms — not `tmux pipe-pane` (yet). Push-style API, polling underneath. If/when polling cost becomes a hotspot, the underlying mechanism swaps to `pipe-pane` without changing the API.
- Blocked-on-prompt detection is regex-based over the bottom rows of the rendered screen. Best-effort signal, not a guarantee.
- `pipe` is fire-and-forget keystroke injection. Newlines on the source become Enter presses on the destination — sanitize content if you don't want random execution.
- All user-supplied text in mesh APIs round-trips literal `<` correctly. The named-key parser (`<C-c>`, `<Enter>`, etc.) is bypassed inside mesh — use `Pty.send` directly for keystrokes.
