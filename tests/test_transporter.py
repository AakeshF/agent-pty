"""Acceptance tests: Transporter checkpoint/restore of a pane's visible context.

The Transporter writes a pane's rendered screen plus its (best-effort) spawn
spec to a JSON file, and restores it by spawning a *new* pane. It is honestly
a context checkpoint, not process migration. Tests run against a real tmux
server; the autouse conftest fixture handles cleanup. They use tmp_path so no
checkpoint file leaks between runs.
"""

import json
import time

import pytest

from agent_pty import Pty
from agent_pty.session import SessionNotFoundError
from agent_pty.transporter import Checkpoint, Transporter
from tests.conftest import TEST_SHELL


def _spawn_with_token(name: str, token: str) -> None:
    """Spawn a shell, echo a known token, and wait for it to render."""
    Pty.spawn(name, cmd=TEST_SHELL)
    Pty.wait_for(name, "$", timeout=3.0)
    Pty.send(name, f"echo {token}\n")
    Pty.wait_for(name, token, timeout=3.0)
    time.sleep(0.2)


# ---------- 1. beam_out captures the screen token ----------


def test_beam_out_writes_screen_with_token(tmp_path):
    token = "TRANSPORT-TOKEN-Q9"
    _spawn_with_token("tp-out", token)
    path = str(tmp_path / "cp.json")
    returned = Transporter.beam_out("tp-out", path)
    assert returned == path
    payload = json.loads((tmp_path / "cp.json").read_text())
    assert token in payload["checkpoint"]["screen"]


def test_beam_out_records_geometry(tmp_path):
    Pty.spawn("tp-geo", cmd=TEST_SHELL, cols=100, rows=30)
    Pty.wait_for("tp-geo", "$", timeout=3.0)
    path = str(tmp_path / "geo.json")
    Transporter.beam_out("tp-geo", path)
    cp = Transporter.load(path)
    assert cp.cols == 100
    assert cp.rows == 30


# ---------- 2. beam_out on a dead pane raises ----------


def test_beam_out_dead_pane_raises(tmp_path):
    path = str(tmp_path / "dead.json")
    with pytest.raises(SessionNotFoundError):
        Transporter.beam_out("tp-never-spawned-xyz", path)


# ---------- 3. load round-trips the Checkpoint ----------


def test_load_round_trips_checkpoint(tmp_path):
    token = "ROUNDTRIP-TOKEN-K2"
    _spawn_with_token("tp-rt", token)
    path = str(tmp_path / "rt.json")
    Transporter.beam_out("tp-rt", path)
    cp = Transporter.load(path)
    assert isinstance(cp, Checkpoint)
    assert cp.name == "tp-rt"
    assert token in cp.screen
    assert isinstance(cp.timestamp, float)
    assert cp.timestamp > 0


# ---------- 4. beam_in spawns a new pane ----------


def test_beam_in_spawns_new_pane(tmp_path):
    token = "BEAMIN-TOKEN-M4"
    _spawn_with_token("tp-src", token)
    path = str(tmp_path / "src.json")
    Transporter.beam_out("tp-src", path)

    assert "tp-dst" not in Pty.list()
    returned = Transporter.beam_in("tp-dst", path, cmd=TEST_SHELL)
    assert returned == "tp-dst"
    assert "tp-dst" in Pty.list()
    # The restored pane is a fresh, usable shell.
    Pty.wait_for("tp-dst", "$", timeout=3.0)


# ---------- 5. beam_in does NOT auto-inject the old screen ----------


def test_beam_in_does_not_replay_old_screen(tmp_path):
    token = "SHOULD-NOT-REPLAY-T8"
    _spawn_with_token("tp-noreplay-src", token)
    path = str(tmp_path / "nr.json")
    Transporter.beam_out("tp-noreplay-src", path)

    Transporter.beam_in("tp-noreplay-dst", path, cmd=TEST_SHELL)
    Pty.wait_for("tp-noreplay-dst", "$", timeout=3.0)
    time.sleep(0.3)
    # The fresh pane must not contain the old captured token: the screen is
    # returned for the caller to use as context, never typed back in.
    fresh = Pty.snapshot("tp-noreplay-dst")
    assert token not in fresh
    # But the checkpoint file still carries it for the caller.
    assert token in Transporter.load(path).screen


# ---------- 6. cmd arg overrides stored cmd ----------


def test_beam_in_cmd_arg_overrides_stored(tmp_path):
    _spawn_with_token("tp-ovr-src", "OVR-TOKEN-1")
    path = str(tmp_path / "ovr.json")
    Transporter.beam_out("tp-ovr-src", path)
    # Override with a command that prints a distinctive marker.
    override = "bash --norc --noprofile -c 'echo OVERRIDE-RAN-X3; exec bash --norc --noprofile'"
    Transporter.beam_in("tp-ovr-dst", path, cmd=override)
    Pty.wait_for("tp-ovr-dst", "OVERRIDE-RAN-X3", timeout=3.0)
    assert "OVERRIDE-RAN-X3" in Pty.snapshot("tp-ovr-dst")
