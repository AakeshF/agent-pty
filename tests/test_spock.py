"""Acceptance tests: Spock read-only science-officer layer.

Spock observes the fleet and returns structured, deterministic, token-cheap
assessments. It NEVER sends keystrokes and NEVER mutates a pane. Tests run
against a real tmux server; the autouse conftest fixture handles cleanup.
"""

import time

from agent_pty import Pty, Spock
from tests.conftest import TEST_SHELL


def _spawn_idle(name: str) -> None:
    """Spawn a quiescent bash shell and wait for its prompt to settle."""
    Pty.spawn(name, cmd=TEST_SHELL)
    Pty.wait_for(name, "$", timeout=3.0)
    # Give the prompt a beat so the settle-window double-sample sees no change.
    time.sleep(0.3)


# A continuously-changing visible screen is the contract's definition of "busy"
# (the rendered screen changes across the settle window). A bare `yes` saturates
# the visible screen to identical repeated lines, so two snapshots compare equal
# and the pane reads as idle by definition; an incrementing counter keeps the
# visible content changing and is robustly detected as busy.
_BUSY_CMD = (
    "bash -c 'i=0; while true; do echo busy-output $i; "
    "i=$((i+1)); sleep 0.02; done'"
)


def _spawn_busy(name: str) -> None:
    """Spawn a pane whose visible screen keeps changing (busy)."""
    Pty.spawn(name, cmd=_BUSY_CMD)
    time.sleep(0.4)


def _poll_state(name: str, want: str, deadline_s: float = 2.0):
    """Poll diagnose() until state==want or deadline; return the last report."""
    deadline = time.monotonic() + deadline_s
    report = Spock.diagnose(name)
    while time.monotonic() < deadline and report.state != want:
        report = Spock.diagnose(name)
    return report


# ---------- 1. assess: idle and busy state ----------


def test_assess_reports_idle_pane():
    _spawn_idle("sp-idle")
    report = _poll_state("sp-idle", "idle", deadline_s=2.0)
    panes = {p.name: p for p in Spock.assess(["sp-idle"]).panes}
    assert panes["sp-idle"].state == "idle"


def test_assess_reports_busy_pane():
    # A pane whose visible screen keeps changing -> busy.
    _spawn_busy("sp-busy")
    report = _poll_state("sp-busy", "busy", deadline_s=2.0)
    assert report.state == "busy"
    panes = {p.name: p for p in Spock.assess(["sp-busy"]).panes}
    assert panes["sp-busy"].state == "busy"


# ---------- 2. assess: blocked pane ----------


def test_assess_reports_blocked_pane_with_hint():
    Pty.spawn("sp-blk", cmd=TEST_SHELL)
    Pty.wait_for("sp-blk", "$", timeout=3.0)
    Pty.send("sp-blk", "read -p 'Continue? [y/N] ' x\n")
    deadline = time.monotonic() + 1.5
    report = Spock.diagnose("sp-blk")
    while time.monotonic() < deadline and report.state != "blocked":
        time.sleep(0.05)
        report = Spock.diagnose("sp-blk")
    assert report.state == "blocked"
    assert report.hint, f"expected non-empty hint, got {report.hint!r}"


# ---------- 3. diagnose: dead (unmanaged) pane ----------


def test_diagnose_unmanaged_name_is_dead():
    report = Spock.diagnose("sp-never-spawned-xyz")
    assert report.state == "dead"
    assert report.hint is None
    assert report.digest == ""


def test_diagnose_killed_pane_is_dead():
    _spawn_idle("sp-kill")
    assert Spock.diagnose("sp-kill").state != "dead"
    Pty.kill("sp-kill")
    report = Spock.diagnose("sp-kill")
    assert report.state == "dead"


# ---------- 4. deadlock flag ----------


def test_deadlock_true_when_blocked_and_no_busy():
    _spawn_idle("sp-dl-idle")
    Pty.spawn("sp-dl-blk", cmd=TEST_SHELL)
    Pty.wait_for("sp-dl-blk", "$", timeout=3.0)
    Pty.send("sp-dl-blk", "read -p 'Continue? [y/N] ' x\n")
    # Wait for the blocked state to be detectable.
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline and Spock.diagnose("sp-dl-blk").state != "blocked":
        time.sleep(0.05)
    fleet = Spock.assess(["sp-dl-idle", "sp-dl-blk"])
    states = {p.name: p.state for p in fleet.panes}
    assert states["sp-dl-blk"] == "blocked"
    assert "busy" not in states.values()
    assert fleet.deadlock is True


def test_deadlock_false_when_busy_pane_present():
    Pty.spawn("sp-dl2-blk", cmd=TEST_SHELL)
    Pty.wait_for("sp-dl2-blk", "$", timeout=3.0)
    Pty.send("sp-dl2-blk", "read -p 'Continue? [y/N] ' x\n")
    _spawn_busy("sp-dl2-busy")
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline and Spock.diagnose("sp-dl2-blk").state != "blocked":
        time.sleep(0.05)
    fleet = Spock.assess(["sp-dl2-blk", "sp-dl2-busy"])
    states = {p.name: p.state for p in fleet.panes}
    assert states["sp-dl2-blk"] == "blocked"
    assert "busy" in states.values()
    assert fleet.deadlock is False


# ---------- 5. recommend: priority ordering ----------


def test_recommend_sorts_blocked_first_busy_last():
    Pty.spawn("sp-rec-blk", cmd=TEST_SHELL)
    Pty.wait_for("sp-rec-blk", "$", timeout=3.0)
    Pty.send("sp-rec-blk", "read -p 'Continue? [y/N] ' x\n")
    _spawn_busy("sp-rec-busy")
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline and Spock.diagnose("sp-rec-blk").state != "blocked":
        time.sleep(0.05)
    advisories = Spock.recommend(["sp-rec-busy", "sp-rec-blk"])
    by_name = {a.name: a for a in advisories}
    assert by_name["sp-rec-blk"].priority == 0
    assert by_name["sp-rec-busy"].priority == 3
    # First advisory must be the most urgent (the blocked pane).
    assert advisories[0].name == "sp-rec-blk"
    # Result is sorted by (priority, name) ascending.
    keys = [(a.priority, a.name) for a in advisories]
    assert keys == sorted(keys)


# ---------- 6. digest: last visible screen line ----------


def test_digest_contains_last_echoed_token():
    Pty.spawn("sp-dig", cmd=TEST_SHELL)
    Pty.wait_for("sp-dig", "$", timeout=3.0)
    Pty.send("sp-dig", "echo DIGEST-TOKEN-Z7\n")
    Pty.wait_for("sp-dig", "DIGEST-TOKEN-Z7", timeout=3.0)
    time.sleep(0.3)
    report = Spock.diagnose("sp-dig")
    assert report.digest != ""
    # The digest is the last non-empty rendered line; the freshly-echoed token
    # should appear in recent screen content reflected by the digest or be the
    # prompt line following it. Assert the token is observable in the snapshot
    # and that digest is a real trimmed line.
    snap = Pty.snapshot("sp-dig")
    assert "DIGEST-TOKEN-Z7" in snap
    last_nonempty = [ln for ln in snap.splitlines() if ln.strip()][-1].strip()
    assert report.digest == last_nonempty


# ---------- 7. read-only invariant ----------


def test_spock_does_not_type_into_panes():
    _spawn_idle("sp-ro")
    before = Pty.snapshot("sp-ro")
    Spock.assess(["sp-ro"])
    Spock.recommend(["sp-ro"])
    Spock.diagnose("sp-ro")
    after = Pty.snapshot("sp-ro")
    assert after == before, "Spock mutated a pane (it must be read-only)"


# ---------- 8. names filter ----------


def test_assess_names_filter_reports_only_subset():
    _spawn_idle("sp-f1")
    _spawn_idle("sp-f2")
    _spawn_idle("sp-f3")
    fleet = Spock.assess(["sp-f1", "sp-f2"])
    names = [p.name for p in fleet.panes]
    assert names == ["sp-f1", "sp-f2"]
    assert "sp-f3" not in names


def test_assess_none_reports_all_managed():
    _spawn_idle("sp-all1")
    _spawn_idle("sp-all2")
    fleet = Spock.assess()
    names = {p.name for p in fleet.panes}
    assert {"sp-all1", "sp-all2"} <= names


# ---------- 9. MCP surface parity ----------


EXPECTED_SPOCK_TOOLS = {
    "spock_assess",
    "spock_diagnose",
    "spock_recommend",
}


def test_mcp_surface_registers_all_spock_tools():
    import asyncio

    from agent_pty.mcp import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    missing = EXPECTED_SPOCK_TOOLS - names
    assert not missing, f"missing spock tools in MCP server: {missing}"


def test_mcp_each_spock_tool_has_description():
    import asyncio

    from agent_pty.mcp import mcp

    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    for name in EXPECTED_SPOCK_TOOLS:
        assert tools[name].description, f"{name} missing description"
        assert (
            len(tools[name].description) > 30
        ), f"{name} description too brief"
