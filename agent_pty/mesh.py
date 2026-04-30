"""Mesh: orchestration layer over multiple PTY sessions.

Built on the core agent-pty primitives. Supports the Captain Kirk pattern:
one agent driving N agents in other panes, with done-detection,
push-style event subscriptions, blocked-on-prompt detection, incremental
snapshots, cross-pane piping, and lifecycle notifications.

Opt-in. Core API users never need to import this module.
"""

from __future__ import annotations

import queue
import re
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Iterator, Optional

from agent_pty.io import send, snapshot
from agent_pty.session import (
    SessionNotFoundError,
    _full,
    _get_server,
    _has,
    list_sessions,
)
from agent_pty.wait import wait_for

DEFAULT_DONE_MARKER = "<<END>>"
SUBSCRIBE_POLL_INTERVAL = 0.025
LIFECYCLE_POLL_INTERVAL = 0.5
IDLE_THRESHOLD = 2.0


# ---------- Sync primitives ----------


def send_with_done(
    name: str,
    text: str,
    done_marker: str = DEFAULT_DONE_MARKER,
    timeout: float = 60.0,
) -> str:
    """Send `text`, wait for `done_marker`, return reply text bounded by them.

    Captain-Kirk convention: prompt the sub-agent to terminate its reply
    with the marker (e.g. "Answer X. End your reply with <<END>>"). The
    returned string is the screen content that appeared after the sent
    prompt's last non-empty line and before the marker, with the marker
    itself excluded. Trailing/leading whitespace is trimmed.

    `text` is treated as literal: any `<` characters are sent as-is, not
    interpreted as named-key tokens. (Use `Pty.send` directly if you need
    keystroke parsing.)
    """
    send(name, text.replace("<", "<<"))
    snap = wait_for(name, done_marker, timeout=timeout)
    return _extract_reply(snap, text, done_marker)


def _extract_reply(snap: str, sent_text: str, done_marker: str) -> str:
    marker_idx = snap.rfind(done_marker)
    if marker_idx == -1:
        return ""
    sent_lines = [line for line in sent_text.split("\n") if line.strip()]
    anchor = sent_lines[-1] if sent_lines else ""
    if anchor:
        anchor_idx = snap.rfind(anchor, 0, marker_idx)
        start = anchor_idx + len(anchor) if anchor_idx != -1 else 0
    else:
        start = 0
    return snap[start:marker_idx].strip("\n").strip()


def snapshot_since(name: str, marker: str) -> str:
    """Return text after the most recent occurrence of `marker` on screen.

    If `marker` is not found, return the full snapshot (caller asked for
    everything-since-something-not-there, which is everything).
    """
    snap = snapshot(name)
    idx = snap.rfind(marker)
    if idx == -1:
        return snap
    return snap[idx + len(marker):].lstrip("\n")


# Heuristic patterns for blocked-on-prompt detection.
_BLOCKED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)password[^:\n]*:\s*$"), "password prompt"),
    (
        re.compile(r"(?i)(\[y/?n\]|\(y/?n\)|\[yes/no\]|\(yes/no\))\s*\??\s*$"),
        "y/n confirmation",
    ),
    (re.compile(r"(?i)\bcontinue\?\s*$"), "continue prompt"),
    (re.compile(r"(?i)(allow|approve)\b[^\n]{0,80}\?\s*$"), "approval prompt"),
    (re.compile(r"(?i)press\s+any\s+key"), "any-key prompt"),
    (re.compile(r"(?i)(2fa|verification)\s+code\s*:?\s*$"), "2FA code prompt"),
]


def detect_blocked(name: str) -> Optional[str]:
    """Return a hint string if the session looks blocked on input, else None.

    Heuristic: regex over the bottom non-empty lines of the rendered screen
    against common interactive prompts. Best-effort. False positives are
    possible (e.g. a `read -p "Continue?"` script). False negatives are
    possible (custom prompts). Use as a signal, not a guarantee.
    """
    snap = snapshot(name)
    lines = [line for line in snap.split("\n") if line.strip()]
    if not lines:
        return None
    tail = "\n".join(lines[-3:])
    for pattern, hint in _BLOCKED_PATTERNS:
        if pattern.search(tail):
            return hint
    return None


def pipe(from_name: str, to_name: str, lines: int | None = None) -> None:
    """Inject content from one session's screen into another's input stream.

    `lines=None`: pipe the entire current screen.
    `lines=N`: pipe the last N non-empty lines.

    The payload is sent literally via tmux send-keys (with `<` escaped to
    `<<` so named-key parsing doesn't fire). The captain's caller never
    sees the payload as a return value, so large artifacts don't cost
    orchestrator tokens.

    Caveat: this is fire-and-forget keystroke injection. The destination
    pane will receive newlines as Enter, etc. Caller is responsible for
    sanitization.
    """
    snap = snapshot(from_name)
    if lines is None:
        payload = snap
    else:
        nonempty = [line for line in snap.split("\n") if line.strip()]
        payload = "\n".join(nonempty[-lines:]) if nonempty else ""
    if not payload:
        return
    escaped = payload.replace("<", "<<")
    send(to_name, escaped)


# ---------- Subscriptions ----------


class Subscription:
    """A live pattern-match subscription against a session's screen.

    Yields a snapshot each time the pattern hits a new position in the
    rendered screen. Static matches that don't move don't refire.

    Use:
        sub = Mesh.subscribe("pane", "ERROR")
        snap = sub.next(timeout=10)   # blocks; returns None on timeout
        sub.close()

    Or as a context manager:
        with Mesh.subscribe("pane", "ERROR") as sub:
            for snap in sub:          # blocks indefinitely between hits
                ...
    """

    def __init__(self, name: str, pattern: re.Pattern[str]) -> None:
        self._name = name
        self._pattern = pattern
        self._queue: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._last_idx: int | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        weakref.finalize(self, self._stop.set)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                snap = snapshot(self._name)
            except SessionNotFoundError:
                self._stop.set()
                return
            matches = list(self._pattern.finditer(snap))
            if matches:
                idx = matches[-1].start()
                if self._last_idx != idx:
                    self._last_idx = idx
                    self._queue.put(snap)
            self._stop.wait(SUBSCRIBE_POLL_INTERVAL)

    def next(self, timeout: float = 10.0) -> Optional[str]:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    def __iter__(self) -> Iterator[str]:
        return self

    def __next__(self) -> str:
        item = self.next(timeout=86400)
        if item is None:
            raise StopIteration
        return item

    def __enter__(self) -> "Subscription":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def subscribe(name: str, pattern: str | re.Pattern[str]) -> Subscription:
    """Start a background subscription to a pattern in a session's screen."""
    if not _has(_get_server(), _full(name)):
        raise SessionNotFoundError(f"Session {name!r} not found")
    compiled = re.compile(re.escape(pattern)) if isinstance(pattern, str) else pattern
    return Subscription(name, compiled)


# ---------- Lifecycle events ----------


@dataclass
class LifecycleEvent:
    kind: str  # "born" | "died" | "idle" | "busy"
    name: str
    timestamp: float


class _LifecycleMonitor:
    """Singleton that watches managed sessions and fans events out to streams."""

    def __init__(self) -> None:
        self._listeners: list[queue.Queue[LifecycleEvent]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._known: set[str] = set()
        self._last_screen: dict[str, tuple[float, str]] = {}
        self._idle: set[str] = set()

    def add_listener(self) -> queue.Queue[LifecycleEvent]:
        q: queue.Queue[LifecycleEvent] = queue.Queue()
        with self._lock:
            self._listeners.append(q)
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._known = set(list_sessions())
                self._last_screen.clear()
                self._idle.clear()
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
        return q

    def remove_listener(self, q: queue.Queue[LifecycleEvent]) -> None:
        with self._lock:
            try:
                self._listeners.remove(q)
            except ValueError:
                pass
            if not self._listeners:
                self._stop.set()
                t = self._thread
                self._thread = None
        if not self._listeners and t is not None:
            t.join(timeout=1.0)

    def _emit(self, event: LifecycleEvent) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for q in listeners:
            q.put(event)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                current = set(list_sessions())
            except Exception:
                self._stop.wait(LIFECYCLE_POLL_INTERVAL)
                continue
            now = time.monotonic()
            for new in current - self._known:
                self._emit(LifecycleEvent("born", new, now))
            for gone in self._known - current:
                self._emit(LifecycleEvent("died", gone, now))
                self._last_screen.pop(gone, None)
                self._idle.discard(gone)
            self._known = current
            for s in list(current):
                try:
                    snap = snapshot(s)
                except SessionNotFoundError:
                    continue
                last = self._last_screen.get(s)
                if last is None:
                    self._last_screen[s] = (now, snap)
                    continue
                last_t, last_snap = last
                if snap != last_snap:
                    self._last_screen[s] = (now, snap)
                    if s in self._idle:
                        self._emit(LifecycleEvent("busy", s, now))
                        self._idle.discard(s)
                else:
                    if s not in self._idle and (now - last_t) >= IDLE_THRESHOLD:
                        self._emit(LifecycleEvent("idle", s, now))
                        self._idle.add(s)
            self._stop.wait(LIFECYCLE_POLL_INTERVAL)


_lifecycle_monitor = _LifecycleMonitor()


class LifecycleStream:
    """Iterator over lifecycle events. Open via `lifecycle_events()`."""

    def __init__(self) -> None:
        self._q = _lifecycle_monitor.add_listener()
        self._closed = False

        def _release(q: queue.Queue[LifecycleEvent]) -> None:
            _lifecycle_monitor.remove_listener(q)

        self._finalizer = weakref.finalize(self, _release, self._q)

    def next(self, timeout: float = 10.0) -> Optional[LifecycleEvent]:
        if self._closed:
            return None
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._finalizer()

    def __iter__(self) -> Iterator[LifecycleEvent]:
        return self

    def __next__(self) -> LifecycleEvent:
        ev = self.next(timeout=86400)
        if ev is None:
            raise StopIteration
        return ev

    def __enter__(self) -> "LifecycleStream":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def lifecycle_events() -> LifecycleStream:
    """Return a stream of lifecycle events for managed sessions.

    Events emitted: born, died, idle (no screen change for IDLE_THRESHOLD
    seconds), busy (idle session changed again). Multiple callers each get
    their own stream; the underlying monitor thread is shared.
    """
    return LifecycleStream()


class Mesh:
    """Public namespace for the mesh API, parallel to Pty."""

    send_with_done = staticmethod(send_with_done)
    snapshot_since = staticmethod(snapshot_since)
    detect_blocked = staticmethod(detect_blocked)
    pipe = staticmethod(pipe)
    subscribe = staticmethod(subscribe)
    lifecycle_events = staticmethod(lifecycle_events)
