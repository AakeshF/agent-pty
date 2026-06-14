"""Acceptance tests: Captain's Log read-only recorder layer.

Captain's Log snapshots watched sessions on a poll loop and appends each
CHANGED screen to a transcript (in memory, and optionally a jsonl file for
audit + replay). It NEVER sends a keystroke and NEVER mutates a pane. Tests run
against a real tmux server; the autouse conftest fixture handles cleanup.
"""

import time

from agent_pty import Pty
from agent_pty.captains_log import CaptainsLog, LogEntry, Recorder, replay, start
from tests.conftest import TEST_SHELL


def _spawn_idle(name: str) -> None:
    """Spawn a quiescent bash shell and wait for its prompt to settle."""
    Pty.spawn(name, cmd=TEST_SHELL)
    Pty.wait_for(name, "$", timeout=3.0)
    time.sleep(0.2)


def _wait_for_entry(rec: Recorder, name: str, token: str, deadline_s: float = 3.0) -> bool:
    """Poll the recorder until some entry for `name` contains `token`."""
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        for e in rec.entries:
            if e.name == name and token in e.screen:
                return True
        time.sleep(0.05)
    return False


# ---------- 1. records a changed screen into memory ----------


def test_records_changed_screen_to_memory():
    _spawn_idle("cl-mem")
    rec = start(["cl-mem"], interval=0.1)
    try:
        Pty.send("cl-mem", "echo CL-TOKEN-A1\n")
        Pty.wait_for("cl-mem", "CL-TOKEN-A1", timeout=3.0)
        assert _wait_for_entry(rec, "cl-mem", "CL-TOKEN-A1")
    finally:
        rec.close()
    assert any("CL-TOKEN-A1" in e.screen for e in rec.entries)
    assert all(isinstance(e, LogEntry) for e in rec.entries)


# ---------- 2. records to a jsonl file and replays it ----------


def test_records_to_file_and_replays(tmp_path):
    log_path = str(tmp_path / "run.jsonl")
    _spawn_idle("cl-file")
    rec = start(["cl-file"], path=log_path, interval=0.1)
    try:
        Pty.send("cl-file", "echo CL-TOKEN-B2\n")
        Pty.wait_for("cl-file", "CL-TOKEN-B2", timeout=3.0)
        assert _wait_for_entry(rec, "cl-file", "CL-TOKEN-B2")
    finally:
        rec.close()

    entries = replay(log_path)
    assert entries, "replay returned no entries"
    assert all(isinstance(e, LogEntry) for e in entries)
    assert any("CL-TOKEN-B2" in e.screen for e in entries)
    assert all(e.name == "cl-file" for e in entries)


# ---------- 3. dedup: an idle pane does not log every tick ----------


def test_idle_pane_is_deduped():
    _spawn_idle("cl-idle")
    rec = start(["cl-idle"], interval=0.05)
    try:
        # Let many poll ticks elapse with no screen change.
        time.sleep(0.6)
        count_after_idle = len(rec.entries)
        time.sleep(0.4)
        count_later = len(rec.entries)
    finally:
        rec.close()
    # An idle pane logs its initial screen (maybe a couple early frames as the
    # prompt settles) but must NOT accrue a new entry on every tick.
    assert count_later == count_after_idle, (
        f"idle pane kept logging: {count_after_idle} -> {count_later}"
    )
    # ~0.4s at 0.05s ticks would be ~8 entries without dedup; assert it's small.
    assert count_later <= 3, f"expected dedup to keep entries small, got {count_later}"


# ---------- 4. a new change after idle produces a new entry ----------


def test_new_change_after_idle_logs_again():
    _spawn_idle("cl-change")
    rec = start(["cl-change"], interval=0.05)
    try:
        time.sleep(0.3)
        baseline = len(rec.entries)
        Pty.send("cl-change", "echo CL-TOKEN-C3\n")
        Pty.wait_for("cl-change", "CL-TOKEN-C3", timeout=3.0)
        assert _wait_for_entry(rec, "cl-change", "CL-TOKEN-C3")
    finally:
        rec.close()
    assert len(rec.entries) > baseline


# ---------- 5. context manager stops the thread ----------


def test_context_manager_records_and_stops():
    _spawn_idle("cl-ctx")
    with start(["cl-ctx"], interval=0.05) as rec:
        Pty.send("cl-ctx", "echo CL-TOKEN-D4\n")
        Pty.wait_for("cl-ctx", "CL-TOKEN-D4", timeout=3.0)
        assert _wait_for_entry(rec, "cl-ctx", "CL-TOKEN-D4")
    # After the context exits, the thread is stopped; entries persist.
    assert not rec._thread.is_alive()
    assert any("CL-TOKEN-D4" in e.screen for e in rec.entries)


# ---------- 6. read-only invariant ----------


def test_recorder_does_not_type_into_panes():
    _spawn_idle("cl-ro")
    before = Pty.snapshot("cl-ro")
    rec = start(["cl-ro"], interval=0.05)
    try:
        time.sleep(0.5)
    finally:
        rec.close()
    after = Pty.snapshot("cl-ro")
    assert after == before, "Captain's Log mutated a pane (it must be read-only)"


# ---------- 7. names=None watches all managed sessions ----------


def test_names_none_records_all_managed():
    _spawn_idle("cl-allA")
    _spawn_idle("cl-allB")
    rec = start(interval=0.1)
    try:
        Pty.send("cl-allA", "echo CL-TOKEN-EA\n")
        Pty.send("cl-allB", "echo CL-TOKEN-EB\n")
        Pty.wait_for("cl-allA", "CL-TOKEN-EA", timeout=3.0)
        Pty.wait_for("cl-allB", "CL-TOKEN-EB", timeout=3.0)
        assert _wait_for_entry(rec, "cl-allA", "CL-TOKEN-EA")
        assert _wait_for_entry(rec, "cl-allB", "CL-TOKEN-EB")
    finally:
        rec.close()
    names = {e.name for e in rec.entries}
    assert {"cl-allA", "cl-allB"} <= names


# ---------- 8. CaptainsLog namespace parity ----------


def test_captains_log_namespace_exposes_api():
    assert CaptainsLog.start is start
    assert CaptainsLog.replay is replay
    assert CaptainsLog.LogEntry is LogEntry
    assert CaptainsLog.Recorder is Recorder
