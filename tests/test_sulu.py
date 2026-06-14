"""Acceptance tests: Sulu helmsman / dispatcher layer.

Sulu takes a backlog of commands and auto-assigns each to an IDLE pane (found
via Spock), runs it framed with a done-marker (via mesh.send_with_done), and
collects the replies. When there are more commands than free panes it queues
the overflow. Tests run against a real tmux server; the autouse conftest
fixture handles cleanup. Commands are fast/deterministic shell echos.
"""

import time

from agent_pty import Pty
from agent_pty.sulu import Sulu, dispatch
from tests.conftest import TEST_SHELL


def _spawn_idle(name: str) -> None:
    """Spawn a quiescent bash shell and wait for its prompt to settle."""
    Pty.spawn(name, cmd=TEST_SHELL)
    Pty.wait_for(name, "$", timeout=3.0)
    # Give the prompt a beat so Spock's settle-window double-sample sees no
    # change and reports the pane idle.
    time.sleep(0.3)


# ---------- 1. two commands, two idle panes ----------


def test_dispatch_two_commands_two_panes():
    _spawn_idle("su-a")
    _spawn_idle("su-b")
    results = dispatch(
        ["echo AAA", "echo BBB"],
        names=["su-a", "su-b"],
        timeout=20.0,
    )
    assert set(results) == {"echo AAA", "echo BBB"}
    assert "AAA" in results["echo AAA"], results
    assert "BBB" in results["echo BBB"], results


# ---------- 2. more commands than panes: queued, both complete ----------


def test_dispatch_more_commands_than_panes_queues():
    _spawn_idle("su-solo")
    results = dispatch(
        ["echo ONE", "echo TWO"],
        names=["su-solo"],
        timeout=30.0,
    )
    assert "ONE" in results["echo ONE"], results
    assert "TWO" in results["echo TWO"], results


# ---------- 3. names=None scans all managed panes ----------


def test_dispatch_names_none_uses_all_managed():
    _spawn_idle("su-all1")
    _spawn_idle("su-all2")
    results = dispatch(
        ["echo XX", "echo YY"],
        timeout=20.0,
    )
    assert "XX" in results["echo XX"], results
    assert "YY" in results["echo YY"], results


# ---------- 4. no idle pane within timeout -> empty reply ----------


def test_dispatch_no_pane_times_out_to_empty():
    # No candidate panes exist at all; the command can never be assigned.
    results = dispatch(
        ["echo NEVER"],
        names=["su-does-not-exist"],
        timeout=1.0,
        poll=0.1,
    )
    assert results == {"echo NEVER": ""}


# ---------- 5. custom done marker is honored ----------


def test_dispatch_custom_done_marker():
    _spawn_idle("su-mark")
    results = dispatch(
        ["echo MARKED"],
        names=["su-mark"],
        done_marker="##FIN##",
        timeout=20.0,
    )
    assert "MARKED" in results["echo MARKED"], results
    # The marker itself must not leak into the returned reply.
    assert "##FIN##" not in results["echo MARKED"]


# ---------- 6. Sulu namespace mirrors the module function ----------


def test_sulu_namespace_dispatch_is_module_function():
    assert Sulu.dispatch is dispatch
