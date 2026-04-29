import subprocess
import sys
import time

import pytest

from tests.conftest import TEST_SHELL


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "agent_pty.cli", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def test_cli_roundtrip_spawn_send_snapshot_kill():
    _run("spawn", "cli1", "--cmd", TEST_SHELL)
    _run("send", "cli1", "echo cli-marker\n")

    deadline = time.monotonic() + 3.0
    found = False
    while time.monotonic() < deadline:
        result = _run("snapshot", "cli1")
        if "cli-marker" in result.stdout:
            found = True
            break
        time.sleep(0.1)

    _run("kill", "cli1")
    assert found, "snapshot never showed cli-marker"


def test_cli_list_shows_managed_sessions():
    _run("spawn", "cli2", "--cmd", TEST_SHELL)
    result = _run("list")
    assert "cli2" in result.stdout.splitlines()
    _run("kill", "cli2")
    result = _run("list")
    assert "cli2" not in result.stdout.splitlines()


def test_cli_kill_nonexistent_exits_nonzero():
    result = _run("kill", "definitely-not-there", check=False)
    assert result.returncode != 0
    assert "error" in result.stderr.lower()


def test_cli_wait_for_returns_snapshot():
    _run("spawn", "cli3", "--cmd", TEST_SHELL)
    _run("send", "cli3", "echo wait-marker\n")
    result = _run("wait-for", "cli3", "wait-marker", "--timeout", "3")
    assert "wait-marker" in result.stdout
    _run("kill", "cli3")
