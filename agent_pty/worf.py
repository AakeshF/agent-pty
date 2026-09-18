"""Worf: tactical / adversarial-review pane over the PTY fleet.

The Captain Kirk pattern's "adversarial review" use case as a single call:
spin up an INDEPENDENT reviewer pane that shares no context with the target,
feed it the target pane's current content, and get back a verdict. The point
of the independence is that a fresh reviewer with no memory of how the work
was produced is a stronger critic than self-review.

Mechanics (revised 2026-09 for Claude Code >= 2.1, whose `--print` mode exits
immediately unless it is given a prompt): the reviewer pane is a plain SHELL.
Each review writes the prompt to a temp file and runs `reviewer_cmd` with that
file on stdin, framed so the done-marker prints only after the command exits
(`mesh.frame_shell`). `reviewer_cmd` defaults to `claude -p --output-format
text`; any CLI that reads a prompt from stdin and writes its answer to stdout
works (`gemini`, `aider --message`, `ollama run <model>`, or `cat` in tests).

Worf is an ACTUATOR: it spawns a pane and drives it via mesh.send_with_done.
It composes on existing primitives only — session.spawn/kill for the pane,
io.snapshot to capture the target, mesh.send_with_done for the round-trip —
and never re-implements done-detection or screen-scraping.

Best-effort: the verdict is whatever the reviewer command printed; a command
that never exits yields a TimeoutError after `timeout`. The mechanics are
deterministic; the verdict's quality is the model's.
"""

from __future__ import annotations

import os
import shlex
import tempfile

from agent_pty import mesh
from agent_pty.io import snapshot
from agent_pty.session import kill, list_sessions, spawn
from agent_pty.wait import wait_for

DEFAULT_REVIEWER_SHELL = "bash --norc --noprofile"
DEFAULT_REVIEWER_CMD = "claude -p --output-format text"
DEFAULT_DONE_MARKER = "<<END>>"


def _capture_target(target_name: str, lines: int | None) -> str:
    """Capture the target pane's content: full screen, or last `lines` non-empty lines."""
    snap = snapshot(target_name)
    if lines is None:
        return snap
    nonempty = [ln for ln in snap.split("\n") if ln.strip()]
    return "\n".join(nonempty[-lines:]) if nonempty else ""


def _build_prompt(instruction: str, content: str) -> str:
    """Combine the review instruction and captured content into one prompt."""
    return (
        f"{instruction}\n\n"
        "--- BEGIN CONTENT UNDER REVIEW ---\n"
        f"{content}\n"
        "--- END CONTENT UNDER REVIEW ---\n"
    )


def _ensure_reviewer(reviewer_name: str, reviewer_shell: str) -> None:
    """Spawn the reviewer shell if it is not already running (reuse for follow-ups)."""
    if reviewer_name in list_sessions():
        return
    spawn(reviewer_name, cmd=reviewer_shell)
    try:
        wait_for(reviewer_name, "$", timeout=5.0)
    except TimeoutError:
        # Non-`$` prompts (or a slow shell) still work; send_with_done anchors
        # on the command echo, not on the prompt.
        pass


def review(
    target_name: str,
    instruction: str,
    reviewer_name: str = "worf-reviewer",
    reviewer_cmd: str | None = None,
    done_marker: str = DEFAULT_DONE_MARKER,
    timeout: float = 60.0,
    lines: int | None = None,
    reviewer_shell: str = DEFAULT_REVIEWER_SHELL,
) -> str:
    """Review a target pane's content with an independent reviewer.

    1. Ensure a reviewer SHELL pane named `reviewer_name` exists (spawned on
       first use, reused afterwards so follow-up reviews share nothing but
       the pane).
    2. Capture the target's content (full screen, or its last `lines`
       non-empty lines) and write the review prompt to a temp file.
    3. Run `reviewer_cmd` (default: ``claude -p --output-format text``) with
       that file on stdin, framed by mesh.frame_shell so `done_marker` prints
       only after the command exits, and collect the output via
       mesh.send_with_done.
    4. Return the verdict string.

    The reviewer pane is left running; call `Worf.dismiss(reviewer_name)` to
    tear it down. The reviewer shares no context with the target; that
    independence is the whole point.
    """
    content = _capture_target(target_name, lines)
    _ensure_reviewer(reviewer_name, reviewer_shell)
    prompt = _build_prompt(instruction, content)
    fd, path = tempfile.mkstemp(prefix="agent-pty-worf-", suffix=".txt")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(prompt)
        command = f"{reviewer_cmd or DEFAULT_REVIEWER_CMD} < {shlex.quote(path)}"
        framed = mesh.frame_shell(command, done_marker)
        return mesh.send_with_done(reviewer_name, framed, done_marker, timeout)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def dismiss(reviewer_name: str) -> None:
    """Kill the reviewer pane. Convenience wrapper over session.kill."""
    kill(reviewer_name)


class Worf:
    """Public namespace for the Worf API, parallel to Pty, Mesh, and Spock."""

    review = staticmethod(review)
    dismiss = staticmethod(dismiss)
