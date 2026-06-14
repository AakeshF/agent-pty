"""Worf: tactical / adversarial-review pane over the PTY fleet.

The Captain Kirk pattern's "adversarial review" use case as a single call:
spin up an INDEPENDENT reviewer pane that shares no context with the target,
feed it the target pane's current content, and get back a verdict. The point
of the independence is that a fresh reviewer with no memory of how the work
was produced is a stronger critic than self-review.

Worf is an ACTUATOR: it spawns a pane and drives it via mesh.send_with_done.
It composes on existing primitives only — session.spawn/kill for the pane,
io.snapshot to capture the target, mesh.send_with_done for the round-trip —
and never re-implements done-detection or screen-scraping.

Best-effort, same caveats as mesh: the reviewer pane must be told to end its
reply with `done_marker`, and a real reviewer (a `claude` CLI) will only be
as good a critic as the model behind it. The mechanics are deterministic; the
verdict's quality is not.
"""

from __future__ import annotations

from agent_pty import mesh
from agent_pty.io import snapshot
from agent_pty.session import spawn, kill

DEFAULT_REVIEWER_CMD = "bash --norc --noprofile"
DEFAULT_DONE_MARKER = "<<END>>"


def _capture_target(target_name: str, lines: int | None) -> str:
    """Capture the target pane's content: full screen, or last `lines` non-empty lines."""
    snap = snapshot(target_name)
    if lines is None:
        return snap
    nonempty = [ln for ln in snap.split("\n") if ln.strip()]
    return "\n".join(nonempty[-lines:]) if nonempty else ""


def _build_prompt(instruction: str, content: str, done_marker: str) -> str:
    """Combine the review instruction and captured content into one prompt.

    The reviewer is told to end its reply with `done_marker` so the
    round-trip can be bounded by mesh.send_with_done.
    """
    return (
        f"{instruction}\n\n"
        "--- BEGIN CONTENT UNDER REVIEW ---\n"
        f"{content}\n"
        "--- END CONTENT UNDER REVIEW ---\n\n"
        f"End your reply with {done_marker} on its own line.\n"
    )


def review(
    target_name: str,
    instruction: str,
    reviewer_name: str = "worf-reviewer",
    reviewer_cmd: str | None = None,
    done_marker: str = DEFAULT_DONE_MARKER,
    timeout: float = 60.0,
    lines: int | None = None,
) -> str:
    """Review a target pane's content with an independent reviewer pane.

    1. Spawn a reviewer pane (`reviewer_cmd=None` -> a plain shell; real use
       passes e.g. ``"claude --print --output-format text"``).
    2. Capture the target's content (full screen, or its last `lines`
       non-empty lines).
    3. Ask the reviewer to review it via mesh.send_with_done, bounding the
       reply with `done_marker`.
    4. Return the verdict string.

    The reviewer pane is left running — the caller decides whether to keep it
    for follow-up questions or call `Worf.dismiss(reviewer_name)`. The reviewer
    shares no context with the target; that independence is the whole point.

    Best-effort: the verdict is exactly what the reviewer emitted between the
    sent prompt and the marker. A reviewer that never prints the marker yields
    an empty verdict after `timeout`.
    """
    content = _capture_target(target_name, lines)
    spawn(reviewer_name, cmd=reviewer_cmd or DEFAULT_REVIEWER_CMD)
    prompt = _build_prompt(instruction, content, done_marker)
    return mesh.send_with_done(reviewer_name, prompt, done_marker, timeout)


def dismiss(reviewer_name: str) -> None:
    """Kill the reviewer pane. Convenience wrapper over session.kill."""
    kill(reviewer_name)


class Worf:
    """Public namespace for the Worf API, parallel to Pty, Mesh, and Spock."""

    review = staticmethod(review)
    dismiss = staticmethod(dismiss)
