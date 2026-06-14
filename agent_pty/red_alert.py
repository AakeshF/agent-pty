"""RedAlert: escalation to the human when the fleet needs attention.

The complement to Spock's silent reporting. Spock OBSERVES and returns a
structured assessment, but a report nobody reads is no help: the captain (or
human) may not look at it for several turns, so a deadlock or a dead pane sits
unnoticed. RedAlert closes that gap — it watches the fleet on a background
thread and FIRES A NOTIFICATION the moment something needs a human.

Read-only on panes (it leans entirely on spock.assess, which never mutates a
pane). The only side effect is the notification itself: a desktop toast via
notify-send when available, else a line on stderr, or any callable the caller
supplies. Best-effort: it inherits Spock's deadlock/dead heuristics, so it can
miss a custom prompt or fire on a benign `read -p` — a signal, not a guarantee.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from agent_pty.spock import assess

POLL_INTERVAL = 0.5
NOTIFY_TITLE = "agent-pty RED ALERT"

Notifier = Callable[[str], None]


@dataclass
class Alert:
    kind: str  # "deadlock" | "death"
    detail: str  # one-line, human/LLM readable
    names: list[str] = field(default_factory=list)  # implicated session names


def check(names: list[str] | None = None) -> Optional[Alert]:
    """Inspect the fleet; return an Alert if a human is needed, else None.

    Delegates the heuristics to ``spock.assess``. A deadlock (>=1 blocked pane
    and nothing busy — the whole fleet stalled on the captain or human) is the
    highest-value signal and is preferred when both conditions hold. Any pane
    in state "dead" also escalates. Returns None when the fleet is fine.

    Best-effort: deadlock/blocked detection inherits Spock's (and mesh's)
    regex limits; "dead" is exactly "not in list_sessions / snapshot raised".
    """
    report = assess(names)
    if report.deadlock:
        blocked = [p.name for p in report.panes if p.state == "blocked"]
        return Alert("deadlock", report.summary, blocked)
    dead = [p.name for p in report.panes if p.state == "dead"]
    if dead:
        detail = f"{len(dead)} dead pane(s): {', '.join(dead)}."
        return Alert("death", detail, dead)
    return None


def _default_notify(message: str) -> None:
    """Fire a desktop toast via notify-send if present, else print to stderr.

    Dependency-free and non-fatal: any failure to reach notify-send falls back
    to stderr rather than raising, so a notification never crashes the watcher.
    """
    notify_send = shutil.which("notify-send")
    if notify_send:
        try:
            subprocess.run(
                [notify_send, NOTIFY_TITLE, message],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception:
            pass  # fall through to stderr
    print(f"[{NOTIFY_TITLE}] {message}", file=sys.stderr, flush=True)


def notify(message: str, notifier: Notifier | None = None) -> None:
    """Send one notification. Default notifier: notify-send, else stderr.

    A custom ``notifier`` is any ``callable(str)`` — e.g. a Slack post, an
    email, or (in tests) a list's ``append``. Kept dependency-free and
    non-fatal.
    """
    (notifier or _default_notify)(message)


class Alerter:
    """Background thread that polls ``check`` and notifies on a new alert.

    On each poll it calls ``check(names)``. When the result is a NEW alert
    (deduped against the previous one — identical consecutive alerts fire
    once), it calls ``notify(alert.detail, notifier)``. A return to a healthy
    fleet (None) resets the dedup state, so a re-occurring problem re-alerts.

    Use:
        al = RedAlert.watch(["a", "b"], notifier=my_cb)
        ...
        al.close()

    Or as a context manager:
        with RedAlert.watch(notifier=my_cb):
            ...
    """

    def __init__(
        self,
        names: list[str] | None = None,
        notifier: Notifier | None = None,
        poll: float = POLL_INTERVAL,
    ) -> None:
        self._names = names
        self._notifier = notifier
        self._poll = poll
        self._stop = threading.Event()
        self._last: tuple[str, str] | None = None  # (kind, detail) dedup key
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                alert = check(self._names)
            except Exception:
                # Never let a transient fleet-read error kill the watcher.
                self._stop.wait(self._poll)
                continue
            if alert is None:
                self._last = None
            else:
                key = (alert.kind, alert.detail)
                if key != self._last:
                    self._last = key
                    try:
                        notify(alert.detail, self._notifier)
                    except Exception:
                        pass  # a broken notifier must not kill the watcher
            self._stop.wait(self._poll)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    def close(self) -> None:
        self.stop()

    def __enter__(self) -> "Alerter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def watch(
    names: list[str] | None = None,
    notifier: Notifier | None = None,
    poll: float = POLL_INTERVAL,
) -> Alerter:
    """Start a background ``Alerter`` watching the fleet. Caller must close it."""
    return Alerter(names=names, notifier=notifier, poll=poll)


class RedAlert:
    """Public namespace for the RedAlert API, parallel to Pty/Mesh/Spock."""

    Alert = Alert
    check = staticmethod(check)
    notify = staticmethod(notify)
    watch = staticmethod(watch)
