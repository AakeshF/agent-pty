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

from mcp.server.fastmcp import FastMCP

from agent_pty import (
    KeyParseError,
    Pty,
    SessionExistsError,
    SessionNotFoundError,
)

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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
