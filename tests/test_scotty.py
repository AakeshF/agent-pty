"""Acceptance tests: Scotty resilience + budget layer (M9).

Scotty is an ACTUATOR: it records spawn specs and respawns crashed panes
(repair / Supervisor), and answers the simplest resource question
(over_budget). Tests run against a real tmux server; the autouse conftest
fixture handles cleanup. External crashes are simulated with a raw
`tmux kill-session` so we exercise the "tmux lost it, but the registry didn't"
recovery path rather than a graceful Pty.kill.
"""

import shutil
import subprocess
import time

import pytest

from agent_pty import Pty
from agent_pty.scotty import Scotty, Spec, Supervisor
from agent_pty.session import PREFIX
from tests.conftest import TEST_SHELL

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="tmux not installed"
)


def _external_kill(name: str) -> None:
    """Kill a pane the way a real crash would — outside Scotty's knowledge."""
    subprocess.run(
        ["tmux", "kill-session", "-t", f"{PREFIX}{name}"],
        stderr=subprocess.DEVNULL,
    )


def _spawn_supervised(name: str) -> None:
    """Register a spec, spawn the pane, and wait for its prompt to settle."""
    Scotty.register(name, cmd=TEST_SHELL)
    Pty.spawn(name, cmd=TEST_SHELL)
    Pty.wait_for(name, "$", timeout=3.0)


def _wait_until(predicate, deadline_s: float = 2.0, poll_s: float = 0.05) -> bool:
    """Poll `predicate` until it is truthy or the deadline elapses."""
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll_s)
    return predicate()


# ---------- 1. register / status / forget ----------


def test_register_records_spec_in_status():
    Scotty.register("sc-reg", cmd=TEST_SHELL, cwd="/tmp", cols=100, rows=40)
    by_name = {s.name: s for s in Scotty.status()}
    assert "sc-reg" in by_name
    spec = by_name["sc-reg"]
    assert isinstance(spec, Spec)
    assert spec.cmd == TEST_SHELL
    assert spec.cwd == "/tmp"
    assert spec.cols == 100
    assert spec.rows == 40
    assert spec.restarts == 0


def test_status_returns_independent_copies():
    Scotty.register("sc-copy", cmd=TEST_SHELL)
    snap = {s.name: s for s in Scotty.status()}["sc-copy"]
    snap.restarts = 999  # mutating the copy must not touch the registry
    fresh = {s.name: s for s in Scotty.status()}["sc-copy"]
    assert fresh.restarts == 0


def test_register_replaces_and_resets_restarts():
    Scotty.register("sc-replace", cmd=TEST_SHELL)
    Pty.spawn("sc-replace", cmd=TEST_SHELL)
    Pty.wait_for("sc-replace", "$", timeout=3.0)
    _external_kill("sc-replace")
    assert _wait_until(lambda: "sc-replace" not in Pty.list())
    Scotty.repair("sc-replace")
    assert {s.name: s for s in Scotty.status()}["sc-replace"].restarts == 1
    # Re-registering is a fresh contract -> counter resets.
    Scotty.register("sc-replace", cmd=TEST_SHELL)
    assert {s.name: s for s in Scotty.status()}["sc-replace"].restarts == 0


def test_forget_drops_spec():
    Scotty.register("sc-forget", cmd=TEST_SHELL)
    assert "sc-forget" in {s.name for s in Scotty.status()}
    Scotty.forget("sc-forget")
    assert "sc-forget" not in {s.name for s in Scotty.status()}
    # forget of an unknown name is a no-op, not an error.
    Scotty.forget("sc-never-registered")


# ---------- 2. repair: respawns a dead registered pane ----------


def test_repair_respawns_killed_pane():
    _spawn_supervised("sc-rep")
    assert "sc-rep" in Pty.list()
    _external_kill("sc-rep")
    assert _wait_until(lambda: "sc-rep" not in Pty.list()), "kill did not take"

    returned = Scotty.repair("sc-rep")
    assert returned == "sc-rep"
    assert _wait_until(lambda: "sc-rep" in Pty.list()), "repair did not respawn"
    assert {s.name: s for s in Scotty.status()}["sc-rep"].restarts == 1


def test_repair_alive_pane_is_noop():
    _spawn_supervised("sc-alive")
    returned = Scotty.repair("sc-alive")
    assert returned == "sc-alive"
    # Still exactly one pane, restart counter untouched.
    assert "sc-alive" in Pty.list()
    assert {s.name: s for s in Scotty.status()}["sc-alive"].restarts == 0


def test_repair_unregistered_raises():
    with pytest.raises(ValueError):
        Scotty.repair("sc-not-registered-xyz")


# ---------- 3. Supervisor: auto-repair on external crash ----------


def test_supervisor_auto_repairs_within_window():
    _spawn_supervised("sc-sup")
    sup = Scotty.supervise(restarts_max=3, poll=0.2)
    try:
        _external_kill("sc-sup")
        assert _wait_until(lambda: "sc-sup" not in Pty.list(), deadline_s=1.0)
        # Supervisor should bring it back automatically.
        assert _wait_until(
            lambda: "sc-sup" in Pty.list(), deadline_s=1.5
        ), "supervisor did not respawn the pane in time"
    finally:
        sup.close()


def test_supervisor_respects_restart_budget():
    # A spec whose cmd exits immediately can never stay alive; the budget caps
    # the respawn loop so the supervisor doesn't churn forever.
    Scotty.register("sc-budget", cmd="bash -c 'exit 0'")
    sup = Scotty.supervise(restarts_max=2, poll=0.15)
    try:
        # Give the supervisor several poll cycles to exhaust the budget.
        _wait_until(
            lambda: {s.name: s for s in Scotty.status()}["sc-budget"].restarts >= 2,
            deadline_s=2.0,
        )
        time.sleep(0.5)  # extra cycles to confirm it stops at the cap
        restarts = {s.name: s for s in Scotty.status()}["sc-budget"].restarts
        assert restarts <= 2, f"restart budget exceeded: {restarts}"
    finally:
        sup.close()


def test_supervisor_context_manager_stops_thread():
    with Scotty.supervise(poll=0.1) as sup:
        assert isinstance(sup, Supervisor)
        assert sup._thread.is_alive()
    # After the context exits the thread must be stopped.
    assert _wait_until(lambda: not sup._thread.is_alive(), deadline_s=1.0)


# ---------- 4. over_budget ----------


def test_over_budget_basic():
    # Start clean (autouse fixture killed managed sessions).
    assert isinstance(Scotty.over_budget(0), bool)
    _spawn_supervised("sc-b1")
    _spawn_supervised("sc-b2")
    n = len(Pty.list())
    assert n >= 2
    assert Scotty.over_budget(n) is False  # at-budget is not over (strict >)
    assert Scotty.over_budget(n - 1) is True
    assert Scotty.over_budget(n + 10) is False
