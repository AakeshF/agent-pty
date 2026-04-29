import subprocess

import pytest

from agent_pty.session import PREFIX

TEST_SHELL = "bash --norc --noprofile"


def _killall_managed() -> None:
    try:
        out = subprocess.check_output(
            ["tmux", "ls", "-F", "#{session_name}"],
            stderr=subprocess.DEVNULL,
        ).decode()
    except subprocess.CalledProcessError:
        return
    for line in out.splitlines():
        if line.startswith(PREFIX):
            subprocess.run(
                ["tmux", "kill-session", "-t", line],
                stderr=subprocess.DEVNULL,
            )


@pytest.fixture(autouse=True)
def _cleanup_managed_sessions():
    _killall_managed()
    yield
    _killall_managed()
