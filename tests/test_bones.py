"""Acceptance tests: Bones read-only health-pathology layer.

Bones diagnoses sickness in a still-running pane (errors, thrashing, hung,
dead). Like Spock it NEVER sends keystrokes and NEVER mutates a pane. Tests
run against a real tmux server; the autouse conftest fixture handles cleanup.
"""

import shutil
import time

import pytest

from agent_pty import Pty
from agent_pty.bones import Bones, Diagnosis
from tests.conftest import TEST_SHELL


def _spawn_idle(name: str) -> None:
    """Spawn a quiescent bash shell and wait for its prompt to settle."""
    Pty.spawn(name, cmd=TEST_SHELL)
    Pty.wait_for(name, "$", timeout=3.0)
    time.sleep(0.3)


def _poll_symptom(name: str, want: str, deadline_s: float = 2.0) -> Diagnosis:
    """Poll examine() until `want` appears in symptoms or deadline elapses."""
    deadline = time.monotonic() + deadline_s
    diag = Bones.examine(name)
    while time.monotonic() < deadline and want not in diag.symptoms:
        diag = Bones.examine(name)
    return diag


# ---------- 1. quiescent shell at a prompt -> healthy ----------


def test_quiescent_prompt_is_healthy():
    _spawn_idle("bn-ok")
    diag = Bones.examine("bn-ok")
    assert diag.healthy is True
    assert diag.symptoms == []


# ---------- 2. a Python traceback -> "errors" ----------


def test_python_traceback_reports_errors():
    _spawn_idle("bn-err")
    # Print a canned traceback-looking blob without invoking python.
    Pty.send("bn-err", "printf 'Traceback (most recent call last):\\n")
    Pty.send("bn-err", "  File x\\nValueError: boom\\n'\n")
    Pty.wait_for("bn-err", "ValueError", timeout=3.0)
    time.sleep(0.3)
    diag = Bones.examine("bn-err")
    assert "errors" in diag.symptoms
    assert diag.healthy is False


def test_command_not_found_reports_errors():
    _spawn_idle("bn-cnf")
    Pty.send("bn-cnf", "this-binary-does-not-exist-zzz\n")
    Pty.wait_for("bn-cnf", "not found", timeout=3.0)
    time.sleep(0.3)
    diag = Bones.examine("bn-cnf")
    assert "errors" in diag.symptoms


# ---------- 3. a thrashing pane -> "thrashing" ----------


def test_thrashing_pane_reports_thrashing():
    if shutil.which("yes") is None:
        pytest.skip("'yes' binary not available")
    _spawn_idle("bn-thrash")
    Pty.send("bn-thrash", "yes sameLINE\n")
    diag = _poll_symptom("bn-thrash", "thrashing", deadline_s=3.0)
    assert "thrashing" in diag.symptoms
    assert diag.healthy is False


# ---------- 4. dead session -> not healthy, "dead" ----------


def test_unmanaged_name_is_dead():
    diag = Bones.examine("bn-never-spawned-xyz")
    assert diag.healthy is False
    assert "dead" in diag.symptoms


def test_killed_pane_is_dead():
    _spawn_idle("bn-kill")
    assert Bones.examine("bn-kill").healthy is True
    Pty.kill("bn-kill")
    diag = Bones.examine("bn-kill")
    assert diag.healthy is False
    assert "dead" in diag.symptoms


# ---------- 5. triage: sickest first, dead worst ----------


def test_triage_sorts_sickest_first():
    _spawn_idle("bn-tr-ok")
    _spawn_idle("bn-tr-err")
    Pty.send("bn-tr-err", "this-binary-does-not-exist-zzz\n")
    Pty.wait_for("bn-tr-err", "not found", timeout=3.0)
    time.sleep(0.3)
    results = Bones.triage(["bn-tr-ok", "bn-tr-err", "bn-tr-dead"])
    names = [d.name for d in results]
    # Dead is always worst; the error pane outranks the healthy one.
    assert names[0] == "bn-tr-dead"
    assert names.index("bn-tr-err") < names.index("bn-tr-ok")
    healthy = {d.name: d.healthy for d in results}
    assert healthy["bn-tr-ok"] is True
    assert healthy["bn-tr-dead"] is False


def test_triage_none_covers_all_managed():
    _spawn_idle("bn-trn-1")
    _spawn_idle("bn-trn-2")
    results = Bones.triage()
    names = {d.name for d in results}
    assert {"bn-trn-1", "bn-trn-2"} <= names


# ---------- 6. read-only invariant ----------


def test_bones_does_not_type_into_panes():
    _spawn_idle("bn-ro")
    before = Pty.snapshot("bn-ro")
    Bones.examine("bn-ro")
    Bones.triage(["bn-ro"])
    after = Pty.snapshot("bn-ro")
    assert after == before, "Bones mutated a pane (it must be read-only)"
