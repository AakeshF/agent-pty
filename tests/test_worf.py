"""Acceptance tests: Worf adversarial-review actuator.

Worf spins up an independent reviewer pane, feeds it a target pane's content,
and returns the verdict. Mechanics are tested against a SHELL STUB reviewer:
the reviewer is a plain bash pane, and the review instruction is crafted as a
printf that deterministically emits a canned verdict ending in the marker, so
we control both panes and the round-trip is non-flaky. A real `claude` CLI
test is marked @pytest.mark.manual.

Tests run against a real tmux server; the autouse conftest fixture cleans up.
"""

import shutil
import time

import pytest

from agent_pty import Pty
from agent_pty.session import SessionNotFoundError, list_sessions
from agent_pty.worf import Worf
from tests.conftest import TEST_SHELL


def _spawn_target(name: str, token: str) -> None:
    """Spawn a target pane and echo a recognizable token into its screen."""
    Pty.spawn(name, cmd=TEST_SHELL)
    Pty.wait_for(name, "$", timeout=3.0)
    Pty.send(name, f"echo {token}\n")
    Pty.wait_for(name, token, timeout=3.0)
    time.sleep(0.2)


# A stub "review instruction" that is actually a bash command: when the
# reviewer pane (a shell) runs it, it prints a canned verdict ending in the
# marker. send_with_done waits for the marker, so the trailing wrapper lines
# from _build_prompt don't matter — the verdict has already landed.
_STUB_INSTRUCTION = "printf 'VERDICT: ok\\n<<END>>\\n' #"


# ---------- 1. mechanics: returns the canned verdict ----------


def test_review_returns_stub_verdict():
    _spawn_target("worf-target", "REVIEW-ME-A1")
    verdict = Worf.review(
        "worf-target",
        _STUB_INSTRUCTION,
        reviewer_name="worf-rev",
        reviewer_cmd=TEST_SHELL,
        done_marker="<<END>>",
        timeout=5.0,
    )
    assert "VERDICT: ok" in verdict


# ---------- 2. mechanics: reviewer pane is spawned and left running ----------


def test_review_spawns_independent_reviewer_pane():
    _spawn_target("worf-t2", "REVIEW-ME-B2")
    assert "worf-rev2" not in list_sessions()
    Worf.review(
        "worf-t2",
        _STUB_INSTRUCTION,
        reviewer_name="worf-rev2",
        reviewer_cmd=TEST_SHELL,
        done_marker="<<END>>",
        timeout=5.0,
    )
    # The reviewer is a distinct, independent pane left running for follow-up.
    assert "worf-rev2" in list_sessions()
    assert "worf-rev2" != "worf-t2"


# ---------- 3. dismiss: kills the reviewer pane ----------


def test_dismiss_kills_reviewer_pane():
    _spawn_target("worf-t3", "REVIEW-ME-C3")
    Worf.review(
        "worf-t3",
        _STUB_INSTRUCTION,
        reviewer_name="worf-rev3",
        reviewer_cmd=TEST_SHELL,
        done_marker="<<END>>",
        timeout=5.0,
    )
    assert "worf-rev3" in list_sessions()
    Worf.dismiss("worf-rev3")
    assert "worf-rev3" not in list_sessions()


def test_dismiss_unknown_pane_raises():
    with pytest.raises(SessionNotFoundError):
        Worf.dismiss("worf-never-spawned-xyz")


# ---------- 4. lines: captures only the tail of the target ----------


def test_review_with_lines_limit_still_returns_verdict():
    # The stub verdict is independent of captured content, but exercising the
    # lines path confirms the tail-capture branch doesn't break the round-trip.
    _spawn_target("worf-t4", "REVIEW-ME-D4")
    verdict = Worf.review(
        "worf-t4",
        _STUB_INSTRUCTION,
        reviewer_name="worf-rev4",
        reviewer_cmd=TEST_SHELL,
        done_marker="<<END>>",
        timeout=5.0,
        lines=2,
    )
    assert "VERDICT: ok" in verdict


# ---------- 5. default reviewer_cmd is a shell ----------


def test_review_default_reviewer_cmd_spawns_shell():
    _spawn_target("worf-t5", "REVIEW-ME-E5")
    verdict = Worf.review(
        "worf-t5",
        _STUB_INSTRUCTION,
        reviewer_name="worf-rev5",
        done_marker="<<END>>",
        timeout=5.0,
    )
    assert "VERDICT: ok" in verdict
    assert "worf-rev5" in list_sessions()


# ---------- 6. real-claude integration (manual, opt-in) ----------


@pytest.mark.manual
@pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="claude CLI not installed",
)
def test_worf_reviews_with_real_claude():
    """Spin up a real `claude` reviewer pane and get a verdict.

    Marked @pytest.mark.manual; not in default CI. Requires:
      - claude CLI on PATH
      - valid auth (ANTHROPIC_API_KEY or `claude login` already done)
    """
    _spawn_target("worf-mt", "def add(a, b): return a - b  # bug: subtracts")
    verdict = Worf.review(
        "worf-mt",
        "You are an adversarial code reviewer. Critique the code below for bugs.",
        reviewer_name="worf-real",
        reviewer_cmd="claude --print --output-format text",
        done_marker="<<END>>",
        timeout=60.0,
    )
    assert verdict.strip(), "reviewer returned an empty verdict"
