"""PrimeDirective: policy / auto-approval actuator for blocked panes.

Spock detects a deadlock (a pane blocked on a prompt while nothing is busy);
the captain still has to *decide* what to do about it. PrimeDirective is that
decision and the action: given a blocked pane, it consults a Policy and either
auto-approves, auto-denies, or escalates to the human. It is the actuator for
the M6 open question "Permission auto-approval" — forward to the captain (slow,
costs tokens) or auto-approve from a policy (faster, security-sensitive). Both,
behind a policy.

This is an ACTUATOR: when it decides "approve"/"deny" it SENDS keystrokes into
the pane via the core `send`. Use deliberately.

Security stance (hard-coded, non-negotiable): a prompt that looks like it wants
a secret (password / passphrase / 2fa / verification / secret) is ALWAYS
escalated, even under a permissive policy. PrimeDirective NEVER auto-answers a
secrets prompt. Decisions ride on `mesh.detect_blocked`'s best-effort hint, so
this is a convenience-and-safety net, not an oracle — a missed prompt simply
isn't acted on, and the conservative default escalates everything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_pty import mesh
from agent_pty.io import send

# Substrings that, if present in the blocked-hint (case-insensitive), force an
# escalate regardless of policy. These mirror mesh's secret-bearing prompts.
_SECRET_MARKERS = ("password", "passphrase", "2fa", "verification", "secret")

# Hints permissive() will auto-approve (matched as case-insensitive substrings
# of the blocked-hint). detect_blocked emits e.g. "y/n confirmation",
# "continue prompt", "approval prompt".
_PERMISSIVE_RULES = {
    "y/n": "approve",
    "continue": "approve",
    "approval": "approve",
}

_DECISIONS = ("approve", "deny", "escalate")


@dataclass
class Policy:
    """A blocked-hint -> decision policy.

    `rules` maps a case-insensitive substring of the blocked-hint to one of
    "approve" | "deny" | "escalate". The first matching rule wins (dict
    insertion order). `default` applies when no rule matches.

    Secrets always override to "escalate" — they are checked before `rules`
    and cannot be approved/denied by any rule.
    """

    rules: dict[str, str] = field(default_factory=dict)
    default: str = "escalate"

    def __post_init__(self) -> None:
        # A decision the actuator can't act on (typo'd "aprove") would be
        # silently ignored by enforce() — fail loudly at construction instead.
        for decision in (*self.rules.values(), self.default):
            if decision not in _DECISIONS:
                raise ValueError(
                    f"invalid decision {decision!r}; expected one of {_DECISIONS}"
                )

    @staticmethod
    def conservative() -> "Policy":
        """Escalate EVERYTHING. No rules; default escalate. The safe baseline."""
        return Policy(rules={}, default="escalate")

    @staticmethod
    def permissive() -> "Policy":
        """Auto-approve ordinary y/n / continue / approval prompts.

        Still escalates anything unmatched, and ALWAYS escalates a secrets
        prompt (the hard-coded override applies on top of any policy).
        """
        return Policy(rules=dict(_PERMISSIVE_RULES), default="escalate")


def _is_secret(hint: str) -> bool:
    low = hint.lower()
    return any(marker in low for marker in _SECRET_MARKERS)


def resolve(name: str, policy: Policy | None = None) -> str:
    """Decide what to do about pane `name`. Returns the decision string.

    "none"     -> pane is not blocked (mesh.detect_blocked found no hint).
    "escalate" -> defer to the human/captain (also: secrets, always).
    "approve" / "deny" -> a policy rule matched.

    policy=None uses Policy.conservative() (escalate everything). The match is
    a case-insensitive substring test of the blocked-hint against policy.rules,
    first rule wins; secrets short-circuit to "escalate"; otherwise the
    policy.default applies.

    Raises SessionNotFoundError if `name` is not a live session (it reads the
    screen via mesh.detect_blocked); a pane that dies mid-call surfaces the same
    way. enforce() inherits this for the same reason.
    """
    policy = policy or Policy.conservative()
    hint = mesh.detect_blocked(name)
    if not hint:
        return "none"
    if _is_secret(hint):
        return "escalate"
    low = hint.lower()
    for substring, decision in policy.rules.items():
        if substring.lower() in low:
            return decision
    return policy.default


def enforce(
    name: str,
    policy: Policy | None = None,
    approve_keys: str = "y<Enter>",
    deny_keys: str = "n<Enter>",
) -> str:
    """Resolve a decision for `name` and ACT on it. Returns the decision.

    "approve" -> send `approve_keys` into the pane.
    "deny"    -> send `deny_keys` into the pane.
    "escalate" / "none" -> do nothing (the caller/human handles it).

    Keys are sent via the core `send`, which DOES parse named-key tokens like
    <Enter> — intended here so the answer is actually submitted.
    """
    decision = resolve(name, policy)
    if decision == "approve":
        send(name, approve_keys)
    elif decision == "deny":
        send(name, deny_keys)
    return decision


class PrimeDirective:
    """Public namespace for the PrimeDirective API, parallel to Pty/Mesh/Spock."""

    Policy = Policy
    resolve = staticmethod(resolve)
    enforce = staticmethod(enforce)
