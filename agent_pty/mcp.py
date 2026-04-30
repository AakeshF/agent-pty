"""MCP server exposing agent-pty as native tool calls for LLM agents.

Run via stdio transport (the standard for local Claude Code integration):
    agent-pty-mcp

Wire into Claude Code by adding to ~/.claude.json or .claude/settings.json:
    {
      "mcpServers": {
        "agent-pty": {
          "command": "/path/to/.venv/bin/agent-pty-mcp"
        }
      }
    }
"""

from __future__ import annotations

import threading
import uuid

from mcp.server.fastmcp import FastMCP

from agent_pty import (
    KeyParseError,
    Mesh,
    Pty,
    SessionExistsError,
    SessionNotFoundError,
)
from agent_pty.mesh import LifecycleStream, Subscription

mcp = FastMCP("agent-pty")


@mcp.tool()
def pty_spawn(
    name: str,
    cmd: str | None = None,
    cwd: str | None = None,
    cols: int = 80,
    rows: int = 24,
) -> str:
    """Create a new persistent terminal session backed by tmux.

    Use this when you need to drive an interactive program: REPLs (python,
    psql, node), TUIs (vim, lazygit, htop, k9s), debuggers (gdb, pdb), or
    any flow with multi-step prompts (sudo password, deploy confirmations,
    auth flows). The session persists across calls; the user can run
    `tmux attach -t agent-pty-<name>` to watch or take over.

    Args:
        name: Session identifier (used in subsequent calls).
        cmd: Command to run; None opens the user's default shell.
        cwd: Working directory.
        cols, rows: Terminal dimensions.

    Returns the session name on success.
    """
    try:
        return Pty.spawn(name, cmd=cmd, cwd=cwd, cols=cols, rows=rows)
    except SessionExistsError as e:
        raise ValueError(str(e))


@mcp.tool()
def pty_send(name: str, text: str) -> str:
    """Send keystrokes to a session.

    Supports literal text plus named keys mixed freely:
        <Enter> <Esc> <Tab> <BS> <Space>
        <Up> <Down> <Left> <Right> <Home> <End>
        <PgUp> <PgDn> <Del>
        <F1>-<F12>
        <C-x> (Ctrl-x), <S-x> (Shift-x), <M-x> (Alt/Meta-x)
        <<  -> literal <

    Examples:
        text="echo hi\\n"            run "echo hi" in shell
        text="ihello<Esc>:wq<Enter>" type and save in vim
        text="<C-c>"                 send Ctrl-C
        text="<Up><Enter>"           recall and re-execute last REPL line

    After sending, use pty_snapshot or pty_wait_for to read the result.
    Returns "ok" on success.
    """
    try:
        Pty.send(name, text)
    except SessionNotFoundError as e:
        raise ValueError(str(e))
    except KeyParseError as e:
        raise ValueError(f"key parse error: {e}")
    return "ok"


@mcp.tool()
def pty_snapshot(name: str) -> str:
    """Return the current rendered screen of a session as plain text.

    Reflects the post-redraw state of the terminal (what a human would
    see right now), not raw stdout history. No ANSI escape codes.

    For waiting on specific output to appear, prefer pty_wait_for —
    it's the synchronization primitive between pty_send and reading
    the result.
    """
    try:
        return Pty.snapshot(name)
    except SessionNotFoundError as e:
        raise ValueError(str(e))


@mcp.tool()
def pty_wait_for(name: str, pattern: str, timeout: float = 10.0) -> str:
    """Block until `pattern` (literal substring) appears in the session's
    screen, then return the matching snapshot.

    This is the synchronization primitive for interactive flows:
        pty_send(name, "command\\n")
        pty_wait_for(name, "expected-output")
        pty_send(name, "next-command\\n")
        ...

    Raises if the pattern doesn't appear within `timeout` seconds.
    """
    try:
        return Pty.wait_for(name, pattern, timeout=timeout)
    except SessionNotFoundError as e:
        raise ValueError(str(e))
    except TimeoutError as e:
        raise ValueError(f"timeout: {e}")


@mcp.tool()
def pty_list() -> list[str]:
    """List the names of currently-managed PTY sessions."""
    return Pty.list()


@mcp.tool()
def pty_kill(name: str) -> str:
    """Kill a session and clean up its tmux state.

    Always call this when finished with a session, especially after
    long-running interactive work — orphaned tmux sessions accumulate
    otherwise.
    """
    try:
        Pty.kill(name)
    except SessionNotFoundError as e:
        raise ValueError(str(e))
    return "ok"


# ---------- Mesh tools (M6) ----------
#
# Mesh exposes orchestration features for the Captain Kirk pattern: one
# agent driving N agents in other panes. The async iterators in the Python
# API (subscribe, lifecycle_events) are exposed here as create/next/close
# tool triplets, since MCP tool calls are request/response.

_subscriptions: dict[str, Subscription] = {}
_lifecycle_streams: dict[str, LifecycleStream] = {}
_registry_lock = threading.Lock()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@mcp.tool()
def mesh_send_with_done(
    name: str,
    text: str,
    done_marker: str = "<<END>>",
    timeout: float = 60.0,
) -> str:
    """Send `text` to a session, wait for `done_marker`, return the reply.

    Captain-Kirk protocol convention: prompt the sub-agent to terminate
    its reply with the marker (e.g. "Answer X. End your reply with
    <<END>>"). The returned string is the reply text bounded by the
    sent prompt and the marker, with leading/trailing whitespace
    trimmed and the marker excluded.

    Use when driving another LLM CLI (or any program with a structured
    reply) where you need to know reliably when the response is done.
    """
    try:
        return Mesh.send_with_done(
            name, text, done_marker=done_marker, timeout=timeout
        )
    except SessionNotFoundError as e:
        raise ValueError(str(e))
    except KeyParseError as e:
        raise ValueError(f"key parse error: {e}")
    except TimeoutError as e:
        raise ValueError(f"timeout: {e}")


@mcp.tool()
def mesh_snapshot_since(name: str, marker: str) -> str:
    """Return text appended to the screen after the most recent occurrence
    of `marker`.

    Useful when you've planted a known string (e.g. via `pty_send`) and
    want only the output that came after, without paying for the full
    screen each time. If `marker` is not on screen, returns the full
    snapshot.
    """
    try:
        return Mesh.snapshot_since(name, marker)
    except SessionNotFoundError as e:
        raise ValueError(str(e))


@mcp.tool()
def mesh_detect_blocked(name: str) -> str:
    """Return a hint string if the session looks blocked on a prompt,
    or empty string if not.

    Heuristic: pattern-matches the bottom rows of the screen against
    common interactive prompts (password, y/n, approval, 2FA, etc.).
    Best-effort signal, not a guarantee. Useful for catching sub-agents
    that have silently stalled on a permission prompt.
    """
    try:
        hint = Mesh.detect_blocked(name)
        return hint or ""
    except SessionNotFoundError as e:
        raise ValueError(str(e))


@mcp.tool()
def mesh_pipe(from_name: str, to_name: str, lines: int = 0) -> str:
    """Inject content from one session's screen into another's input.

    `lines=0` (default) pipes the full current screen.
    `lines=N` pipes the last N non-empty lines.

    The payload moves between panes without surfacing as a return value
    here, so large artifacts (diffs, logs) don't cost orchestrator tokens.

    Caveat: this is fire-and-forget keystroke injection. Newlines become
    Enter presses on the destination. Sanitize content if you don't want
    it executed.
    """
    try:
        Mesh.pipe(from_name, to_name, lines=lines if lines > 0 else None)
    except SessionNotFoundError as e:
        raise ValueError(str(e))
    return "ok"


@mcp.tool()
def mesh_subscribe_create(name: str, pattern: str) -> str:
    """Start a background subscription to `pattern` (literal substring) in
    a session's screen.

    Returns a subscription id; pass it to `mesh_subscribe_next` to block
    on the next match, and `mesh_subscribe_close` when finished. Each
    distinct screen position yields once; static matches don't refire.
    """
    try:
        sub = Mesh.subscribe(name, pattern)
    except SessionNotFoundError as e:
        raise ValueError(str(e))
    sid = _new_id()
    with _registry_lock:
        _subscriptions[sid] = sub
    return sid


@mcp.tool()
def mesh_subscribe_next(subscription_id: str, timeout: float = 10.0) -> str:
    """Block up to `timeout` seconds for the next match on a subscription.

    Returns the matching snapshot, or empty string on timeout. The
    subscription remains open; call repeatedly to consume more events.
    """
    with _registry_lock:
        sub = _subscriptions.get(subscription_id)
    if sub is None:
        raise ValueError(f"unknown subscription_id: {subscription_id}")
    snap = sub.next(timeout=timeout)
    return snap or ""


@mcp.tool()
def mesh_subscribe_close(subscription_id: str) -> str:
    """Close a subscription and free its background thread."""
    with _registry_lock:
        sub = _subscriptions.pop(subscription_id, None)
    if sub is None:
        raise ValueError(f"unknown subscription_id: {subscription_id}")
    sub.close()
    return "ok"


@mcp.tool()
def mesh_lifecycle_create() -> str:
    """Open a lifecycle event stream over managed sessions.

    Returns a stream id; pass it to `mesh_lifecycle_next` to consume the
    next event, and `mesh_lifecycle_close` when done. Events: born,
    died, idle (no screen change for ~2s), busy (idle session changed
    again).
    """
    stream = Mesh.lifecycle_events()
    sid = _new_id()
    with _registry_lock:
        _lifecycle_streams[sid] = stream
    return sid


@mcp.tool()
def mesh_lifecycle_next(stream_id: str, timeout: float = 10.0) -> dict:
    """Block up to `timeout` seconds for the next lifecycle event.

    Returns a dict with keys `kind`, `name`, `timestamp`, or an empty
    dict on timeout.
    """
    with _registry_lock:
        stream = _lifecycle_streams.get(stream_id)
    if stream is None:
        raise ValueError(f"unknown stream_id: {stream_id}")
    ev = stream.next(timeout=timeout)
    if ev is None:
        return {}
    return {"kind": ev.kind, "name": ev.name, "timestamp": ev.timestamp}


@mcp.tool()
def mesh_lifecycle_close(stream_id: str) -> str:
    """Close a lifecycle event stream."""
    with _registry_lock:
        stream = _lifecycle_streams.pop(stream_id, None)
    if stream is None:
        raise ValueError(f"unknown stream_id: {stream_id}")
    stream.close()
    return "ok"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
