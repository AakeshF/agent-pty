import time

import pytest

from agent_pty import KeyParseError, Pty
from agent_pty.keys import parse
from tests.conftest import TEST_SHELL


def test_parse_plain_text():
    assert parse("hello") == [("text", "hello")]


def test_parse_named_key():
    assert parse("<Enter>") == [("key", "Enter")]


def test_parse_mixed():
    assert parse("hi<Enter>bye") == [
        ("text", "hi"),
        ("key", "Enter"),
        ("text", "bye"),
    ]


def test_parse_double_lt_is_literal():
    assert parse("a<<b") == [("text", "a<b")]


def test_parse_unknown_token_raises():
    with pytest.raises(KeyParseError):
        parse("<NotARealKey>")


def test_parse_empty_token_raises():
    with pytest.raises(KeyParseError):
        parse("<>")


def test_parse_unterminated_raises():
    with pytest.raises(KeyParseError):
        parse("hello<Enter")


def test_parse_modifier_keys():
    assert parse("<C-c>") == [("key", "C-c")]
    assert parse("<S-Tab>") == [("key", "S-Tab")]
    assert parse("<M-x>") == [("key", "M-x")]


def test_parse_function_keys():
    assert parse("<F1>") == [("key", "F1")]
    assert parse("<F12>") == [("key", "F12")]


def test_parse_aliases():
    assert parse("<CR>") == [("key", "Enter")]
    assert parse("<BS>") == [("key", "BSpace")]
    assert parse("<PgUp>") == [("key", "PageUp")]
    assert parse("<Del>") == [("key", "DC")]


def test_named_keys_drive_vim(tmp_path):
    target = tmp_path / "test.txt"
    Pty.spawn("v", cmd=f"vim {target}")
    time.sleep(0.8)
    Pty.send("v", "ihello vim world<Esc>:wq<Enter>")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if target.exists() and target.read_text().strip() == "hello vim world":
            return
        time.sleep(0.1)
    contents = target.read_text() if target.exists() else "<missing>"
    pytest.fail(f"vim did not save expected content; got: {contents!r}")


def test_named_keys_up_arrow_history():
    Pty.spawn("p", cmd="python3 -q")
    Pty.wait_for("p", ">>>", timeout=5.0)
    Pty.send("p", "print('hist-x-marker')\n")
    Pty.wait_for("p", "hist-x-marker", timeout=3.0)
    Pty.send("p", "<Up><Enter>")
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if Pty.snapshot("p").count("hist-x-marker") >= 3:
            return
        time.sleep(0.1)
    pytest.fail(
        f"Up arrow did not recall; count: "
        f"{Pty.snapshot('p').count('hist-x-marker')}"
    )


def test_named_keys_ctrl_c_interrupts():
    Pty.spawn("t1", cmd=TEST_SHELL)
    Pty.send("t1", "sleep 60\n")
    time.sleep(0.3)
    start = time.monotonic()
    Pty.send("t1", "<C-c>")
    Pty.send("t1", "echo after-int-marker\n")
    Pty.wait_for("t1", "after-int-marker", timeout=3.0)
    elapsed = time.monotonic() - start
    assert elapsed < 3.0, f"interrupt took {elapsed:.2f}s, expected <3s"


def test_double_lt_renders_as_literal():
    Pty.spawn("t1", cmd=TEST_SHELL)
    Pty.send("t1", "echo 'foo<<bar'\n")
    snap = Pty.wait_for("t1", "foo<bar", timeout=3.0)
    assert "foo<bar" in snap


def test_unknown_token_raises_on_send():
    Pty.spawn("t1", cmd=TEST_SHELL)
    with pytest.raises(KeyParseError):
        Pty.send("t1", "<NotARealKey>")
