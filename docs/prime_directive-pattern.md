# Prime Directive pattern

The policy actuator that closes a gap [Spock](spock-pattern.md) only *names*. Spock detects a deadlock — a pane blocked on a prompt while nothing else is making progress — but it is read-only: it never answers. Someone still has to decide *and act*. PrimeDirective is that decision and that action: given a blocked pane, it consults a policy and either auto-approves, auto-denies, or escalates to the human.

It is the explicit answer to the [Captain Kirk pattern](captain-kirk-pattern.md#whats-deliberately-out-of-scope)'s deferred open question:

> **Permission auto-approval.** Mesh detects a sub-agent is blocked on a permission prompt. Does it forward to the captain (slow, costs tokens) or auto-approve from a policy file (faster, security-sensitive)? Both, behind a flag.

Both — behind a `Policy`.

## The shape

Spock reports; PrimeDirective acts. Where Spock is the instrument panel, PrimeDirective is the autopilot's "should I handle this or wake the captain?" rule. It is an **actuator**: when it decides `approve`/`deny` it sends keystrokes into the pane via the core `send`. It composes on the existing primitives — `mesh.detect_blocked` for the hint, `io.send` for the answer — and adds no new core or mesh signature.

The loop it enables:

```
Spock.assess(fleet).deadlock  ->  for each blocked pane:  PrimeDirective.enforce(pane, policy)
```

The captain spends tokens only on what `enforce` returns `escalate` for. The routine `y/n` churn never reaches the orchestrator.

## How it relates to forwarding every prompt

|  | Captain tokens per prompt | Latency | Secrets handling |
|---|---|---|---|
| Forward every prompt to the captain | one round-trip *each* | a full LLM turn | captain decides (can leak into context) |
| Hard-code "always approve" | none | instant | dangerous — answers password prompts |
| `PrimeDirective.enforce` (this) | only on `escalate` | instant for policy hits | **always escalates secrets**, hard-coded |

The honest framing: when a sub-agent blocks **once**, just forward it. PrimeDirective earns its place when a long-running fleet throws the *same* mundane confirmations over and over — the "permission deadlocks" and "orchestrator becomes the bottleneck" costs from the Kirk doc compound across turns.

## Where it earns its place

- **It actuates Spock's deadlock signal.** Spock can tell the captain the fleet is stalled; PrimeDirective can *unstall* the routine cases without a captain turn. This directly mitigates the [permission-deadlocks cost](captain-kirk-pattern.md#the-honest-costs).
- **Token economics.** Only `escalate` decisions cost an orchestrator round-trip. Routine `y/n` / `continue` / `approval` prompts are answered locally. Mitigates the [orchestrator-bottleneck cost](captain-kirk-pattern.md#the-honest-costs).
- **A single, auditable trust boundary.** The whole "what may be auto-answered" decision lives in one `Policy` object a reviewer can read, instead of scattered ad-hoc approvals.
- **Conservative by default.** `policy=None` and `Policy.conservative()` escalate *everything*. You opt into automation explicitly; you never get it by accident.

## The security stance (hard-coded, non-negotiable)

A prompt whose hint contains `password` / `passphrase` / `2fa` / `verification` / `secret` is **always** resolved to `escalate`, checked *before* any policy rule. No policy — not even `Policy(rules={"password": "approve"}, default="approve")` — can make PrimeDirective type into a secrets prompt. The override is in code, not configuration, so it cannot be turned off by a permissive policy file. A human always answers for a secret.

## The honest costs

- **Decisions ride on a best-effort hint.** `resolve` is only as good as `mesh.detect_blocked` — same regex heuristic, same false positives (a `read -p "Continue?"` script) and false negatives (custom prompts). A *missed* prompt is the safe failure: PrimeDirective simply returns `none` and does nothing.
- **A false-positive hint could be auto-answered.** Under a permissive policy, a non-prompt line that happens to match the y/n heuristic could receive a stray `y<Enter>`. This is why the default is conservative and why automation is opt-in per policy.
- **It actually types.** Unlike Spock, this mutates the pane. `approve`/`deny` are fire-and-forget keystrokes; there is no read-back confirmation that the answer was accepted. Treat it as best-effort actuation, not a transaction.
- **The secrets list is substring-based.** It catches the common cases by hint, not every conceivable secret prompt. The conservative default — escalate the unknown — is the backstop.

These are real. PrimeDirective exists to take the routine confirmation churn off the captain while keeping a hard floor under anything sensitive — not to pretend the heuristics are an oracle.

## Module shape — `agent_pty.prime_directive`

Lives alongside the core, mesh, and Spock in the same package. New module file `agent_pty/prime_directive.py`. A `PrimeDirective` namespace class mirrors `Pty` / `Mesh` / `Spock` (`staticmethod` wrappers over module-level functions) and re-exposes the `Policy` dataclass as `PrimeDirective.Policy`. It imports `mesh.detect_blocked` (read) and `io.send` (actuate) — the only module in the analyst/policy tier that sends keystrokes.

## Public API

```python
@dataclass
class Policy:
    rules: dict[str, str]          # case-insensitive substring of hint -> "approve"|"deny"|"escalate"
    default: str = "escalate"      # applied when no rule matches

    @staticmethod
    def conservative() -> Policy    # escalate EVERYTHING (rules empty)
    @staticmethod
    def permissive() -> Policy      # approve y/n / continue / approval; secrets always escalate

PrimeDirective.resolve(name, policy=None) -> str
    # detect_blocked(name); no hint -> "none". Secrets -> "escalate" (override).
    # First rule whose key is a case-insensitive substring of the hint wins;
    # else policy.default. policy=None -> Policy.conservative().

PrimeDirective.enforce(name, policy=None, approve_keys="y<Enter>", deny_keys="n<Enter>") -> str
    # resolve(); "approve" -> send(approve_keys); "deny" -> send(deny_keys);
    # "escalate"/"none" -> do nothing. Returns the decision. send() parses
    # named keys, so <Enter> submits the answer.
```

### Decision precedence (deterministic)

Checked in this order — first match wins:

1. **none** — `detect_blocked` returns no hint (pane isn't blocked).
2. **escalate** — the hint looks like a secret (hard-coded override).
3. **policy rule** — first `rules` key that is a case-insensitive substring of the hint.
4. **policy.default** — otherwise.

## Status

Pattern documented and **implemented** 2026-06-13 as M10. Composes on M6 (mesh) and the frozen core (M1–M5); actuates the deadlock signal from M7 (Spock); changes no existing signature.
