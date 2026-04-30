from __future__ import annotations

import libtmux

from agent_pty.keys import parse as _parse_keys
from agent_pty.session import (
    SessionNotFoundError,
    _full,
    _get_server,
    _has,
)


def _get_pane(name: str) -> libtmux.Pane:
    server = _get_server()
    full = _full(name)
    if not _has(server, full):
        raise SessionNotFoundError(f"Session {name!r} not found")
    try:
        session = server.sessions.get(session_name=full)
    except Exception:
        # TOCTOU: session disappeared between the _has check and .get()
        raise SessionNotFoundError(f"Session {name!r} not found")
    return session.active_window.active_pane


def send(name: str, text: str) -> None:
    pane = _get_pane(name)
    for kind, value in _parse_keys(text):
        if kind == "text":
            pane.cmd("send-keys", "-l", value)
        else:
            pane.cmd("send-keys", value)


def snapshot(name: str) -> str:
    pane = _get_pane(name)
    lines = pane.capture_pane()
    if isinstance(lines, list):
        return "\n".join(lines)
    return lines
