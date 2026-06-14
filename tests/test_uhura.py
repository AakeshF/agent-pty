"""Acceptance tests: Uhura communications layer (structured handshake).

Uhura is an ACTUATOR built on top of mesh.send_with_done: it frames a request
(appending a standard "end your reply with <marker>" instruction), sends it,
waits for the marker, and returns the reply — optionally parsed as JSON. It
also broadcasts the same framed request to many panes concurrently.

These tests exercise the MECHANICS against bash SHELL STUBS, not a real LLM.
The framing appends an instruction line that begins with the word "When", so
each stub defines a shell function `When` that, when invoked, clears the
instruction echo off-screen and prints a canned reply followed by the marker.
That makes the marker appear exactly once (after the reply), which is the clean
contract a real sub-agent would honor. A test needing a real `claude` CLI is
marked @pytest.mark.manual (see test_mesh.py example 12).
"""

import shutil
import time

import pytest

from agent_pty import Pty
from agent_pty.io import send
from agent_pty.uhura import Uhura, ask, broadcast
from tests.conftest import TEST_SHELL

# A marker with no special shell meaning (unlike "<<END>>", which bash parses as
# a here-document operator when it lands on an executed line).
MARK = "@@DONE@@"


def _spawn_stub(name: str) -> None:
    """Spawn a quiescent bash pane and let its prompt settle."""
    Pty.spawn(name, cmd=TEST_SHELL)
    Pty.wait_for(name, "$", timeout=3.0)
    time.sleep(0.2)


def _when_def(body_printf: str) -> str:
    """A `When` function: clear the instruction echo, run body, print the marker.

    `body_printf` is a shell snippet (terminated by ';') that prints the reply
    body. The marker is assembled from '@@' halves at runtime so the literal
    marker never appears in the echoed function-definition line — only after the
    reply. `clear` wipes the instruction-line echo (and its marker) so the only
    marker on screen is the one this function prints last.
    """
    return "When() { clear; %s printf '%%sDONE%%s\\n' @@ @@; }" % body_printf


def _define_when(name: str, body_printf: str) -> None:
    """Define the `When` stub function in an already-spawned pane."""
    send(name, _when_def(body_printf) + "\n")
    time.sleep(0.2)


# ---------- 1. ask: framed round-trip returns the reply ----------


def test_ask_returns_framed_reply():
    _spawn_stub("uh-ask")
    # The whole framed request is sent: the first line defines `When`, the
    # appended instruction line (starting with "When") then invokes it.
    reply = ask("uh-ask", _when_def("printf 'pong\\n';"), done_marker=MARK, timeout=5.0)
    assert reply == "pong"


def test_ask_strips_marker_and_instruction_noise():
    _spawn_stub("uh-ask2")
    reply = ask(
        "uh-ask2",
        _when_def("printf 'hello world\\n';"),
        done_marker=MARK,
        timeout=5.0,
    )
    assert reply == "hello world"
    assert MARK not in reply


def test_ask_via_namespace_class():
    _spawn_stub("uh-ns")
    reply = Uhura.ask(
        "uh-ns", _when_def("printf 'ackd\\n';"), done_marker=MARK, timeout=5.0
    )
    assert reply == "ackd"


# ---------- 2. ask want_json: bare object ----------


def test_ask_want_json_parses_bare_object():
    _spawn_stub("uh-json")
    result = ask(
        "uh-json",
        _when_def("printf '{\"k\": 1}\\n';"),
        done_marker=MARK,
        timeout=5.0,
        want_json=True,
    )
    assert result == {"k": 1}


def test_ask_want_json_parses_fenced_block():
    _spawn_stub("uh-jsonf")
    result = ask(
        "uh-jsonf",
        _when_def("printf '```json\\n[1, 2, 3]\\n```\\n';"),
        done_marker=MARK,
        timeout=5.0,
        want_json=True,
    )
    assert result == [1, 2, 3]


def test_ask_want_json_object_inside_prose():
    _spawn_stub("uh-jsonp")
    result = ask(
        "uh-jsonp",
        _when_def("printf 'here you go: {\"ok\": true} thanks\\n';"),
        done_marker=MARK,
        timeout=5.0,
        want_json=True,
    )
    assert result == {"ok": True}


# ---------- 3. ask want_json: parse failure falls back to _raw/_error ----------


def test_ask_want_json_parse_failure_returns_raw_and_error():
    _spawn_stub("uh-bad")
    result = ask(
        "uh-bad",
        _when_def("printf 'not json at all\\n';"),
        done_marker=MARK,
        timeout=5.0,
        want_json=True,
    )
    assert isinstance(result, dict)
    assert result.get("_raw") == "not json at all"
    assert result.get("_error")


# ---------- 4. broadcast: concurrent replies keyed by name ----------


def test_broadcast_collects_replies_keyed_by_name():
    _spawn_stub("uh-b1")
    _spawn_stub("uh-b2")
    # Each pane gets a distinct `When`; broadcast sends the same framed request
    # ("When") to both, so each answers with its own canned reply.
    _define_when("uh-b1", "printf 'r1\\n';")
    _define_when("uh-b2", "printf 'r2\\n';")
    result = broadcast(["uh-b1", "uh-b2"], "When", done_marker=MARK, timeout=5.0)
    assert result == {"uh-b1": "r1", "uh-b2": "r2"}


def test_broadcast_timeout_pane_maps_to_empty_string():
    _spawn_stub("uh-ok")
    _define_when("uh-ok", "printf 'good\\n';")
    # uh-silent is never spawned -> the send/wait fails fast and maps to "".
    result = broadcast(
        ["uh-ok", "uh-never-spawned-zzz"],
        "When",
        done_marker=MARK,
        timeout=1.5,
    )
    assert result["uh-ok"] == "good"
    assert result["uh-never-spawned-zzz"] == ""


def test_broadcast_returns_one_key_per_name():
    _spawn_stub("uh-k1")
    _spawn_stub("uh-k2")
    _spawn_stub("uh-k3")
    for n in ("uh-k1", "uh-k2", "uh-k3"):
        _define_when(n, "printf 'ok\\n';")
    result = broadcast(["uh-k1", "uh-k2", "uh-k3"], "When", done_marker=MARK, timeout=5.0)
    assert set(result.keys()) == {"uh-k1", "uh-k2", "uh-k3"}


# ---------- 5. Captain-Kirk integration (manual, opt-in) ----------


@pytest.mark.manual
@pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="claude CLI not installed",
)
def test_uhura_asks_real_claude():
    """Frame a request to a real `claude` CLI and get a structured reply.

    Marked @pytest.mark.manual; not in default CI. Requires the claude CLI on
    PATH with valid auth.
    """
    Pty.spawn("uh-kirk", cmd="claude --print --output-format text", cols=120, rows=40)
    time.sleep(2)
    reply = ask(
        "uh-kirk",
        "Reply with the single word 'ack'.",
        done_marker="<<END>>",
        timeout=30.0,
    )
    assert reply.strip(), "Uhura got an empty reply from claude"
