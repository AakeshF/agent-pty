"""Bones: ship's doctor — read-only health pathology over a PTY pane.

The complement to Spock. Where Spock reports a coarse *state* of a pane
(dead/blocked/idle/busy), Bones diagnoses *sickness* in a still-running pane:
errors on screen, a thrashing loop, a hung mid-task pane. Like Spock, Bones
NEVER sends a keystroke and NEVER mutates a pane — it only OBSERVES.

A pane is "healthy" iff no symptoms are detected. The detectors are all
best-effort screen heuristics over snapshot(s): they read the rendered
screen, not process state, so they can miss off-screen errors and can
misfire on output that merely looks like an error. Use as a signal, not a
guarantee.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from agent_pty.io import snapshot
from agent_pty.session import SessionNotFoundError, list_sessions

# Settle window for the hung/double-sample check (mirrors spock.SETTLE_INTERVAL).
SETTLE_INTERVAL = 0.15
# A non-empty visible line repeated more than this many times reads as thrashing.
THRASH_REPEATS = 8
# Bottom line endings that look like a ready prompt (=> not hung, just waiting).
_PROMPT_ENDINGS = ("$", "#", ">>>", ">")

# Case-insensitive error signatures matched against the whole screen.
_ERROR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\btraceback\b",
        r"\bfatal\b",
        r"\bpanic\b",
        r"segmentation fault",
        r"error:",
        r"\bexception\b",
        r"command not found",
    )
]


@dataclass
class Diagnosis:
    name: str
    healthy: bool
    symptoms: list[str] = field(default_factory=list)


def _has_errors(snap: str) -> bool:
    return any(p.search(snap) for p in _ERROR_PATTERNS)


def _is_thrashing(snap: str) -> bool:
    """True if any single non-empty line is repeated > THRASH_REPEATS times."""
    counts: dict[str, int] = {}
    for line in snap.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        counts[stripped] = counts.get(stripped, 0) + 1
        if counts[stripped] > THRASH_REPEATS:
            return True
    return False


def _looks_like_prompt(snap: str) -> bool:
    for line in reversed(snap.split("\n")):
        if line.strip():
            return line.rstrip().endswith(_PROMPT_ENDINGS)
    return False


def _is_hung(first: str, second: str) -> bool:
    """Unchanged across the settle window AND not sitting at a ready prompt.

    A pane idling at a shell prompt is unchanged too, but it's *waiting*, not
    *hung*; the prompt-ending check distinguishes "stuck mid-task" from "done
    and ready". Best-effort: a pane that merely paused during the window reads
    as hung, and a custom prompt without a known ending can false-positive.
    """
    return first == second and not _looks_like_prompt(second)


def examine(name: str) -> Diagnosis:
    """Diagnose a single pane. healthy == (no symptoms detected).

    Symptoms (best-effort, screen-only): "dead" (gone/SessionNotFoundError),
    "errors" (error signature on screen), "thrashing" (one line repeated
    > THRASH_REPEATS times), "hung" (screen unchanged across the settle
    window and not at a ready prompt). Symptom order is stable.
    """
    if name not in set(list_sessions()):
        return Diagnosis(name, healthy=False, symptoms=["dead"])
    try:
        first = snapshot(name)
    except SessionNotFoundError:
        return Diagnosis(name, healthy=False, symptoms=["dead"])

    symptoms: list[str] = []
    if _has_errors(first):
        symptoms.append("errors")
    if _is_thrashing(first):
        symptoms.append("thrashing")

    time.sleep(SETTLE_INTERVAL)
    try:
        second = snapshot(name)
    except SessionNotFoundError:
        # Died inside the settle window — that's the worst symptom.
        return Diagnosis(name, healthy=False, symptoms=["dead"])
    if _is_hung(first, second):
        symptoms.append("hung")

    return Diagnosis(name, healthy=not symptoms, symptoms=symptoms)


def triage(names: list[str] | None = None) -> list[Diagnosis]:
    """Examine panes and return them sickest-first.

    names=None -> all managed sessions; names given -> only those (an
    unmanaged name diagnoses as "dead"). Sorted by descending symptom count,
    with a dead pane always sorted worst; ties break by name for determinism.
    """
    targets = list_sessions() if names is None else list(names)
    results = [examine(n) for n in targets]
    return sorted(
        results,
        key=lambda d: (-("dead" in d.symptoms), -len(d.symptoms), d.name),
    )


class Bones:
    """Public namespace for the read-only Bones API, parallel to Spock."""

    Diagnosis = Diagnosis
    examine = staticmethod(examine)
    triage = staticmethod(triage)
