import re
import time

import pytest

from agent_pty import Pty, SessionNotFoundError
from tests.conftest import TEST_SHELL


def test_wait_for_returns_snapshot_on_match():
    Pty.spawn("t1", cmd=TEST_SHELL)
    Pty.send("t1", "echo target-marker\n")
    snap = Pty.wait_for("t1", "target-marker", timeout=3.0)
    assert "target-marker" in snap


def test_wait_for_times_out():
    Pty.spawn("t1", cmd=TEST_SHELL)
    with pytest.raises(TimeoutError):
        Pty.wait_for("t1", "never-appears-xyz-zzz", timeout=0.5)


def test_wait_for_drives_python_repl():
    Pty.spawn("p1", cmd="python3 -q")
    Pty.wait_for("p1", ">>>", timeout=5.0)
    Pty.send("p1", "print(2+2)\n")
    snap = Pty.wait_for("p1", re.compile(r"^4$", re.MULTILINE), timeout=5.0)
    assert "4" in snap


def test_wait_for_low_latency():
    Pty.spawn("t1", cmd=TEST_SHELL)
    Pty.send("t1", "(sleep 0.3 && echo latency-marker) &\n")
    start = time.monotonic()
    Pty.wait_for("t1", "latency-marker", timeout=3.0)
    elapsed = time.monotonic() - start
    assert elapsed < 0.6, f"wait_for took {elapsed:.3f}s, expected <0.6s"


def test_wait_for_nonexistent_raises():
    with pytest.raises(SessionNotFoundError):
        Pty.wait_for("nope", "x", timeout=0.5)
