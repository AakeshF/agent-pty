"""Holodeck: sandboxed panes backed by git worktrees.

The "worktree swarm" use case named in the Captain Kirk doc made real. Each
simulation is an isolated git worktree with its own PTY pane, so N agents can
make non-overlapping changes against the same repo without trampling each
other's working tree. The captain coordinates; each holodeck is a hermetic
sandbox that can be created, run in, and torn down cleanly.

ACTUATOR: this module runs `git` and spawns/kills panes. It does not read or
steer pane contents — driving the agent inside a simulation is mesh's job; this
module only provides the isolated stage.

Best-effort cleanup: `destroy` kills the pane then removes the worktree with
`--force`. A worktree whose directory was deleted out from under git, or a repo
in a weird state, can leave `git worktree prune`-able cruft behind; that's a git
limitation, not something this layer hides.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from agent_pty import session

_PREFIX = "agent-pty-holo-"


@dataclass
class Simulation:
    """A live holodeck: a session name bound to its isolated worktree path."""

    name: str
    worktree: str  # absolute path of the git worktree backing this pane
    base: str  # repo directory the worktree branched off (used for cleanup)
    branch: str | None  # branch created with -b, or None for a detached worktree


# name -> Simulation. Module-level so create/destroy/list share one view.
_registry: dict[str, Simulation] = {}


def _git(base: str, *args: str) -> str:
    """Run `git -C base <args>`, returning stdout; raise on failure."""
    result = subprocess.run(
        ["git", "-C", base, *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {base!r}: {result.stderr.strip()}"
        )
    return result.stdout


def _is_git_repo(base: str) -> bool:
    result = subprocess.run(
        ["git", "-C", base, "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def create(
    name: str,
    base: str | None = None,
    branch: str | None = None,
    cmd: str | None = None,
    cols: int = 80,
    rows: int = 24,
) -> str:
    """Create an isolated git-worktree sandbox and spawn a pane inside it.

    `base` is the repo directory the worktree branches off (default: cwd); it
    must be a git repo or this raises RuntimeError. A fresh temp directory
    (prefix "agent-pty-holo-") is created and `git worktree add` populates it:
    `-b <branch>` to start a new branch, or `--detach` when no branch is given.
    A pane named `name` is then spawned there via `session.spawn`. The worktree
    path is recorded in the module registry; `name` is returned.

    On any failure after the worktree is added (e.g. the pane fails to spawn),
    the worktree is removed again so we don't leak a half-built sandbox.
    """
    base = base or "."
    if not _is_git_repo(base):
        raise RuntimeError(f"base {base!r} is not a git repository")

    worktree = tempfile.mkdtemp(prefix=_PREFIX)
    add_args = ["worktree", "add"]
    if branch:
        add_args += ["-b", branch]
    else:
        add_args.append("--detach")
    add_args.append(worktree)
    try:
        _git(base, *add_args)
    except RuntimeError:
        # `git worktree add` failed (dirty path, branch exists, ...): the temp
        # dir mkdtemp created is now an orphan with no worktree — drop it.
        shutil.rmtree(worktree, ignore_errors=True)
        raise

    try:
        session.spawn(name, cmd=cmd, cwd=worktree, cols=cols, rows=rows)
    except Exception:
        # Roll back the worktree so a failed spawn doesn't leak it.
        _force_remove_worktree(base, worktree)
        raise

    _registry[name] = Simulation(
        name=name, worktree=worktree, base=base, branch=branch
    )
    return name


def _force_remove_worktree(base: str, worktree: str) -> None:
    """Best-effort `git worktree remove --force`, falling back to prune.

    If git can't remove it (directory already gone, etc.) we prune the
    administrative entry and drop the tree directly so nothing leaks.
    """
    try:
        _git(base, "worktree", "remove", "--force", worktree)
    except RuntimeError:
        shutil.rmtree(worktree, ignore_errors=True)
        try:
            _git(base, "worktree", "prune")
        except RuntimeError:
            pass


def destroy(name: str) -> None:
    """Tear down a simulation: kill the pane, remove the worktree, deregister.

    The pane is killed first (ignoring a session that already died), then the
    worktree is removed with `--force` and the registry entry dropped. Raises
    KeyError if `name` was never created by this module.
    """
    sim = _registry.pop(name)
    try:
        session.kill(name)
    except session.SessionNotFoundError:
        pass
    _force_remove_worktree(sim.base, sim.worktree)


def list() -> list[str]:  # noqa: A001 - intentional Pty/Mesh-style namespace verb
    """Return the names of active simulations, sorted."""
    return sorted(_registry)


class Holodeck:
    """Public namespace for the holodeck API, parallel to Pty, Mesh and Spock."""

    create = staticmethod(create)
    destroy = staticmethod(destroy)
    list = staticmethod(list)
