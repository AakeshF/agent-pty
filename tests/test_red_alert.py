"""Acceptance tests: RedAlert escalation layer (M13).

RedAlert watches the fleet and fires a notifier when a human is needed
(deadlock or a dead pane). It is read-only on panes; the notification is the
only side effect. Tests capture notifications with a list-appending callable
as the notifier, and build a deadlock from a real blocked pane (a `read -p`
prompt with no busy pane). The autouse conftest fixture handles cleanup.
"""

import time

from agent_pty import Pty
from agent_pty.red_alert import Alert, Alerter, RedAlert, check, notify, watch
from tests.conftest import TEST_SHELL


def _spawn_blocked(name: str) -> None:
    """Spawn a bash pane and leave it blocked on a y/N prompt."""
    Pty.spawn(name, cmd=TEST_SHELL)
    Pty.wait_for(name, "$", timeout=3.0)
    Pty.send(name, "read -p 'Continue? [y/N] ' x\n")
    # Wait until the prompt is detectable as blocked.
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        a = check([name])
        if a is not None and a.kind == "deadlock":
            return
        time.sleep(0.05)


# ---------- 1. check: deadlock ----------


def test_check_returns_deadlock_alert():
    _spawn_blocked("ra-dl")
    alert = check(["ra-dl"])
    assert alert is not None
    assert alert.kind == "deadlock"
    assert "ra-dl" in alert.names
    assert alert.detail


def test_check_prefers_deadlock_over_death():
    # One blocked pane (deadlock) + one dead/never-spawned name. Deadlock wins.
    _spawn_blocked("ra-pref-blk")
    alert = check(["ra-pref-blk", "ra-never-spawned-xyz"])
    assert alert is not None
    assert alert.kind == "deadlock"


# ---------- 2. check: death ----------


def test_check_returns_death_for_unmanaged_name():
    alert = check(["ra-never-spawned-abc"])
    assert alert is not None
    assert alert.kind == "death"
    assert "ra-never-spawned-abc" in alert.names


def test_check_returns_death_after_kill():
    Pty.spawn("ra-kill", cmd=TEST_SHELL)
    Pty.wait_for("ra-kill", "$", timeout=3.0)
    Pty.kill("ra-kill")
    alert = check(["ra-kill"])
    assert alert is not None
    assert alert.kind == "death"


# ---------- 3. check: healthy fleet -> None ----------


def test_check_returns_none_for_idle_fleet():
    Pty.spawn("ra-idle", cmd=TEST_SHELL)
    Pty.wait_for("ra-idle", "$", timeout=3.0)
    time.sleep(0.3)
    assert check(["ra-idle"]) is None


# ---------- 4. notify: custom callable appends ----------


def test_notify_calls_custom_notifier():
    captured: list[str] = []
    notify("hello human", notifier=captured.append)
    assert captured == ["hello human"]


# ---------- 5. watch: fires within ~1.5s on a deadlock ----------


def test_watch_fires_on_deadlock():
    captured: list[str] = []
    _spawn_blocked("ra-watch")
    al = watch(["ra-watch"], notifier=captured.append, poll=0.2)
    try:
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and not captured:
            time.sleep(0.05)
        assert captured, "watch() did not fire a notification within 1.5s"
    finally:
        al.close()


def test_watch_dedups_consecutive_identical_alerts():
    captured: list[str] = []
    _spawn_blocked("ra-dedup")
    al = watch(["ra-dedup"], notifier=captured.append, poll=0.15)
    try:
        # Let it poll several times; the pane stays blocked the whole time.
        time.sleep(1.0)
        assert len(captured) == 1, f"expected one deduped alert, got {captured}"
    finally:
        al.close()


# ---------- 6. Alerter is a context manager and closes cleanly ----------


def test_alerter_context_manager():
    captured: list[str] = []
    _spawn_blocked("ra-ctx")
    with watch(["ra-ctx"], notifier=captured.append, poll=0.2) as al:
        assert isinstance(al, Alerter)
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and not captured:
            time.sleep(0.05)
        assert captured


# ---------- 7. read-only invariant: RedAlert never types into panes ----------


def test_red_alert_does_not_type_into_panes():
    Pty.spawn("ra-ro", cmd=TEST_SHELL)
    Pty.wait_for("ra-ro", "$", timeout=3.0)
    time.sleep(0.3)
    before = Pty.snapshot("ra-ro")
    captured: list[str] = []
    check(["ra-ro"])
    with watch(["ra-ro"], notifier=captured.append, poll=0.15):
        time.sleep(0.4)
    after = Pty.snapshot("ra-ro")
    assert after == before, "RedAlert mutated a pane (it must be read-only)"


# ---------- 8. namespace parity ----------


def test_namespace_exposes_api():
    assert RedAlert.Alert is Alert
    assert RedAlert.check is check
    assert RedAlert.notify is notify
    assert RedAlert.watch is watch


def test_alert_dataclass_shape():
    a = Alert("deadlock", "stalled", ["x", "y"])
    assert a.kind == "deadlock"
    assert a.detail == "stalled"
    assert a.names == ["x", "y"]
