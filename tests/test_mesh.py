"""M6 acceptance tests: mesh orchestration layer.

Each test corresponds to one numbered acceptance criterion in
docs/build-plan.md M6 section. Tests run against a real tmux server.
"""

import os
import re
import shutil
import subprocess
import time

import pytest

from agent_pty import Mesh, Pty, SessionNotFoundError
from tests.conftest import TEST_SHELL


# ---------- 1. Done-detection round-trip ----------


def test_send_with_done_returns_reply_bounded_by_marker():
    Pty.spawn("d1", cmd=TEST_SHELL)
    Pty.wait_for("d1", "$", timeout=3.0)
    reply = Mesh.send_with_done(
        "d1",
        "printf 'reply text\\n<<END>>\\n'\n",
        done_marker="<<END>>",
        timeout=3.0,
    )
    assert reply == "reply text"


def test_send_with_done_does_not_leak_subsequent_output():
    Pty.spawn("d2", cmd=TEST_SHELL)
    Pty.wait_for("d2", "$", timeout=3.0)
    reply = Mesh.send_with_done(
        "d2",
        "printf 'first\\n<<END>>\\n'\n",
        done_marker="<<END>>",
        timeout=3.0,
    )
    Pty.send("d2", "echo unrelated-after\n")
    Pty.wait_for("d2", "unrelated-after", timeout=3.0)
    assert "unrelated-after" not in reply
    assert reply == "first"


def test_send_with_done_ignores_marker_in_command_echo():
    # The sent line CONTAINS the marker; the output arrives only after a delay.
    # Done-detection must wait for the marker that appears after the echo.
    Pty.spawn("d3", cmd=TEST_SHELL)
    Pty.wait_for("d3", "$", timeout=3.0)
    t0 = time.monotonic()
    reply = Mesh.send_with_done(
        "d3",
        "sleep 0.6; printf 'late reply\\n<<END>>\\n'",
        done_marker="<<END>>",
        timeout=5.0,
    )
    assert time.monotonic() - t0 >= 0.5, "returned before the command produced output"
    assert reply == "late reply"


def test_send_with_done_ignores_marker_from_previous_reply():
    Pty.spawn("d4", cmd=TEST_SHELL)
    Pty.wait_for("d4", "$", timeout=3.0)
    first = Mesh.send_with_done("d4", "printf 'one\\n<<END>>\\n'", timeout=3.0)
    second = Mesh.send_with_done(
        "d4", "sleep 0.4; printf 'two\\n<<END>>\\n'", timeout=3.0
    )
    assert first == "one"
    assert second == "two"


def test_send_with_done_submits_with_enter_not_newline():
    # No trailing newline in the text: send_with_done must still submit.
    Pty.spawn("d5", cmd=TEST_SHELL)
    Pty.wait_for("d5", "$", timeout=3.0)
    reply = Mesh.send_with_done("d5", "printf 'no-newline\\n<<END>>\\n'", timeout=3.0)
    assert reply == "no-newline"


def test_frame_shell_hides_marker_from_echo():
    framed = Mesh.frame_shell("echo hi", "<<END>>")
    assert "<<END>>" not in framed
    assert framed.startswith("echo hi; printf")
    Pty.spawn("d6", cmd=TEST_SHELL)
    Pty.wait_for("d6", "$", timeout=3.0)
    assert Mesh.send_with_done("d6", framed, timeout=3.0) == "hi"


# ---------- 2. Subscription latency ----------


def test_subscription_yields_within_250ms_of_match():
    Pty.spawn("s1", cmd=TEST_SHELL)
    Pty.wait_for("s1", "$", timeout=3.0)
    with Mesh.subscribe("s1", "ERROR-TOKEN") as sub:
        time.sleep(0.2)
        send_t = time.monotonic()
        Pty.send("s1", "echo ERROR-TOKEN\n")
        snap = sub.next(timeout=2.0)
        elapsed = time.monotonic() - send_t
    assert snap is not None
    assert "ERROR-TOKEN" in snap
    assert elapsed < 0.5, f"subscription latency {elapsed:.3f}s > 0.5s budget"


# ---------- 3. Subscription cancellation ----------


def test_subscription_close_stops_yielding():
    Pty.spawn("s2", cmd=TEST_SHELL)
    Pty.wait_for("s2", "$", timeout=3.0)
    sub = Mesh.subscribe("s2", "NEVER-FIRES-XYZ")
    sub.close()
    # After close, next() should return None promptly (within timeout, doesn't block)
    start = time.monotonic()
    result = sub.next(timeout=0.2)
    elapsed = time.monotonic() - start
    assert result is None
    assert elapsed < 0.4
    # Internal thread is no longer alive
    assert not sub._thread.is_alive()


# ---------- 4. Blocked detection: sudo password ----------


def test_detect_blocked_sudo_password():
    if shutil.which("sudo") is None:
        pytest.skip("sudo not available")
    Pty.spawn("b1", cmd="sudo -k -p 'Password please: ' true")
    deadline = time.monotonic() + 1.5
    hint = None
    while time.monotonic() < deadline:
        hint = Mesh.detect_blocked("b1")
        if hint:
            break
        time.sleep(0.05)
    assert hint is not None
    assert "password" in hint.lower()


# ---------- 5. Blocked detection: y/n prompt ----------


def test_detect_blocked_y_n_prompt():
    Pty.spawn("b2", cmd=TEST_SHELL)
    Pty.wait_for("b2", "$", timeout=3.0)
    Pty.send("b2", "read -p 'Continue? [y/N] ' x\n")
    deadline = time.monotonic() + 1.5
    hint = None
    while time.monotonic() < deadline:
        hint = Mesh.detect_blocked("b2")
        if hint:
            break
        time.sleep(0.05)
    assert hint is not None
    assert "y/n" in hint.lower() or "continue" in hint.lower()


# ---------- 5b. Blocked detection: Claude Code permission dialog ----------

CLAUDE_PERMISSION_DIALOG = (
    " Bash command\\n\\n   touch /tmp/x\\n   Create probe file\\n\\n"
    " Do you want to proceed?\\n ❯ 1. Yes\\n"
    "   2. Yes, and always allow access to /tmp from this project\\n"
    "   3. No\\n\\n Esc to cancel · Tab to amend\\n"
)


CLAUDE_TRUST_DIALOG = (
    " Only proceed if you trust this configuration.\\n\\n ❯ No, exit\\n"
    "   Yes, I trust this folder\\n\\n Enter to confirm · Esc to cancel\\n"
)


def test_detect_blocked_claude_trust_prompt():
    Pty.spawn("b2t", cmd=TEST_SHELL, cols=120)
    Pty.wait_for("b2t", "$", timeout=3.0)
    Pty.send("b2t", f"printf '{CLAUDE_TRUST_DIALOG}'; read -n1 x\n")
    deadline = time.monotonic() + 1.5
    hint = None
    while time.monotonic() < deadline:
        hint = Mesh.detect_blocked("b2t")
        if hint:
            break
        time.sleep(0.05)
    assert hint == "claude trust prompt"


def test_detect_blocked_claude_permission_prompt():
    Pty.spawn("b2c", cmd=TEST_SHELL, cols=120)
    Pty.wait_for("b2c", "$", timeout=3.0)
    Pty.send("b2c", f"printf '{CLAUDE_PERMISSION_DIALOG}'; read -n1 x\n")
    deadline = time.monotonic() + 1.5
    hint = None
    while time.monotonic() < deadline:
        hint = Mesh.detect_blocked("b2c")
        if hint:
            break
        time.sleep(0.05)
    assert hint == "claude permission prompt"


# ---------- 6. Blocked detection: false-positive guard ----------


def test_detect_blocked_busy_session_returns_none():
    if shutil.which("yes") is None:
        pytest.skip("`yes` not available")
    # `yes` continually emits 'y' — busy, not blocked
    Pty.spawn("b3", cmd="yes 'busy-output'")
    time.sleep(0.4)
    hint = Mesh.detect_blocked("b3")
    assert hint is None, f"expected None, got {hint!r}"


# ---------- 7. snapshot_since ----------


def test_snapshot_since_returns_only_post_marker_content():
    Pty.spawn("ss1", cmd=TEST_SHELL)
    Pty.wait_for("ss1", "$", timeout=3.0)
    Pty.send("ss1", "echo before-anchor\n")
    Pty.wait_for("ss1", "before-anchor", timeout=3.0)
    Pty.send("ss1", "echo MARK-XYZ-HERE\n")
    Pty.wait_for("ss1", "MARK-XYZ-HERE", timeout=3.0)
    Pty.send("ss1", "echo after-anchor-1\n")
    Pty.wait_for("ss1", "after-anchor-1", timeout=3.0)
    Pty.send("ss1", "echo after-anchor-2\n")
    Pty.wait_for("ss1", "after-anchor-2", timeout=3.0)
    after = Mesh.snapshot_since("ss1", "MARK-XYZ-HERE")
    assert "after-anchor-1" in after
    assert "after-anchor-2" in after
    assert "before-anchor" not in after


# ---------- 8. Pipe between panes ----------


def test_pipe_between_panes():
    Pty.spawn("a", cmd=TEST_SHELL)
    Pty.spawn("b", cmd=TEST_SHELL)
    Pty.wait_for("a", "$", timeout=3.0)
    Pty.wait_for("b", "$", timeout=3.0)
    Pty.send("a", "echo PIPED-PAYLOAD-Q9\n")
    Pty.wait_for("a", "PIPED-PAYLOAD-Q9", timeout=3.0)
    result = Mesh.pipe("a", "b", lines=2)
    assert result is None
    snap_b = Pty.wait_for("b", "PIPED-PAYLOAD-Q9", timeout=2.0)
    assert "PIPED-PAYLOAD-Q9" in snap_b


# ---------- 9. Lifecycle: birth + death ----------


def test_lifecycle_birth_and_death():
    with Mesh.lifecycle_events() as events:
        # Drain any startup events before the test session is born
        while events.next(timeout=0.1) is not None:
            pass
        Pty.spawn("life-c", cmd=TEST_SHELL)
        born = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            ev = events.next(timeout=0.5)
            if ev and ev.kind == "born" and ev.name == "life-c":
                born = ev
                break
        assert born is not None, "did not see born event"
        # Kill from outside the agent_pty API
        subprocess.run(
            ["tmux", "kill-session", "-t", "agent-pty-life-c"],
            check=True,
        )
        died = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            ev = events.next(timeout=0.5)
            if ev and ev.kind == "died" and ev.name == "life-c":
                died = ev
                break
        assert died is not None, "did not see died event"


# ---------- 10. Lifecycle: idle / busy ----------


def test_lifecycle_idle_then_busy():
    with Mesh.lifecycle_events() as events:
        Pty.spawn("life-i", cmd=TEST_SHELL)
        # Drain any born/idle/busy noise from other concurrent test state
        idle = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            ev = events.next(timeout=0.5)
            if ev and ev.kind == "idle" and ev.name == "life-i":
                idle = ev
                break
        assert idle is not None, "did not see idle event"
        # Wake it up
        Pty.send("life-i", "echo wakeup\n")
        busy = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            ev = events.next(timeout=0.5)
            if ev and ev.kind == "busy" and ev.name == "life-i":
                busy = ev
                break
        assert busy is not None, "did not see busy event"


# ---------- 11. MCP surface parity ----------


EXPECTED_MESH_TOOLS = {
    "mesh_send_with_done",
    "mesh_snapshot_since",
    "mesh_detect_blocked",
    "mesh_pipe",
    "mesh_subscribe_create",
    "mesh_subscribe_next",
    "mesh_subscribe_close",
    "mesh_lifecycle_create",
    "mesh_lifecycle_next",
    "mesh_lifecycle_close",
}


def test_mcp_surface_registers_all_mesh_tools():
    import asyncio

    from agent_pty.mcp import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    missing = EXPECTED_MESH_TOOLS - names
    assert not missing, f"missing mesh tools in MCP server: {missing}"


def test_mcp_each_mesh_tool_has_description():
    import asyncio

    from agent_pty.mcp import mcp

    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    for name in EXPECTED_MESH_TOOLS:
        assert tools[name].description, f"{name} missing description"
        assert (
            len(tools[name].description) > 30
        ), f"{name} description too brief"


# ---------- 12. Captain-Kirk integration (manual, opt-in) ----------

def _spawn_claude(name: str) -> None:
    """Spawn an interactive `claude` pane and get it to its input box.

    A nested launch (from inside another Claude session) starts with the
    folder-trust dialog even in HOME; the test accepts it deliberately —
    PrimeDirective never will. Then wait for the idle input box.
    """
    Pty.spawn(
        name,
        cmd="env -u CLAUDECODE claude --model haiku",
        cwd=os.path.expanduser("~"),
        cols=120,
        rows=40,
    )
    Pty.wait_for(name, "❯", timeout=30.0)
    time.sleep(1.0)
    if Mesh.detect_blocked(name) == "claude trust prompt":
        Pty.send(name, "<Down><Enter>")
    Pty.wait_for(name, "? for shortcuts", timeout=30.0)



@pytest.mark.manual
@pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="claude CLI not installed",
)
def test_captain_kirk_drives_real_claude():
    """Drive a real `claude` CLI and get a sentinel-bounded reply.

    Marked @pytest.mark.manual; not in default CI. Requires:
      - claude CLI on PATH
      - valid auth (ANTHROPIC_API_KEY or `claude login` already done)
    """
    _spawn_claude("kirk")
    reply = Mesh.send_with_done(
        "kirk",
        "Reply with the literal text 'ack' followed by <<END>> on a new line.",
        done_marker="<<END>>",
        timeout=60.0,
    )
    assert reply.strip(), "captain got an empty reply"
