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
    Bones,
    CaptainsLog,
    Holodeck,
    KeyParseError,
    Mesh,
    PrimeDirective,
    Pty,
    RedAlert,
    Scotty,
    SessionExistsError,
    SessionNotFoundError,
    Spock,
    Sulu,
    Transporter,
    Uhura,
    Worf,
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
_supervisors: dict[str, object] = {}  # Scotty.Supervisor instances (M9)
_recorders: dict[str, object] = {}  # CaptainsLog.Recorder instances (M12)
_alerters: dict[str, object] = {}  # RedAlert.Alerter instances (M13)
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


# ---------- Spock tools (M7) ----------
#
# Spock is the read-only science officer complement to the Kirk/mesh layer.
# Where mesh COMMANDS panes (sends keys, pipes, steers), Spock NEVER sends
# keystrokes and NEVER mutates a pane — it only OBSERVES the fleet and returns
# a deterministic, token-cheap assessment so the captain doesn't have to read
# N raw screens. All three tools are advisory: action_hint says what *could*
# be done, but Spock will never do it.


@mcp.tool()
def spock_assess(names: list[str] | None = None) -> dict:
    """Survey the whole fleet read-only and return a structured assessment.

    For each pane, determines state — "dead" (unmanaged/gone), "blocked"
    (stalled on an interactive prompt), "busy" (screen changing), or
    "idle" (screen settled) — plus a one-line digest of its last output.
    Also flags `deadlock` (>=1 pane blocked and none busy: work stalled
    waiting on you) and a one-line `summary`.

    Use this instead of snapshotting N panes individually when you just
    need to know who needs attention. `names=None` covers all managed
    sessions; pass a list to restrict scope (unknown names report "dead").
    Spock is read-only: it observes, it never sends keystrokes.

    Returns {"panes": [{"name","state","hint","digest"}, ...],
             "deadlock": bool, "summary": str} (hint is "" when absent).
    """
    report = Spock.assess(names)
    return {
        "panes": [
            {
                "name": p.name,
                "state": p.state,
                "hint": p.hint or "",
                "digest": p.digest,
            }
            for p in report.panes
        ],
        "deadlock": report.deadlock,
        "summary": report.summary,
    }


@mcp.tool()
def spock_diagnose(name: str) -> dict:
    """Deep read-only analysis of a single pane.

    Same state logic as spock_assess (dead/blocked/busy/idle) applied to
    one session, using that pane's own short settle window to tell busy
    from idle. Use when spock_assess flagged a pane and you want its
    detail (the blocked-prompt hint, the last output line) without reading
    the full screen. An unmanaged/dead session returns state "dead".
    Spock is read-only: this never touches the pane.

    Returns {"name","state","hint","digest"} (hint is "" when absent).
    """
    try:
        p = Spock.diagnose(name)
    except SessionNotFoundError as e:
        raise ValueError(str(e))
    return {
        "name": p.name,
        "state": p.state,
        "hint": p.hint or "",
        "digest": p.digest,
    }


@mcp.tool()
def spock_recommend(names: list[str] | None = None) -> dict:
    """Assess the fleet, then return prioritized advisories on what to do.

    Maps each pane to an Advisory sorted most-urgent-first by priority
    (0=blocked, 1=dead, 2=idle, 3=busy) then name, each with a `reason`
    and an `action_hint` (e.g. "respond to the prompt", "session died —
    respawn or remove", "collect output / send next instruction",
    "working — no action needed").

    Use this when you want a ranked to-do list rather than raw state.
    The action_hint is ADVISORY ONLY — Spock recommends but never acts;
    you (the captain) decide and execute via the pty_/mesh_ tools.

    Returns {"advisories": [{"name","priority","reason","action_hint"}, ...]}.
    """
    advisories = Spock.recommend(names)
    return {
        "advisories": [
            {
                "name": a.name,
                "priority": a.priority,
                "reason": a.reason,
                "action_hint": a.action_hint,
            }
            for a in advisories
        ]
    }


# ---------- Uhura tools (M8) ----------
#
# Uhura is the communications officer: a structured request/response handshake
# built ON TOP OF mesh.send_with_done. ACTUATOR — it sends keystrokes into
# panes. `ask` frames a request with an end-with-marker instruction and returns
# the reply (optionally parsed as JSON); `broadcast` fans the same request out
# to many panes concurrently.


@mcp.tool()
def uhura_ask(
    name: str,
    request: str,
    done_marker: str = "<<END>>",
    timeout: float = 60.0,
    want_json: bool = False,
) -> str | dict | list:
    """Send a framed request to one pane and return its reply.

    Uhura wraps `request` with a standard "end your reply with <done_marker>"
    instruction, then sends it and waits for the marker. Use this instead of
    raw mesh_send_with_done when you want the clean framed contract and/or a
    JSON reply. If `want_json` is true, the first JSON object/array in the
    reply is parsed and returned as a dict/list; on parse failure you get
    {"_raw": <reply>, "_error": <msg>} so the text is never lost.
    """
    try:
        return Uhura.ask(
            name,
            request,
            done_marker=done_marker,
            timeout=timeout,
            want_json=want_json,
        )
    except SessionNotFoundError as e:
        raise ValueError(str(e))
    except KeyParseError as e:
        raise ValueError(f"key parse error: {e}")
    except TimeoutError as e:
        raise ValueError(f"timeout: {e}")


@mcp.tool()
def uhura_broadcast(
    names: list[str],
    request: str,
    done_marker: str = "<<END>>",
    timeout: float = 60.0,
) -> dict:
    """Send the same framed request to every named pane and collect replies.

    Panes answer concurrently (one thread each). Returns a dict mapping each
    name to its reply text; a pane that times out or is dead maps to "" so one
    bad pane never sinks the whole broadcast. Use this to poll the whole fleet
    with a single question (e.g. "what's your current status?").
    """
    return Uhura.broadcast(
        names, request, done_marker=done_marker, timeout=timeout
    )


# ---------- Scotty tools (M9) ----------
#
# Scotty is the chief engineer: crash-recovery + resource budget. It keeps a
# module-level REGISTRY of spawn specs so a dead pane can be respawned exactly
# as it was, and a background Supervisor that auto-repairs registered panes.
# ACTUATOR — repair() spawns panes. The Supervisor is a stateful background
# object exposed via the registry start/stop pattern.


@mcp.tool()
def scotty_register(
    name: str,
    cmd: str | None = None,
    cwd: str | None = None,
    cols: int = 80,
    rows: int = 24,
) -> str:
    """Record the spec used to respawn `name` after a crash.

    Mirrors pty_spawn's signature: register exactly what you would spawn.
    Re-registering a name replaces its spec and resets its restart counter.
    Until a pane is registered, Scotty has no recipe to repair it from.
    Returns "ok".
    """
    Scotty.register(name, cmd=cmd, cwd=cwd, cols=cols, rows=rows)
    return "ok"


@mcp.tool()
def scotty_forget(name: str) -> str:
    """Drop `name` from Scotty's repair registry. No-op if never registered."""
    Scotty.forget(name)
    return "ok"


@mcp.tool()
def scotty_repair(name: str) -> str:
    """Respawn `name` from its registered spec if it is currently dead.

    Registered and dead -> respawn and return `name`. Registered and alive ->
    no-op, return `name`. Unregistered -> ValueError (no recipe to repair
    from). Best-effort: the respawned pane is a fresh process; only the spec
    is restored, not the dead pane's screen, scrollback, or in-memory state.
    """
    try:
        return Scotty.repair(name)
    except SessionNotFoundError as e:
        raise ValueError(str(e))
    except SessionExistsError as e:
        raise ValueError(str(e))


@mcp.tool()
def scotty_status() -> list[dict]:
    """Return the repair registry as a list of spec dicts.

    Each entry: {"name","cmd","cwd","cols","rows","restarts"} (cmd/cwd coerced
    to "" when None). `restarts` is how many times Scotty has respawned that
    pane — watch it climb toward the Supervisor's restart budget.
    """
    return [
        {
            "name": s.name,
            "cmd": s.cmd or "",
            "cwd": s.cwd or "",
            "cols": s.cols,
            "rows": s.rows,
            "restarts": s.restarts,
        }
        for s in Scotty.status()
    ]


@mcp.tool()
def scotty_over_budget(max_panes: int) -> bool:
    """True iff the live managed-pane count exceeds `max_panes`.

    The cheapest resource signal: a fleet larger than the captain can afford
    to supervise is the orchestrator-bottleneck failure mode. Strictly
    greater-than (`max_panes` panes is at-budget, not over). Use it to decide
    whether to throttle before spawning more work.
    """
    return Scotty.over_budget(max_panes)


@mcp.tool()
def scotty_supervise_start(restarts_max: int = 3, poll: float = 0.5) -> str:
    """Start a background Supervisor that auto-repairs crashed registered panes.

    Returns a supervisor id; pass it to scotty_supervise_stop when done. The
    Supervisor polls the fleet and respawns any REGISTERED pane that has died,
    up to `restarts_max` restarts per pane (a pane that dies instantly on
    spawn would otherwise loop forever). Register panes via scotty_register
    first; unregistered panes are never touched.
    """
    sup = Scotty.supervise(restarts_max=restarts_max, poll=poll)
    sid = _new_id()
    with _registry_lock:
        _supervisors[sid] = sup
    return sid


@mcp.tool()
def scotty_supervise_stop(supervisor_id: str) -> str:
    """Stop a background Supervisor started by scotty_supervise_start."""
    with _registry_lock:
        sup = _supervisors.pop(supervisor_id, None)
    if sup is None:
        raise ValueError(f"unknown supervisor_id: {supervisor_id}")
    sup.stop()
    return "ok"


# ---------- PrimeDirective tools (M10) ----------
#
# PrimeDirective is the policy / auto-approval actuator for blocked panes.
# Given a blocked pane (detected via mesh), it consults a policy and decides
# approve / deny / escalate. ACTUATOR — enforce() SENDS keystrokes into the
# pane. Security stance is hard-coded: a secrets prompt is ALWAYS escalated.
# The policy is selected by name ("conservative" or "permissive") since MCP
# tools take JSON-able args, not Policy objects.


def _policy_for(policy: str) -> object:
    p = (policy or "conservative").lower()
    if p == "permissive":
        return PrimeDirective.Policy.permissive()
    if p == "conservative":
        return PrimeDirective.Policy.conservative()
    raise ValueError(f"unknown policy {policy!r}: use 'conservative' or 'permissive'")


@mcp.tool()
def prime_directive_resolve(name: str, policy: str = "conservative") -> str:
    """Decide what to do about a blocked pane WITHOUT acting. Returns decision.

    "none" -> pane is not blocked. "escalate" -> defer to the human (also:
    secrets, always). "approve"/"deny" -> a policy rule matched. `policy` is
    "conservative" (escalate everything; the safe baseline) or "permissive"
    (auto-approve ordinary y/n / continue / approval prompts, still escalating
    secrets and anything unmatched). Use this to preview a decision.
    """
    try:
        return PrimeDirective.resolve(name, _policy_for(policy))
    except SessionNotFoundError as e:
        raise ValueError(str(e))


@mcp.tool()
def prime_directive_enforce(
    name: str,
    policy: str = "conservative",
    approve_keys: str = "y<Enter>",
    deny_keys: str = "n<Enter>",
) -> str:
    """Resolve a decision for a blocked pane and ACT on it. Returns decision.

    "approve" sends `approve_keys`, "deny" sends `deny_keys`, "escalate"/"none"
    do nothing (the human handles it). `policy` is "conservative" or
    "permissive". A secrets prompt is ALWAYS escalated regardless of policy —
    PrimeDirective never auto-answers a password/passphrase/2fa prompt. Use to
    clear benign approval prompts so the fleet keeps moving.
    """
    try:
        return PrimeDirective.enforce(
            name,
            _policy_for(policy),
            approve_keys=approve_keys,
            deny_keys=deny_keys,
        )
    except SessionNotFoundError as e:
        raise ValueError(str(e))
    except KeyParseError as e:
        raise ValueError(f"key parse error: {e}")


# ---------- Sulu tools (M11) ----------
#
# Sulu is the helmsman / dispatcher: hand it a backlog of commands and a pool
# of candidate panes and it routes each command to an IDLE pane, runs it framed
# with a done-marker, and collects the replies. ACTUATOR — it sends keystrokes.
# This attacks the orchestrator-bottleneck failure mode (hand off a backlog
# instead of hand-feeding each pane).


@mcp.tool()
def sulu_dispatch(
    commands: list[str],
    names: list[str] | None = None,
    done_marker: str = "<<END>>",
    timeout: float = 60.0,
    poll: float = 0.2,
) -> dict:
    """Auto-assign a backlog of commands to idle panes and collect replies.

    Each command is routed to an IDLE pane (found via Spock), run framed so its
    output ends with `done_marker`, and its reply collected. With more commands
    than idle panes, the overflow queues and assigns as panes free up. `names`
    is the candidate pool (None -> all managed sessions). Returns a dict mapping
    each command to its reply text; a command that never gets a free pane within
    `timeout` maps to "". Use for fast, deterministic, self-terminating work.
    """
    return Sulu.dispatch(
        commands,
        names=names,
        done_marker=done_marker,
        timeout=timeout,
        poll=poll,
    )


# ---------- CaptainsLog tools (M12) ----------
#
# Captain's Log is the read-only flight recorder: a background Recorder
# snapshots watched sessions on a poll loop and appends each changed screen to
# a transcript (in memory and, if a path is given, to a jsonl file). It NEVER
# sends a keystroke. The Recorder is a stateful background object exposed via
# the registry start/stop pattern; replay reads a transcript file back.


@mcp.tool()
def captains_log_start(
    names: list[str] | None = None,
    path: str | None = None,
    interval: float = 0.5,
) -> str:
    """Start recording watched sessions to memory (and optionally a jsonl file).

    Returns a recorder id; pass it to captains_log_stop when done. `names=None`
    records all managed sessions (re-resolved each tick, so newborn sessions are
    picked up); a list pins the watch set. Recording is deduped — an unchanged
    screen produces no new entry. Use for audit + replay of a multi-agent run
    without paying tokens to read every screen every turn.
    """
    rec = CaptainsLog.start(names=names, path=path, interval=interval)
    rid = _new_id()
    with _registry_lock:
        _recorders[rid] = rec
    return rid


@mcp.tool()
def captains_log_stop(recorder_id: str) -> list[dict]:
    """Stop a recorder and return its captured entries.

    Each entry: {"timestamp","name","screen"}. The background thread is stopped
    and the recorder dropped from the registry. Read the returned transcript to
    reconstruct what each pane showed and when.
    """
    with _registry_lock:
        rec = _recorders.pop(recorder_id, None)
    if rec is None:
        raise ValueError(f"unknown recorder_id: {recorder_id}")
    rec.stop()
    return [
        {"timestamp": e.timestamp, "name": e.name, "screen": e.screen}
        for e in rec.entries
    ]


@mcp.tool()
def captains_log_replay(path: str) -> list[dict]:
    """Parse a jsonl transcript file back into a list of entry dicts.

    The inverse of the file a running recorder writes. Each entry:
    {"timestamp","name","screen"}, returned in capture order. Use to replay a
    past run's transcript after the recorder has stopped.
    """
    return [
        {"timestamp": e.timestamp, "name": e.name, "screen": e.screen}
        for e in CaptainsLog.replay(path)
    ]


# ---------- RedAlert tools (M13) ----------
#
# RedAlert is escalation to the human: it watches the fleet (via Spock) and
# fires a notification the moment a deadlock or a dead pane appears. Read-only
# on panes; the only side effect is the notification (desktop toast via
# notify-send, else stderr). The Alerter is a stateful background object via the
# registry start/stop pattern; `check` is a one-shot synchronous probe.


@mcp.tool()
def red_alert_check(names: list[str] | None = None) -> dict:
    """Inspect the fleet once; return an Alert dict if a human is needed.

    Returns {"kind","detail","names"} where kind is "deadlock" (>=1 blocked
    pane and nothing busy — the fleet is stalled on you) or "death" (>=1 dead
    pane). Returns an empty dict {} when the fleet is fine. Use this for a
    one-shot health probe; use red_alert_notify_start for continuous watching.
    """
    alert = RedAlert.check(names)
    if alert is None:
        return {}
    return {"kind": alert.kind, "detail": alert.detail, "names": alert.names}


@mcp.tool()
def red_alert_notify(message: str) -> str:
    """Fire one notification now (desktop toast via notify-send, else stderr).

    A manual escalation channel: surface an arbitrary message to the human
    immediately, independent of the fleet-watching loop. Returns "ok".
    """
    RedAlert.notify(message)
    return "ok"


@mcp.tool()
def red_alert_notify_start(names: list[str] | None = None, poll: float = 0.5) -> str:
    """Start a background Alerter that notifies the human on a new fleet alert.

    Returns an alerter id; pass it to red_alert_notify_stop when done. The
    Alerter polls the fleet and fires a desktop/stderr notification on each NEW
    deadlock or dead-pane alert (deduped against the previous one). Use so a
    deadlock or crash gets a human's attention immediately instead of sitting
    unnoticed for several turns.
    """
    al = RedAlert.watch(names=names, poll=poll)
    aid = _new_id()
    with _registry_lock:
        _alerters[aid] = al
    return aid


@mcp.tool()
def red_alert_notify_stop(alerter_id: str) -> str:
    """Stop a background Alerter started by red_alert_notify_start."""
    with _registry_lock:
        al = _alerters.pop(alerter_id, None)
    if al is None:
        raise ValueError(f"unknown alerter_id: {alerter_id}")
    al.stop()
    return "ok"


# ---------- Holodeck tools (M14) ----------
#
# Holodeck provides sandboxed panes backed by git worktrees: each simulation is
# an isolated worktree with its own pane, so N agents make non-overlapping
# changes against the same repo without trampling each other. ACTUATOR — it
# runs git and spawns/kills panes.


@mcp.tool()
def holodeck_create(
    name: str,
    base: str | None = None,
    branch: str | None = None,
    cmd: str | None = None,
    cols: int = 80,
    rows: int = 24,
) -> str:
    """Create an isolated git-worktree sandbox and spawn a pane inside it.

    `base` is the repo directory to branch off (default: cwd; must be a git
    repo). `branch` starts a new branch (`-b`), else the worktree is detached.
    A pane named `name` is spawned in the fresh worktree. Use this for the
    worktree-swarm pattern — several agents editing the same repo in isolation.
    Returns `name`.
    """
    try:
        return Holodeck.create(
            name, base=base, branch=branch, cmd=cmd, cols=cols, rows=rows
        )
    except (SessionExistsError, RuntimeError) as e:
        # RuntimeError: base isn't a git repo, or `git worktree add` failed.
        raise ValueError(str(e))


@mcp.tool()
def holodeck_destroy(name: str) -> str:
    """Tear down a simulation: kill the pane, remove the worktree, deregister.

    The pane is killed first (ignoring an already-dead session), then the
    worktree is removed with --force. Always call this when finished with a
    holodeck so worktrees and panes don't leak. ValueError if `name` was never
    created by Holodeck. Returns "ok".
    """
    try:
        Holodeck.destroy(name)
    except KeyError:
        raise ValueError(f"no holodeck simulation named {name!r}")
    except Exception as e:
        raise ValueError(str(e))
    return "ok"


@mcp.tool()
def holodeck_list() -> list[str]:
    """Return the names of active holodeck simulations, sorted."""
    return Holodeck.list()


# ---------- Bones tools (M15) ----------
#
# Bones is the ship's doctor: read-only health pathology over a pane. Where
# Spock reports a coarse state (dead/blocked/idle/busy), Bones diagnoses
# *sickness* in a running pane — errors on screen, a thrashing loop, a hung
# mid-task pane. Like Spock, it NEVER sends a keystroke and NEVER mutates a
# pane; it only observes.


@mcp.tool()
def bones_examine(name: str) -> dict:
    """Diagnose a single pane's health, read-only. healthy == no symptoms.

    Returns {"name","healthy","symptoms"}. Symptoms (best-effort, screen-only):
    "dead" (gone), "errors" (error signature on screen), "thrashing" (one line
    repeated many times), "hung" (screen unchanged across a short settle window
    and not at a ready prompt). Use when you suspect a pane is sick but still
    technically alive. Bones never touches the pane.
    """
    d = Bones.examine(name)
    return {"name": d.name, "healthy": d.healthy, "symptoms": d.symptoms}


@mcp.tool()
def bones_triage(names: list[str] | None = None) -> list[dict]:
    """Examine panes and return them sickest-first, read-only.

    `names=None` covers all managed sessions; a list restricts scope (an
    unmanaged name diagnoses as "dead"). Each entry:
    {"name","healthy","symptoms"}, sorted dead-worst then by descending symptom
    count then name. Use to triage the whole fleet's health in one call instead
    of examining panes individually. Bones never touches a pane.
    """
    return [
        {"name": d.name, "healthy": d.healthy, "symptoms": d.symptoms}
        for d in Bones.triage(names)
    ]


# ---------- Transporter tools (M16) ----------
#
# Transporter checkpoints and restores a pane's VISIBLE CONTEXT (rendered
# screen + recoverable spawn spec) to/from a JSON file. NOT process migration:
# restore is a fresh pane running the same command in the same directory, with
# the captured screen returned to the caller as context. ACTUATOR — beam_in
# spawns a pane.


@mcp.tool()
def transporter_beam_out(name: str, path: str) -> str:
    """Checkpoint a pane's visible context to `path` (JSON). Return `path`.

    Captures the rendered screen plus best-effort spawn metadata (cmd/cwd may
    be None) so the pane's context can be restored later via
    transporter_beam_in. ValueError if the pane is dead — you cannot checkpoint
    nothing. Note this saves context, not live process state.
    """
    try:
        return Transporter.beam_out(name, path)
    except SessionNotFoundError as e:
        raise ValueError(str(e))


@mcp.tool()
def transporter_beam_in(
    name: str,
    path: str,
    cmd: str | None = None,
    cols: int | None = None,
    rows: int | None = None,
) -> str:
    """Restore a checkpoint into a NEW pane named `name`. Return `name`.

    Spawns a fresh pane from the saved spec; explicit `cmd`/`cols`/`rows` here
    override the stored values, and the stored cwd is reused. This does NOT type
    the captured screen back in (that would replay old output as keystrokes) —
    call transporter_load to read the screen and feed it back as context
    yourself. ValueError if `name` is already live.
    """
    try:
        return Transporter.beam_in(name, path, cmd=cmd, cols=cols, rows=rows)
    except SessionExistsError as e:
        raise ValueError(str(e))
    except FileNotFoundError:
        raise ValueError(f"no checkpoint file at {path!r}")
    except (KeyError, ValueError) as e:
        raise ValueError(f"malformed checkpoint {path!r}: {e}")


@mcp.tool()
def transporter_load(path: str) -> dict:
    """Load a checkpoint written by transporter_beam_out, without spawning.

    Returns {"name","screen","cmd","cwd","cols","rows","timestamp"} (None
    fields coerced to ""). Use to inspect a checkpoint's captured screen and
    spec — e.g. to feed the screen back as context after a beam_in restore.
    """
    cp = Transporter.load(path)
    return {
        "name": cp.name,
        "screen": cp.screen,
        "cmd": cp.cmd or "",
        "cwd": cp.cwd or "",
        "cols": cp.cols if cp.cols is not None else "",
        "rows": cp.rows if cp.rows is not None else "",
        "timestamp": cp.timestamp,
    }


# ---------- Worf tools (M17) ----------
#
# Worf is tactical / adversarial review: spin up an INDEPENDENT reviewer pane
# that shares no context with the target, feed it the target pane's current
# content, and get back a verdict. The independence is the point — a fresh
# reviewer is a stronger critic than self-review. ACTUATOR — it spawns and
# drives a reviewer pane.


@mcp.tool()
def worf_review(
    target_name: str,
    instruction: str,
    reviewer_name: str = "worf-reviewer",
    reviewer_cmd: str | None = None,
    done_marker: str = "<<END>>",
    timeout: float = 60.0,
    lines: int | None = None,
) -> str:
    """Review a target pane's content with an independent reviewer pane.

    Spawns a reviewer pane (`reviewer_cmd=None` -> a plain shell; real use
    passes e.g. "claude --print --output-format text"), captures the target's
    content (full screen, or its last `lines` non-empty lines), asks the
    reviewer to review it, and returns the verdict bounded by `done_marker`.
    The reviewer is left running for follow-ups; call worf_dismiss to kill it.
    Use for adversarial review of work produced in another pane.
    """
    try:
        return Worf.review(
            target_name,
            instruction,
            reviewer_name=reviewer_name,
            reviewer_cmd=reviewer_cmd,
            done_marker=done_marker,
            timeout=timeout,
            lines=lines,
        )
    except SessionNotFoundError as e:
        raise ValueError(str(e))
    except SessionExistsError as e:
        raise ValueError(str(e))
    except KeyParseError as e:
        raise ValueError(f"key parse error: {e}")
    except TimeoutError as e:
        raise ValueError(f"timeout: {e}")


@mcp.tool()
def worf_dismiss(reviewer_name: str) -> str:
    """Kill a Worf reviewer pane. Call when done with adversarial review."""
    try:
        Worf.dismiss(reviewer_name)
    except SessionNotFoundError as e:
        raise ValueError(str(e))
    return "ok"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
