"""Captain's Log: read-only recorder / transcript over PTY sessions.

The fleet's flight recorder. Where Kirk/mesh COMMANDS panes and Spock ANALYSES
them, Captain's Log only WITNESSES: it snapshots watched sessions on a poll
loop and appends each changed screen to a transcript — in memory and, if a
path was given, to a jsonl file on disk. It NEVER sends a keystroke and NEVER
mutates a pane; it composes only on read-only primitives (io.snapshot,
session.list_sessions). A reviewer can grep for this.

The point is audit + replay: after a multi-agent run you want to know what each
pane actually showed and when, without the captain having paid tokens to read
every screen every turn. Recording is deduped — an idle pane whose screen has
not changed since its last capture produces no new entry — so a quiet fleet
does not bloat the log every tick.

Best-effort, like the rest of the bridge crew: it samples on an interval, so a
screen that flips and flips back inside one poll window is recorded once or not
at all, and the timeline granularity is the poll interval, not keystroke-exact.
It's a transcript, not a TTY-faithful asciinema recording.
"""

from __future__ import annotations

import json
import threading
import time
import weakref
from dataclasses import dataclass

from agent_pty.io import snapshot
from agent_pty.session import SessionNotFoundError, list_sessions

DEFAULT_INTERVAL = 0.5


@dataclass
class LogEntry:
    timestamp: float  # time.monotonic() at capture
    name: str  # session name (PREFIX-stripped, as used elsewhere)
    screen: str  # the rendered screen at capture time


class Recorder:
    """Background recorder that snapshots watched sessions on a poll loop.

    Models on mesh.Subscription / mesh's lifecycle monitor: a daemon thread
    polls every `interval` seconds, snapshots each watched session, and appends
    a LogEntry whenever a pane's screen CHANGED since its last capture (dedup).
    Entries are kept in memory (`.entries`) and, if `path` was given, also
    appended to a jsonl file (one JSON object per line) as they are captured.

    `names=None` records every currently-managed session AND any session that
    is born later (the watch set is re-resolved each tick). A `names` list pins
    the watch set to exactly those names; a name that is not (yet) managed is
    silently skipped until it appears.

    Use:
        rec = start(["c1"], path="run.jsonl")
        ... drive c1 elsewhere ...
        rec.close()
        for entry in rec.entries: ...

    Or as a context manager:
        with start(["c1"]) as rec:
            ...
    """

    def __init__(
        self,
        names: list[str] | None = None,
        path: str | None = None,
        interval: float = DEFAULT_INTERVAL,
    ) -> None:
        self._names = list(names) if names is not None else None
        self._path = path
        self._interval = interval
        self._entries: list[LogEntry] = []
        self._last_screen: dict[str, str] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        if path is not None:
            # Truncate any prior log so a fresh recording starts clean.
            open(path, "w").close()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        weakref.finalize(self, self._stop.set)

    def _targets(self) -> list[str]:
        if self._names is not None:
            return self._names
        try:
            return list_sessions()
        except Exception:
            return []

    def _capture(self, name: str) -> None:
        try:
            screen = snapshot(name)
        except SessionNotFoundError:
            return
        if self._last_screen.get(name) == screen:
            return  # dedup: unchanged since last capture
        self._last_screen[name] = screen
        entry = LogEntry(time.monotonic(), name, screen)
        with self._lock:
            self._entries.append(entry)
        if self._path is not None:
            try:
                self._write(entry)
            except OSError:
                # Disk full / permission revoked mid-run: keep the in-memory
                # transcript going rather than letting the recorder thread die.
                pass

    def _write(self, entry: LogEntry) -> None:
        line = json.dumps(
            {"timestamp": entry.timestamp, "name": entry.name, "screen": entry.screen}
        )
        with open(self._path, "a") as fh:
            fh.write(line + "\n")

    def _run(self) -> None:
        while not self._stop.is_set():
            for name in self._targets():
                self._capture(name)
            self._stop.wait(self._interval)

    @property
    def entries(self) -> list[LogEntry]:
        """A snapshot copy of the entries captured so far."""
        with self._lock:
            return list(self._entries)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    # Alias: mesh streams use .close(); keep both for parity.
    close = stop

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def start(
    names: list[str] | None = None,
    path: str | None = None,
    interval: float = DEFAULT_INTERVAL,
) -> Recorder:
    """Start recording watched sessions to memory (and optionally a jsonl file).

    names=None records all managed sessions (re-resolved each tick, so newborn
    sessions are picked up); a list pins the watch set. Returns a live Recorder;
    call .close() (or use it as a context manager) to stop the background thread.
    """
    return Recorder(names=names, path=path, interval=interval)


def replay(path: str) -> list[LogEntry]:
    """Parse a jsonl transcript file back into a list of LogEntry.

    The inverse of the file Recorder writes. Blank lines are skipped, and so is
    any malformed or partially-written line (e.g. a torn final line from a
    crashed run) — replay recovers every well-formed entry rather than failing
    the whole file. The list is returned in file order (i.e. capture order).
    """
    entries: list[LogEntry] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                entries.append(
                    LogEntry(obj["timestamp"], obj["name"], obj["screen"])
                )
            except (ValueError, KeyError, TypeError):
                continue
    return entries


class CaptainsLog:
    """Public namespace for the recorder API, parallel to Pty/Mesh/Spock."""

    LogEntry = LogEntry
    Recorder = Recorder
    start = staticmethod(start)
    replay = staticmethod(replay)
