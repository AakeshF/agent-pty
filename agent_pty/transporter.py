"""Transporter: checkpoint and restore a pane's *visible context*.

The honest scope first, because it shapes everything below: tmux cannot
migrate a live process. There is no way to freeze a running program's memory,
file descriptors, and child processes and beam them into a fresh pane. So this
is NOT process migration. It is a CONTEXT checkpoint — the rendered screen plus
the spawn spec we can recover (cmd, cwd, geometry) — written to a JSON file and
later used to spawn a *new* pane.

What you get back on restore is a clean pane running the same command in the
same directory, plus the captured screen text available to the caller as
context. What you do NOT get is the old process's in-memory state, scrollback
history beyond the visible screen, environment mutations, or any child it had
spawned. Restore is a fresh start with a remembered backdrop.

Crucially, beam_in does NOT auto-type the captured screen back into the new
pane. Replaying old output as keystrokes would be guessing — it would run
whatever those lines parse to. The screen is returned to the caller (an LLM
agent) to feed back as context however it sees fit.

ACTUATOR: beam_in spawns a pane.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

from agent_pty.io import _get_pane, snapshot
from agent_pty.session import SessionNotFoundError, spawn

CHECKPOINT_VERSION = 1


@dataclass
class Checkpoint:
    name: str
    screen: str
    cmd: str | None
    cwd: str | None
    cols: int | None
    rows: int | None
    timestamp: float


def _pane_meta(name: str) -> tuple[str | None, str | None, int | None, int | None]:
    """Best-effort (cmd, cwd, cols, rows) from tmux for a live pane.

    tmux exposes the *current* foreground command and path, not the original
    spawn `cmd`, so `cmd` here is a best-effort hint (often the shell, e.g.
    "bash"). Geometry is reliable. Any field tmux won't give us is None.
    """
    try:
        pane = _get_pane(name)
    except SessionNotFoundError:
        return None, None, None, None
    cmd = pane.pane_current_command or None
    cwd = pane.pane_current_path or None
    cols = int(pane.pane_width) if pane.pane_width else None
    rows = int(pane.pane_height) if pane.pane_height else None
    return cmd, cwd, cols, rows


def beam_out(name: str, path: str) -> str:
    """Checkpoint a pane's visible context to `path` (JSON). Return `path`.

    Captures the rendered screen via `snapshot` and records what metadata
    tmux can give us (cmd/cwd are best-effort and may be None). Raises
    SessionNotFoundError if the pane is dead — you cannot checkpoint nothing.
    """
    screen = snapshot(name)  # raises SessionNotFoundError if dead
    cmd, cwd, cols, rows = _pane_meta(name)
    cp = Checkpoint(
        name=name,
        screen=screen,
        cmd=cmd,
        cwd=cwd,
        cols=cols,
        rows=rows,
        timestamp=time.time(),
    )
    payload = {"version": CHECKPOINT_VERSION, "checkpoint": asdict(cp)}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return path


def load(path: str) -> Checkpoint:
    """Load a Checkpoint written by `beam_out`. Round-trips the dataclass."""
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    data = payload["checkpoint"] if "checkpoint" in payload else payload
    return Checkpoint(
        name=data["name"],
        screen=data["screen"],
        cmd=data.get("cmd"),
        cwd=data.get("cwd"),
        cols=data.get("cols"),
        rows=data.get("rows"),
        timestamp=data.get("timestamp", 0.0),
    )


def beam_in(
    name: str,
    path: str,
    cmd: str | None = None,
    cols: int | None = None,
    rows: int | None = None,
) -> str:
    """Restore a checkpoint into a NEW pane named `name`. Return `name`.

    Loads the Checkpoint and `session.spawn`s a fresh pane. Explicit args
    override the stored spec: `cmd`/`cols`/`rows` here win over the loaded
    values; the stored `cwd` is always reused (pass a different one by
    spawning yourself if you must). When neither arg nor stored geometry is
    available, `spawn`'s defaults (80x24) apply.

    This does NOT inject the captured screen — that would replay old output
    as keystrokes. Read `load(path).screen` and feed it back as context
    yourself if you want continuity.

    Raises SessionExistsError if `name` is already live (same as
    `session.spawn`), FileNotFoundError if `path` does not exist, and
    KeyError/ValueError if the checkpoint file is malformed (missing required
    fields or not valid JSON).
    """
    cp = load(path)
    use_cmd = cmd if cmd is not None else cp.cmd
    use_cols = cols if cols is not None else cp.cols
    use_rows = rows if rows is not None else cp.rows
    kwargs: dict[str, object] = {}
    if use_cmd is not None:
        kwargs["cmd"] = use_cmd
    if cp.cwd is not None:
        kwargs["cwd"] = cp.cwd
    if use_cols is not None:
        kwargs["cols"] = use_cols
    if use_rows is not None:
        kwargs["rows"] = use_rows
    return spawn(name, **kwargs)


class Transporter:
    """Public namespace for the Transporter API, parallel to Pty/Mesh/Spock."""

    Checkpoint = Checkpoint
    beam_out = staticmethod(beam_out)
    beam_in = staticmethod(beam_in)
    load = staticmethod(load)
