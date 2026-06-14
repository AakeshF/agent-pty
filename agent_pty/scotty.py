"""Scotty: chief engineer — resilience (crash-recovery) + resource budget.

The complement to Kirk/mesh and Spock. Mesh COMMANDS panes and emits a "died"
lifecycle event, but nothing in the fleet REPAIRS a dead pane — and tmux can't
tell us how to bring one back (it has no record of the spec it was spawned
from). Scotty closes that gap: it keeps a module-level REGISTRY of supervised
specs so a crashed pane can be respawned exactly as it was, and a background
Supervisor that does so automatically up to a restart budget. It also answers
the simplest resource question — "are we running too many panes?" — so the
captain can throttle before token/CPU economics blow up.

Scotty is an ACTUATOR: repair() spawns panes. It composes on the read-only
fleet view (session.list_sessions) and the core spawn primitive; it never
sends keystrokes (a respawned pane starts fresh from its cmd).

Best-effort, with honest limits: a respawned pane is a NEW process — in-memory
state, scrollback, and any half-typed input from the dead pane are gone. Scotty
restores the *spec*, not the *session*. Restart budgets exist precisely because
a pane that dies instantly on spawn would otherwise loop forever.
"""

from __future__ import annotations

import threading
import weakref
from dataclasses import dataclass, replace

from agent_pty.session import SessionExistsError, list_sessions, spawn

SUPERVISE_POLL_INTERVAL = 0.5
DEFAULT_RESTARTS_MAX = 3


@dataclass
class Spec:
    """A recorded recipe for (re)spawning one supervised pane.

    `restarts` counts how many times Scotty has respawned this pane; it is the
    budget Supervisor checks against `restarts_max`.
    """

    name: str
    cmd: str | None
    cwd: str | None
    cols: int
    rows: int
    restarts: int = 0


# Module-level registry of supervised specs. tmux is stateless about spawn
# recipes, so this is the only place a dead pane's recipe survives.
_registry: dict[str, Spec] = {}
_lock = threading.Lock()


def register(
    name: str,
    cmd: str | None = None,
    cwd: str | None = None,
    cols: int = 80,
    rows: int = 24,
) -> None:
    """Record (or replace) the spec used to respawn `name` after a crash.

    Mirrors the signature of session.spawn so a caller can register exactly
    what they would spawn. Re-registering a name replaces its spec and resets
    its restart counter (it is a fresh supervision contract).
    """
    with _lock:
        _registry[name] = Spec(name=name, cmd=cmd, cwd=cwd, cols=cols, rows=rows)


def forget(name: str) -> None:
    """Drop `name` from the registry. No-op if it was never registered."""
    with _lock:
        _registry.pop(name, None)


def repair(name: str) -> str:
    """Respawn `name` from its registered spec if it is currently dead.

    - registered AND dead (not in list_sessions): respawn via session.spawn,
      increment its `restarts`, return `name`.
    - registered AND alive: no-op, return `name`.
    - unregistered: raise ValueError (Scotty has no recipe to repair from).

    Best-effort: the respawned pane is a fresh process; only the spec is
    restored, not the dead pane's screen, scrollback, or in-memory state.
    """
    with _lock:
        spec = _registry.get(name)
        if spec is None:
            raise ValueError(f"{name!r} is not registered; nothing to repair")
        if name in list_sessions():
            return name
        try:
            spawn(spec.name, cmd=spec.cmd, cwd=spec.cwd, cols=spec.cols, rows=spec.rows)
        except SessionExistsError:
            # Raced: something respawned it after our liveness check. It's
            # alive now, which is all repair() promises; don't count a restart.
            return name
        _registry[name] = replace(spec, restarts=spec.restarts + 1)
        return name


def status() -> list[Spec]:
    """Return a copy of the registry as a list of Specs (snapshot, safe to keep)."""
    with _lock:
        return [replace(s) for s in _registry.values()]


def over_budget(max_panes: int) -> bool:
    """True iff the live managed-pane count exceeds `max_panes`.

    The cheapest resource signal: a fleet larger than the captain can afford to
    supervise (token/CPU economics) is the orchestrator-bottleneck failure mode.
    Strictly greater-than: `max_panes` panes is at-budget, not over.
    """
    return len(list_sessions()) > max_panes


class Supervisor:
    """A stoppable background thread that auto-repairs crashed registered panes.

    Modeled on mesh.Subscription / mesh._LifecycleMonitor: a daemon thread polls
    list_sessions(); when a REGISTERED pane is missing and its `restarts` is
    below `restarts_max`, it calls repair(). Panes that exhaust their restart
    budget are left dead (a pane that dies on spawn would otherwise loop
    forever). Stop with .close()/.stop(), via the context manager, or by drop
    (a weakref.finalize stops the thread on garbage collection).
    """

    def __init__(
        self,
        restarts_max: int = DEFAULT_RESTARTS_MAX,
        poll: float = SUPERVISE_POLL_INTERVAL,
    ) -> None:
        self._restarts_max = restarts_max
        self._poll = poll
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        weakref.finalize(self, self._stop.set)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                alive = set(list_sessions())
            except Exception:
                self._stop.wait(self._poll)
                continue
            # Snapshot the registry under the lock; repair() re-locks per name.
            with _lock:
                supervised = [replace(s) for s in _registry.values()]
            for spec in supervised:
                if spec.name in alive:
                    continue
                if spec.restarts >= self._restarts_max:
                    continue
                try:
                    repair(spec.name)
                except Exception:
                    # Unregistered mid-loop (ValueError), or spawn raced/failed;
                    # leave it for the next poll rather than killing the thread.
                    pass
            self._stop.wait(self._poll)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    # Alias mirroring mesh.Subscription.close().
    close = stop

    def __enter__(self) -> "Supervisor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def supervise(
    restarts_max: int = DEFAULT_RESTARTS_MAX,
    poll: float = SUPERVISE_POLL_INTERVAL,
) -> Supervisor:
    """Start a background Supervisor that auto-repairs crashed registered panes."""
    return Supervisor(restarts_max=restarts_max, poll=poll)


class Scotty:
    """Public namespace for the Scotty API, parallel to Pty, Mesh, and Spock."""

    register = staticmethod(register)
    forget = staticmethod(forget)
    repair = staticmethod(repair)
    status = staticmethod(status)
    supervise = staticmethod(supervise)
    over_budget = staticmethod(over_budget)
