from __future__ import annotations

import libtmux

PREFIX = "agent-pty-"


class SessionExistsError(Exception):
    pass


class SessionNotFoundError(Exception):
    pass


_server: libtmux.Server | None = None


def _get_server() -> libtmux.Server:
    global _server
    if _server is None:
        _server = libtmux.Server()
    return _server


def _full(name: str) -> str:
    return f"{PREFIX}{name}"


def _strip(full: str) -> str:
    return full[len(PREFIX):] if full.startswith(PREFIX) else full


def _has(server: libtmux.Server, full_name: str) -> bool:
    return any(s.name == full_name for s in server.sessions)


def spawn(
    name: str,
    cmd: str | None = None,
    cwd: str | None = None,
    cols: int = 80,
    rows: int = 24,
) -> str:
    server = _get_server()
    full = _full(name)
    if _has(server, full):
        raise SessionExistsError(f"Session {name!r} already exists")
    server.new_session(
        session_name=full,
        attach=False,
        kill_session=False,
        start_directory=cwd,
        window_command=cmd,
        x=cols,
        y=rows,
    )
    return name


def kill(name: str) -> None:
    server = _get_server()
    full = _full(name)
    if not _has(server, full):
        raise SessionNotFoundError(f"Session {name!r} not found")
    server.kill_session(full)


def list_sessions() -> list[str]:
    server = _get_server()
    return sorted(_strip(s.name) for s in server.sessions if s.name.startswith(PREFIX))
