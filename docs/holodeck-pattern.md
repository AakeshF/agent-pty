# Holodeck pattern

A holodeck is a sandboxed stage: an isolated git worktree with its own PTY pane. It is the concrete machinery behind the **worktree swarm** use case named first in the [Captain Kirk pattern](captain-kirk-pattern.md#where-it-earns-its-place) — N sub-agents, one per `git worktree`, each making non-overlapping changes against the same repo while the captain coordinates and synthesizes a merge plan.

## The shape

Mesh gives the captain panes to drive; Spock gives the captain a read-only instrument panel. Neither answers the question *"where does each sub-agent do its work without trampling the others?"* If you spawn six agents in six panes that all share one working tree, they fight over the same files — a swarm that corrupts its own state. Holodeck answers it: each simulation gets a fresh `git worktree` (its own checkout, its own optional branch, its own directory) and a pane spawned *inside* that directory. Agents in different holodecks edit, build, and commit in genuine isolation; the captain merges afterward.

agent-pty's core is a transport; mesh is the commander's toolkit; Spock is the instrument panel; Holodeck is the **stage crew** — it builds and strikes the isolated set. It is opt-in and composes on the existing primitives (`session.spawn`/`session.kill`) plus plain `git`; it changes no core, mesh, or Spock signature.

## How it relates to spawning panes directly

|  | Filesystem isolation | Independent branch | One-call teardown | Swarm-safe |
|---|---|---|---|---|
| `Pty.spawn(cwd=repo)` × N | no — shared working tree | no | no (pane only; tree dirty) | no — agents collide |
| `Holodeck.create` × N (this) | yes — one worktree each | optional (`branch=`) | yes (`destroy` = pane + worktree) | yes |

The honest framing: if your sub-agents only *read* the repo, or you run them strictly one at a time, you don't need this — just `Pty.spawn(cwd=...)`. Holodeck earns its place the moment two or more agents **write** to the same repo concurrently, which is exactly the worktree-swarm use case and exactly where naive parallelism corrupts state.

## Where it earns its place

- **Worktree swarm.** The headline use case. Fan out one task into N isolated implementations, each on its own branch, then have the captain diff and merge. This is the safe substrate the Kirk doc assumed but the core never provided.
- **A/B implementations.** Two holodecks, same prompt, two strategies. Because each is a real branch, the captain can `git diff` the two trees directly instead of scraping screens.
- **Throwaway experiments.** A simulation is cheap to create and cheap to strike. `create` then `destroy` leaves the host repo exactly as it was — no stray branches checked out in the main tree, no half-applied edits.
- **Parallel CI-style runs.** Build/test the same commit under different configs in parallel without N full clones; worktrees share the object store.

## The honest costs

- **It is an ACTUATOR, and it runs `git`.** `create` invokes `git worktree add` and `destroy` invokes `git worktree remove --force`. A repo in an unusual state (detached operations in progress, locked index) can make these fail; the error surfaces, it isn't swallowed.
- **`--force` removal discards uncommitted work.** `destroy` removes the worktree forcibly. Anything the agent edited but did not commit in that worktree is gone. If the swarm's output matters, the captain must commit (or copy out) *before* destroying — this layer will not preserve it for you.
- **Cleanup is best-effort, not transactional.** If a worktree directory is deleted out from under git, `destroy` falls back to `shutil.rmtree` + `git worktree prune`. That covers the common cases, but a sufficiently mangled repo can still need a manual `git worktree prune`. Worktrees share the object store, so a leaked worktree dir is cheap, but it is still cruft.
- **Shared object store, shared limits.** Worktrees are not clones; they share `.git/objects`. That makes them cheap, but a worktree cannot check out a branch already checked out in another worktree — the swarm must use distinct branches (or detached heads, the default).
- **Temp-dir sprawl on crash.** Worktrees live in `tempfile.mkdtemp` dirs. A `create` that fails after the worktree is added rolls itself back, but a hard process kill between `create` and `destroy` leaves a temp worktree the OS temp-cleaner (and `git worktree prune`) will eventually reclaim — not instantly.

These are real. Holodeck exists to make a write-concurrent swarm *safe to run*, not to pretend git worktrees are free or that forced teardown is reversible.

## Module shape — `agent_pty.holodeck`

Lives alongside the core, mesh, and Spock in the same package. New module file `agent_pty/holodeck.py`. A `Holodeck` namespace class mirrors `Pty`, `Mesh`, and `Spock` (`staticmethod` wrappers over module-level functions). A module-level registry maps `name -> Simulation(name, worktree, base, branch)`, so `create`/`destroy`/`list` share one view. Parallel MCP tools under the `holodeck_*` namespace, exposed by the same `agent-pty-mcp` server.

It composes on read-only-plus-spawn core primitives — `session.spawn`, `session.kill`, `session.SessionNotFoundError` — and shells out to `git`. It deliberately does **not** read or steer pane contents: driving the agent inside a simulation is mesh's job; Holodeck only builds and strikes the isolated stage.

## Public API

```python
@dataclass
class Simulation:
    name: str
    worktree: str        # absolute path of the git worktree backing this pane
    base: str            # repo directory the worktree branched off (used for cleanup)
    branch: str | None   # branch created with -b, or None for a detached worktree

Holodeck.create(name, base=None, branch=None, cmd=None, cols=80, rows=24) -> str
    # base: repo dir (default cwd); must be a git repo or RuntimeError.
    # Creates a fresh temp worktree (prefix "agent-pty-holo-") via
    #   git -C <base> worktree add [-b <branch> | --detach] <worktree_dir>,
    # then session.spawn(name, cmd=cmd, cwd=worktree_dir, cols, rows).
    # Records the path; returns name. Rolls back the worktree if spawn fails.

Holodeck.destroy(name) -> None
    # Kills the pane (ignoring an already-dead session), then
    #   git worktree remove --force <path>, then drops the registry entry.
    # Best-effort fallback to rmtree + worktree prune if git can't remove it.

Holodeck.list() -> list[str]
    # Active simulation names, sorted.
```

### Lifecycle (deterministic)

1. **create** — validate `base` is a git repo → `mkdtemp` → `git worktree add` → `session.spawn` in that dir → register. A failed spawn force-removes the worktree before re-raising, so a failure leaks nothing.
2. **destroy** — `session.kill` (swallowing `SessionNotFoundError`) → `git worktree remove --force` → deregister. Removal falls back to `rmtree` + `prune` if git refuses.
3. **list** — the sorted keys of the registry.

## Status

Pattern documented and **implemented 2026-06-13 as M14**. Composes on M6 (mesh) and the frozen core (M1–M5); changes no existing signature.
