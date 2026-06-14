"""Uhura: communications officer — a structured request/response handshake.

The honest #1 gap in the Captain Kirk pattern is that there is no structured
handshake: the captain prompts a sub-agent and then screen-scrapes for a
sentinel, which is brittle. Uhura keeps the cheap sentinel mechanic (it builds
ON TOP OF mesh.send_with_done — it does not reimplement done-detection) but
wraps it in a clean framed contract: it appends a standard instruction telling
the sub-agent how to terminate its reply, optionally parses the reply as JSON,
and offers a fleet broadcast that fans the same request out concurrently.

Uhura is an ACTUATOR: `ask` and `broadcast` send keystrokes into panes. It is
the counterpart to Spock, who only observes.

Best-effort limits: the framing is an *instruction* to the sub-agent, not an
enforced protocol — a sub-agent that ignores it (never prints the marker) will
time out, and `want_json` only succeeds if the reply actually contains JSON.
JSON extraction is heuristic (first fenced ```json block, else first balanced
{...}/[...]); on failure it returns the raw reply rather than raising.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from agent_pty import mesh

DEFAULT_DONE_MARKER = "<<END>>"


def _frame(request: str, done_marker: str) -> str:
    """Wrap a request with a standard end-your-reply-with-the-marker instruction.

    A trailing newline is appended so mesh.send_with_done submits the line.
    """
    instruction = (
        f"When you are done, end your reply with {done_marker} "
        f"on its own line."
    )
    body = request.rstrip("\n")
    return f"{body}\n{instruction}\n"


def ask(
    name: str,
    request: str,
    done_marker: str = DEFAULT_DONE_MARKER,
    timeout: float = 60.0,
    want_json: bool = False,
) -> str | dict[str, Any] | list[Any]:
    """Send a framed request to one pane and return its reply.

    Frames `request` with a standard "end your reply with <done_marker>"
    instruction, then delegates to mesh.send_with_done for the actual send +
    sentinel wait. The returned string is the reply bounded by the framing and
    the marker, trimmed.

    If `want_json`, extract the first JSON object/array from the reply (handles
    ```json fenced blocks and bare {...}/[...]), json.loads it, and return the
    resulting dict/list. On parse failure return
    {"_raw": <reply>, "_error": <msg>} so the caller still gets the text.
    """
    framed = _frame(request, done_marker)
    reply = mesh.send_with_done(name, framed, done_marker=done_marker, timeout=timeout)
    if not want_json:
        return reply
    return _parse_json(reply)


def broadcast(
    names: list[str],
    request: str,
    done_marker: str = DEFAULT_DONE_MARKER,
    timeout: float = 60.0,
) -> dict[str, str]:
    """Send the same framed request to every pane and collect replies by name.

    Panes answer concurrently (one thread each). A pane that times out
    (TimeoutError from the underlying wait) maps to "" in the result. Any other
    exception (e.g. SessionNotFoundError) also resolves to "" so one bad pane
    never sinks the whole broadcast. The returned dict has one key per name.
    """
    replies: dict[str, str] = {}
    lock = threading.Lock()

    def _worker(target: str) -> None:
        try:
            r = ask(target, request, done_marker=done_marker, timeout=timeout)
        except Exception:
            # TimeoutError (sentinel never arrived) or a dead pane -> empty.
            r = ""
        with lock:
            replies[target] = r if isinstance(r, str) else ""

    threads = [threading.Thread(target=_worker, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return replies


# ---------- JSON extraction ----------


def _parse_json(reply: str) -> dict[str, Any] | list[Any]:
    """Extract and parse the first JSON object/array in `reply`.

    Tries a ```json (or bare ```) fenced block first, then the first balanced
    {...} or [...] span. Returns the parsed value, or
    {"_raw": reply, "_error": msg} if nothing parses.
    """
    candidate = _extract_json_text(reply)
    if candidate is None:
        return {"_raw": reply, "_error": "no JSON object or array found in reply"}
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        return {"_raw": reply, "_error": f"JSON parse failed: {exc}"}


def _extract_json_text(reply: str) -> str | None:
    fenced = _fenced_block(reply)
    if fenced is not None:
        return fenced
    return _balanced_span(reply)


def _fenced_block(reply: str) -> str | None:
    """Return the contents of the first ```json ... ``` (or ``` ... ```) block."""
    fence = "```"
    start = reply.find(fence)
    if start == -1:
        return None
    after = start + len(fence)
    newline = reply.find("\n", after)
    if newline == -1:
        return None
    lang = reply[after:newline].strip().lower()
    if lang and lang != "json":
        # A non-JSON fenced block; don't trust it.
        return None
    end = reply.find(fence, newline + 1)
    if end == -1:
        return None
    return reply[newline + 1:end].strip() or None


def _balanced_span(reply: str) -> str | None:
    """Return the first balanced {...} or [...] span, respecting strings."""
    opens = {"{": "}", "[": "]"}
    start = -1
    opener = ""
    for i, ch in enumerate(reply):
        if ch in opens:
            start = i
            opener = ch
            break
    if start == -1:
        return None
    closer = opens[opener]
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(reply)):
        ch = reply[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return reply[start:i + 1]
    return None


class Uhura:
    """Public namespace for the Uhura API, parallel to Pty, Mesh and Spock."""

    ask = staticmethod(ask)
    broadcast = staticmethod(broadcast)
