"""Spock: read-only science officer over the fleet of PTY sessions.

The complement to Kirk/mesh. Where Mesh COMMANDS panes (sends keys, pipes,
steers), Spock NEVER sends a keystroke and NEVER mutates a pane. It OBSERVES
the whole fleet and returns a deterministic, structured, token-cheap
assessment so the captain doesn't have to read N raw screens.

State per pane is decided in strict precedence: dead, blocked, busy, idle.
busy-vs-idle uses a stateless double-sample heuristic (snapshot, wait a short
settle window, snapshot again; changed => busy). A fleet uses a SINGLE shared
settle window, so cost stays O(1) in sleeps. Best-effort: busy/idle can
misfire on a clock-redrawing or slow pane; blocked-detection is delegated to
mesh.detect_blocked (same caveats). Use as a signal, not a guarantee.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from agent_pty import mesh
from agent_pty.io import snapshot
from agent_pty.session import SessionNotFoundError, list_sessions

SETTLE_INTERVAL = 0.15


@dataclass
class PaneReport:
    name: str
    state: str  # "dead" | "blocked" | "idle" | "busy"
    hint: str | None  # blocked-prompt hint when state=="blocked", else None
    digest: str  # last non-empty line of the rendered screen, trimmed ("" if blank/dead)


@dataclass
class FleetReport:
    panes: list[PaneReport]
    deadlock: bool  # True iff >=1 pane blocked AND no pane busy: work stalled on captain/human
    summary: str  # one-line synthesis


@dataclass
class Advisory:
    name: str
    priority: int  # 0=most urgent. blocked=0, dead=1, idle=2, busy=3
    reason: str  # human/LLM readable
    action_hint: str  # ADVISORY ONLY (Spock never acts)


_PRIORITY = {"blocked": 0, "dead": 1, "idle": 2, "busy": 3}
_REASON = {
    "blocked": "blocked",  # refined with hint below
    "dead": "session not running",
    "idle": "idle — likely finished",
    "busy": "working",
}
_ACTION = {
    "blocked": "respond to the prompt",
    "dead": "session died — respawn or remove",
    "idle": "collect output / send next instruction",
    "busy": "working — no action needed",
}


def _last_nonempty(snap: str) -> str:
    for line in reversed(snap.split("\n")):
        if line.strip():
            return line.strip()
    return ""


def _classify(name: str, first: str | None) -> tuple[str, str | None, str]:
    """Decide (state, hint, digest) given the FIRST snapshot already taken.

    `first` is None for a dead/unmanaged session. The caller must have slept
    SETTLE_INTERVAL since taking `first`; this takes the second sample here.
    """
    if first is None:
        return "dead", None, ""
    # detect_blocked and snapshot both read the screen and can raise if the
    # session died inside the settle window — that race resolves to "dead".
    try:
        hint = mesh.detect_blocked(name)
        if hint:
            return "blocked", hint, _last_nonempty(first)
        second = snapshot(name)
    except SessionNotFoundError:
        return "dead", None, ""
    state = "busy" if second != first else "idle"
    return state, None, _last_nonempty(second)


def _first_snapshot(name: str, managed: set[str]) -> str | None:
    if name not in managed:
        return None
    try:
        return snapshot(name)
    except SessionNotFoundError:
        return None


def assess(names: list[str] | None = None) -> FleetReport:
    """Observe the fleet and return a structured, token-cheap report.

    names=None -> all managed sessions; names given -> only those (an
    unmanaged name is reported as "dead"). Pane order follows the input
    order (or list_sessions() order when None). A single shared settle
    window is used across the whole fleet.
    """
    managed = set(list_sessions())
    targets = list_sessions() if names is None else list(names)
    firsts = {n: _first_snapshot(n, managed) for n in targets}
    time.sleep(SETTLE_INTERVAL)
    panes = []
    for n in targets:
        state, hint, digest = _classify(n, firsts[n])
        panes.append(PaneReport(n, state, hint, digest))
    counts = {s: sum(1 for p in panes if p.state == s) for s in _PRIORITY}
    deadlock = counts["blocked"] >= 1 and counts["busy"] == 0
    return FleetReport(panes, deadlock, _summarize(panes, counts, deadlock))


def _summarize(panes: list[PaneReport], counts: dict[str, int], deadlock: bool) -> str:
    n = len(panes)
    if deadlock:
        b = next(p for p in panes if p.state == "blocked")
        return f"DEADLOCK — {b.name} blocked on {b.hint}, nothing progressing ({n} panes)."
    parts = [f"{counts[s]} {s}" for s in _PRIORITY if counts[s]]
    body = ", ".join(parts) if parts else "nothing"
    return f"{n} panes: {body}."


def diagnose(name: str) -> PaneReport:
    """Deep single-pane analysis. Same state logic, own settle window.

    The session's two snapshots bracket a single SETTLE_INTERVAL sleep.
    Unmanaged/dead sessions return PaneReport(name, "dead", None, "").
    """
    first = _first_snapshot(name, set(list_sessions()))
    if first is None:
        return PaneReport(name, "dead", None, "")
    try:
        hint = mesh.detect_blocked(name)
    except SessionNotFoundError:
        return PaneReport(name, "dead", None, "")
    if hint:
        return PaneReport(name, "blocked", hint, _last_nonempty(first))
    time.sleep(SETTLE_INTERVAL)
    state, _, digest = _classify(name, first)
    return PaneReport(name, state, None, digest)


def recommend(names: list[str] | None = None) -> list[Advisory]:
    """Map an assessment to prioritized, advisory-only recommendations.

    Sorted by (priority, name) ascending. Spock never acts; action_hint is
    guidance for the captain only.
    """
    advisories = []
    for p in assess(names).panes:
        reason = f"blocked on {p.hint}" if p.state == "blocked" else _REASON[p.state]
        advisories.append(Advisory(p.name, _PRIORITY[p.state], reason, _ACTION[p.state]))
    return sorted(advisories, key=lambda a: (a.priority, a.name))


class Spock:
    """Public namespace for the read-only Spock API, parallel to Pty and Mesh."""

    assess = staticmethod(assess)
    diagnose = staticmethod(diagnose)
    recommend = staticmethod(recommend)
