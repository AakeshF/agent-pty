from agent_pty.io import send, snapshot
from agent_pty.keys import KeyParseError
from agent_pty.mesh import (
    LifecycleEvent,
    LifecycleStream,
    Mesh,
    Subscription,
)
from agent_pty.session import (
    SessionExistsError,
    SessionNotFoundError,
    kill,
    list_sessions,
    spawn,
)
from agent_pty.wait import wait_for


class Pty:
    spawn = staticmethod(spawn)
    kill = staticmethod(kill)
    list = staticmethod(list_sessions)
    send = staticmethod(send)
    snapshot = staticmethod(snapshot)
    wait_for = staticmethod(wait_for)


__all__ = [
    "KeyParseError",
    "LifecycleEvent",
    "LifecycleStream",
    "Mesh",
    "Pty",
    "SessionExistsError",
    "SessionNotFoundError",
    "Subscription",
    "kill",
    "list_sessions",
    "send",
    "snapshot",
    "spawn",
    "wait_for",
]
