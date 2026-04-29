import subprocess

import pytest

from agent_pty import Pty, SessionExistsError, SessionNotFoundError


def _tmux_has(full_name: str) -> bool:
    r = subprocess.run(
        ["tmux", "has-session", "-t", full_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return r.returncode == 0


def _tmux_display(target: str, fmt: str) -> str:
    return subprocess.check_output(
        ["tmux", "display-message", "-p", "-t", target, fmt],
    ).decode().strip()


def test_spawn_creates_session():
    Pty.spawn("t1")
    assert _tmux_has("agent-pty-t1")


def test_spawn_duplicate_raises():
    Pty.spawn("t1")
    with pytest.raises(SessionExistsError):
        Pty.spawn("t1")


def test_kill_removes_session():
    Pty.spawn("t1")
    Pty.kill("t1")
    assert not _tmux_has("agent-pty-t1")


def test_kill_nonexistent_raises():
    with pytest.raises(SessionNotFoundError):
        Pty.kill("nope")


def test_list_returns_managed_sessions():
    assert Pty.list() == []
    Pty.spawn("t1")
    Pty.spawn("t2")
    assert Pty.list() == ["t1", "t2"]
    Pty.kill("t1")
    Pty.kill("t2")
    assert Pty.list() == []


def test_spawn_runs_cmd_in_cwd(tmp_path):
    Pty.spawn("c1", cmd="sleep 30", cwd=str(tmp_path))
    assert _tmux_display("agent-pty-c1", "#{pane_start_path}") == str(tmp_path)


def test_spawn_honors_dimensions():
    Pty.spawn("d1", cols=120, rows=40)
    assert _tmux_display("agent-pty-d1", "#{pane_width}x#{pane_height}") == "120x40"
