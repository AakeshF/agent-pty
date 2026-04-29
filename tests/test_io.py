import time

import pytest

from agent_pty import Pty, SessionNotFoundError
from tests.conftest import TEST_SHELL


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_send_and_snapshot_echo_roundtrip():
    Pty.spawn("t1", cmd=TEST_SHELL)
    Pty.send("t1", "echo hello-world\n")
    assert _wait_until(lambda: "hello-world" in Pty.snapshot("t1"))


def test_snapshot_has_no_escape_codes():
    Pty.spawn("t1", cmd=TEST_SHELL)
    Pty.send("t1", "echo formatted\n")
    assert _wait_until(lambda: "formatted" in Pty.snapshot("t1"))
    assert "\x1b[" not in Pty.snapshot("t1")


def test_snapshot_reflects_current_screen_not_history():
    Pty.spawn("t1", cmd=TEST_SHELL)
    Pty.send("t1", "echo first-line\n")
    assert _wait_until(lambda: "first-line" in Pty.snapshot("t1"))
    Pty.send("t1", "clear\n")
    assert _wait_until(
        lambda: "first-line" not in Pty.snapshot("t1"), timeout=3.0
    )


def test_snapshot_of_fresh_shell_shows_prompt():
    Pty.spawn("t1", cmd=TEST_SHELL)
    assert _wait_until(lambda: Pty.snapshot("t1").strip() != "")


def test_send_to_nonexistent_raises():
    with pytest.raises(SessionNotFoundError):
        Pty.send("nope", "x")


def test_snapshot_of_nonexistent_raises():
    with pytest.raises(SessionNotFoundError):
        Pty.snapshot("nope")
