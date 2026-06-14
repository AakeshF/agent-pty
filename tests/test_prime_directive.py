"""Acceptance tests: PrimeDirective policy / auto-approval actuator.

PrimeDirective is the ACTUATOR for Spock's deadlock detection: given a blocked
pane it consults a Policy and either auto-approves (sends keys), auto-denies, or
escalates to the human. These tests drive real bash panes; the autouse conftest
fixture handles cleanup. A hard-coded security override always escalates a
secrets prompt, even under a permissive policy.
"""

import time

from agent_pty import Pty
from agent_pty.prime_directive import Policy, PrimeDirective
from tests.conftest import TEST_SHELL


def _spawn_yn_prompt(name: str) -> None:
    """Spawn a pane sitting on a `read -p 'Continue? [y/N] '` prompt."""
    Pty.spawn(name, cmd=TEST_SHELL)
    Pty.wait_for(name, "$", timeout=3.0)
    Pty.send(name, "read -p 'Continue? [y/N] ' x; echo GOT=$x\n")
    _wait_blocked(name)


def _spawn_secret_prompt(name: str) -> None:
    """Spawn a pane sitting on a password-style prompt."""
    Pty.spawn(name, cmd=TEST_SHELL)
    Pty.wait_for(name, "$", timeout=3.0)
    Pty.send(name, "read -p 'Password: ' p; echo DONE=$p\n")
    _wait_blocked(name)


def _wait_blocked(name: str, deadline_s: float = 2.0) -> str:
    """Poll PrimeDirective.resolve() under permissive policy until it is not
    'none' (i.e. detect_blocked sees the prompt). Returns the decision."""
    deadline = time.monotonic() + deadline_s
    decision = PrimeDirective.resolve(name, Policy.permissive())
    while time.monotonic() < deadline and decision == "none":
        time.sleep(0.05)
        decision = PrimeDirective.resolve(name, Policy.permissive())
    return decision


# ---------- 1. conservative: escalate, do not answer ----------


def test_conservative_resolves_escalate():
    _spawn_yn_prompt("pd-cons")
    assert PrimeDirective.resolve("pd-cons", Policy.conservative()) == "escalate"


def test_conservative_enforce_does_not_answer():
    _spawn_yn_prompt("pd-cons2")
    before = Pty.snapshot("pd-cons2")
    decision = PrimeDirective.enforce("pd-cons2", Policy.conservative())
    assert decision == "escalate"
    # Give any (erroneous) keystroke time to land, then confirm nothing changed.
    time.sleep(0.3)
    after = Pty.snapshot("pd-cons2")
    assert after == before, "conservative policy must not type into the pane"
    # The command echo contains "GOT=$x"; a *completed* read would echo the
    # answered value (e.g. "GOT=y"). Neither auto-answer must have happened.
    assert "GOT=y" not in after and "GOT=n" not in after, (
        "the read should still be blocked, not completed"
    )


def test_default_policy_is_conservative():
    # policy=None must behave like Policy.conservative() -> escalate.
    _spawn_yn_prompt("pd-default")
    assert PrimeDirective.resolve("pd-default") == "escalate"


# ---------- 2. permissive: approve and complete the read ----------


def test_permissive_resolves_approve():
    _spawn_yn_prompt("pd-perm")
    assert PrimeDirective.resolve("pd-perm", Policy.permissive()) == "approve"


def test_permissive_enforce_completes_the_read():
    _spawn_yn_prompt("pd-perm2")
    decision = PrimeDirective.enforce("pd-perm2", Policy.permissive())
    assert decision == "approve"
    # The read should now have consumed 'y' and echoed GOT=y.
    Pty.wait_for("pd-perm2", "GOT=", timeout=3.0)
    snap = Pty.snapshot("pd-perm2")
    assert "GOT=y" in snap, f"expected GOT=y in pane, got:\n{snap}"


# ---------- 3. deny: answer 'n' ----------


def test_deny_enforce_sends_no():
    _spawn_yn_prompt("pd-deny")
    policy = Policy(rules={"y/n": "deny"})
    decision = PrimeDirective.enforce("pd-deny", policy)
    assert decision == "deny"
    Pty.wait_for("pd-deny", "GOT=", timeout=3.0)
    snap = Pty.snapshot("pd-deny")
    # 'n<Enter>' answered the read; GOT echoes back the 'n'.
    assert "GOT=n" in snap, f"expected GOT=n in pane, got:\n{snap}"


# ---------- 4. secrets override: always escalate, even permissive ----------


def test_secret_prompt_overrides_permissive_to_escalate():
    _spawn_secret_prompt("pd-secret")
    # Even the most permissive policy must refuse to answer a secrets prompt.
    assert PrimeDirective.resolve("pd-secret", Policy.permissive()) == "escalate"


def test_secret_prompt_enforce_does_not_answer():
    _spawn_secret_prompt("pd-secret2")
    before = Pty.snapshot("pd-secret2")
    # A policy that would approve everything by hint still must not answer.
    policy = Policy(rules={"password": "approve"}, default="approve")
    decision = PrimeDirective.enforce("pd-secret2", policy)
    assert decision == "escalate", "secrets override must beat any rule"
    time.sleep(0.3)
    after = Pty.snapshot("pd-secret2")
    assert after == before, "secrets prompt must never be auto-answered"
    # The command echo contains "DONE=$p"; a *completed* read would echo the
    # answered value (e.g. "DONE=y"). It must never be auto-answered.
    assert "DONE=y" not in after, "the password read should still be blocked"


# ---------- 5. not-blocked pane -> 'none', no action ----------


def test_unblocked_pane_resolves_none():
    Pty.spawn("pd-none", cmd=TEST_SHELL)
    Pty.wait_for("pd-none", "$", timeout=3.0)
    time.sleep(0.2)
    assert PrimeDirective.resolve("pd-none", Policy.permissive()) == "none"


def test_unblocked_pane_enforce_does_nothing():
    Pty.spawn("pd-none2", cmd=TEST_SHELL)
    Pty.wait_for("pd-none2", "$", timeout=3.0)
    time.sleep(0.2)
    before = Pty.snapshot("pd-none2")
    decision = PrimeDirective.enforce("pd-none2", Policy.permissive())
    assert decision == "none"
    time.sleep(0.2)
    after = Pty.snapshot("pd-none2")
    assert after == before


# ---------- 6. policy semantics: default + first-match ----------


def test_default_applies_when_no_rule_matches():
    _spawn_yn_prompt("pd-def")
    # No rule matches "y/n confirmation"; default deny wins (secrets aside).
    policy = Policy(rules={"never-matches-xyz": "approve"}, default="deny")
    assert PrimeDirective.resolve("pd-def", policy) == "deny"


def test_namespace_exposes_policy():
    # PrimeDirective.Policy must be the Policy dataclass.
    assert PrimeDirective.Policy is Policy
    cons = PrimeDirective.Policy.conservative()
    assert cons.default == "escalate" and cons.rules == {}
