"""Acceptance tests: Holodeck sandboxed-worktree layer.

Holodeck is an ACTUATOR: each simulation is an isolated git worktree with its
own PTY pane. These tests run against a real tmux server and a real git repo.
They REQUIRE git (skipped otherwise) and ALWAYS tear down their worktrees, even
on failure, so the host repo is never left with stray worktrees.
"""

import os
import shutil
import subprocess

import pytest

from agent_pty import Pty
from agent_pty.holodeck import Holodeck, Simulation
from tests.conftest import TEST_SHELL

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not available"
)


def _git(base, *args):
    return subprocess.run(
        ["git", "-C", base, *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.fixture
def repo(tmp_path):
    """An ad-hoc git repo with one commit, isolated under tmp_path."""
    base = tmp_path / "repo"
    base.mkdir()
    base = str(base)
    _git(base, "init", "-q")
    _git(base, "config", "user.email", "t@t.t")
    _git(base, "config", "user.name", "t")
    (tmp_path / "repo" / "README").write_text("hello\n")
    _git(base, "add", "README")
    _git(base, "commit", "-q", "-m", "init")
    return base


@pytest.fixture
def reaper():
    """Track created sims and force-destroy any survivors after the test."""
    created: list[str] = []
    yield created
    for name in list(created):
        try:
            Holodeck.destroy(name)
        except Exception:
            pass


# ---------- 1. create: worktree + pane exist ----------


def test_create_makes_worktree_and_pane(repo, reaper):
    Holodeck.create("h1", base=repo, cmd=TEST_SHELL)
    reaper.append("h1")

    sim = next(s for s in _sims_via_list() if s == "h1")
    assert sim == "h1"
    # The worktree directory exists on disk.
    wt = _worktree_path(repo, "h1")
    assert wt is not None
    assert os.path.isdir(wt)
    # git knows about the worktree.
    listing = _git(repo, "worktree", "list")
    assert wt in listing
    # The pane is a managed agent-pty session.
    assert "h1" in Pty.list()


def test_create_returns_name(repo, reaper):
    name = Holodeck.create("h-ret", base=repo, cmd=TEST_SHELL)
    reaper.append("h-ret")
    assert name == "h-ret"


# ---------- 2. create: pane runs inside the worktree ----------


def test_pane_cwd_is_the_worktree(repo, reaper):
    Holodeck.create("h-cwd", base=repo, cmd=TEST_SHELL)
    reaper.append("h-cwd")
    Pty.wait_for("h-cwd", "$", timeout=3.0)
    wt = _worktree_path(repo, "h-cwd")
    Pty.send("h-cwd", "pwd\n")
    snap = Pty.wait_for("h-cwd", os.path.basename(wt), timeout=3.0)
    # The worktree dir basename appears in the pwd output.
    assert os.path.basename(wt) in snap


# ---------- 3. create with a branch ----------


def test_create_with_branch_makes_branch(repo, reaper):
    Holodeck.create("h-br", base=repo, branch="sim-feature", cmd=TEST_SHELL)
    reaper.append("h-br")
    branches = _git(repo, "branch", "--list", "sim-feature")
    assert "sim-feature" in branches


# ---------- 4. destroy: pane gone + worktree removed ----------


def test_destroy_removes_pane_and_worktree(repo, reaper):
    Holodeck.create("h-del", base=repo, cmd=TEST_SHELL)
    reaper.append("h-del")
    wt = _worktree_path(repo, "h-del")
    assert os.path.isdir(wt)
    assert "h-del" in Pty.list()

    Holodeck.destroy("h-del")
    reaper.remove("h-del")

    assert "h-del" not in Pty.list()
    assert not os.path.isdir(wt)
    assert wt not in _git(repo, "worktree", "list")


# ---------- 5. list: active simulations ----------


def test_list_tracks_active_simulations(repo, reaper):
    before = set(Holodeck.list())
    Holodeck.create("h-l1", base=repo, cmd=TEST_SHELL)
    reaper.append("h-l1")
    Holodeck.create("h-l2", base=repo, cmd=TEST_SHELL)
    reaper.append("h-l2")

    names = set(Holodeck.list())
    assert {"h-l1", "h-l2"} <= names

    Holodeck.destroy("h-l1")
    reaper.remove("h-l1")
    assert "h-l1" not in Holodeck.list()
    assert "h-l2" in Holodeck.list()


# ---------- 6. create on a non-repo raises ----------


def test_create_outside_repo_raises(tmp_path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    with pytest.raises(RuntimeError):
        Holodeck.create("h-nope", base=str(not_a_repo), cmd=TEST_SHELL)
    # Nothing should have been registered.
    assert "h-nope" not in Holodeck.list()


# ---------- 7. isolation: sims have distinct worktrees ----------


def test_simulations_are_isolated(repo, reaper):
    Holodeck.create("h-i1", base=repo, cmd=TEST_SHELL)
    reaper.append("h-i1")
    Holodeck.create("h-i2", base=repo, cmd=TEST_SHELL)
    reaper.append("h-i2")
    w1 = _worktree_path(repo, "h-i1")
    w2 = _worktree_path(repo, "h-i2")
    assert w1 != w2
    assert os.path.isdir(w1) and os.path.isdir(w2)


# ---------- helpers ----------


def _sims_via_list():
    return Holodeck.list()


def _worktree_path(base, name):
    """Resolve a sim's worktree path. Prefers the registry; falls back to git."""
    from agent_pty import holodeck

    sim = holodeck._registry.get(name)
    if isinstance(sim, Simulation):
        return sim.worktree
    return None
