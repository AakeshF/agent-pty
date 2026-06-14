"""Sulu: the helmsman / DISPATCHER over a fleet of PTY panes.

Where Kirk/mesh COMMANDS individual panes and Spock OBSERVES them, Sulu takes
a backlog of work and ROUTES it: given a list of commands and a pool of
candidate panes, it assigns each command to an IDLE pane (found via
Spock.assess), runs it framed with a done-marker (via mesh.send_with_done),
and collects the replies. When there are more commands than free panes it
queues the overflow and assigns it as panes free up.

This directly attacks the "orchestrator becomes the bottleneck" failure mode:
the captain hands Sulu a backlog instead of hand-feeding each pane one command
at a time. Sulu is an ACTUATOR — it sends keystrokes into panes.

Best-effort, and it inherits every heuristic limit of its dependencies:
idle-detection is Spock's double-sample (a pane quiet *during the settle
window* reads as idle even mid-task), and reply-extraction is mesh's
done-marker scraping. Use for fast, deterministic, self-terminating work; a
command that never finds a free pane within `timeout` maps to "".
"""

from __future__ import annotations

import shlex
import time

from agent_pty import mesh
from agent_pty.session import SessionNotFoundError
from agent_pty.spock import assess

DEFAULT_DONE_MARKER = "<<END>>"
TIMEOUT_MARKER = "_timeout"


def _idle_panes(names: list[str] | None) -> list[str]:
    """Return currently-idle candidate panes (Spock.assess -> state=="idle").

    dispatch runs each command to completion before reusing a pane, so a pane
    handed work is busy (its screen is changing) by the time we re-poll — no
    separate in-flight bookkeeping is needed.
    """
    return [p.name for p in assess(names).panes if p.state == "idle"]


def _frame(command: str, done_marker: str) -> str:
    """Frame a shell command so its output is followed by the done marker.

    The marker is printed on its own line *after* the command runs, so
    mesh.send_with_done sees it only once the command has finished. shlex
    quoting keeps an arbitrary marker safe inside the shell line.
    """
    return f"{command}; printf '%s\\n' {shlex.quote(done_marker)}\n"


def dispatch(
    commands: list[str],
    names: list[str] | None = None,
    done_marker: str = DEFAULT_DONE_MARKER,
    timeout: float = 60.0,
    poll: float = 0.2,
) -> dict[str, str]:
    """Auto-assign a backlog of commands to idle panes and collect replies.

    `commands`   — shell commands (or prompts) to run, one per pane-slot.
    `names`      — candidate panes (None -> all managed sessions).
    `done_marker`— sentinel framing each command's output (see `_frame`).
    `timeout`    — overall wall-clock budget for the whole backlog.
    `poll`       — how often to re-poll Spock for a freshly-idle pane.

    Each command is assigned to an IDLE pane (Spock.assess -> state=="idle")
    and run via mesh.send_with_done framed so its output ends with
    `done_marker`. With more commands than idle panes, the overflow is queued
    and assigned as panes free up. Returns a dict mapping each command to its
    reply text; a command that never gets a free pane within `timeout` maps to
    "" (see TIMEOUT_MARKER for the rationale of the empty sentinel).

    Note: duplicate command strings collapse in the returned dict (it is keyed
    by command); the last completion for a given string wins.
    """
    results: dict[str, str] = {cmd: "" for cmd in commands}
    pending = list(commands)
    deadline = time.monotonic() + timeout

    while pending and time.monotonic() < deadline:
        free = _idle_panes(names)
        if not free:
            time.sleep(poll)
            continue
        # Assign as many queued commands as we have free panes, running each
        # to completion before reusing its pane. We re-poll Spock on the next
        # loop so panes that free up mid-batch get picked up.
        for pane in free:
            if not pending:
                break
            command = pending.pop(0)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                pending.insert(0, command)
                break
            results[command] = _run_one(pane, command, done_marker, remaining)

    return results


def _run_one(pane: str, command: str, done_marker: str, timeout: float) -> str:
    """Run a single framed command on `pane` and return its reply (or "")."""
    try:
        return mesh.send_with_done(
            pane, _frame(command, done_marker), done_marker=done_marker, timeout=timeout
        )
    except (TimeoutError, SessionNotFoundError):
        # A pane that dies mid-command, or a marker that never arrives within
        # the per-command budget, yields no reply rather than crashing the
        # whole backlog. Unexpected errors propagate so real bugs don't hide.
        return ""


class Sulu:
    """Public namespace for the Sulu dispatcher API, parallel to Pty/Mesh/Spock."""

    dispatch = staticmethod(dispatch)
