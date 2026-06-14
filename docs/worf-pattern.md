# Worf pattern

The tactical officer: an independent adversarial reviewer on demand. Where the [Captain Kirk pattern](captain-kirk-pattern.md) lists **adversarial review** — "implementer pane + independent reviewer pane (no shared context), captain mediates, stronger than self-review" — as a headline use case, Worf is that use case collapsed into one call.

## The shape

`Worf.review(target, instruction)` spins up a *fresh* reviewer pane, captures the target pane's current screen, hands the reviewer the instruction plus that content, and returns the reviewer's verdict. The reviewer shares **no context** with the target: it never saw how the work was produced, only the artifact. That independence is the entire point — a critic with amnesia is a harder critic than the author grading their own homework.

agent-pty's core is a transport; mesh is the commander's toolkit; Spock is the read-only instrument panel; Worf is the commander's *weapon* — an ACTUATOR that spawns and drives a pane to extract a judgement. Like the rest of the crew it is opt-in and composes purely on existing primitives: `session.spawn`/`kill` for the pane, `io.snapshot` to capture the target, and `mesh.send_with_done` for the bounded round-trip. It re-implements none of them.

## How it relates to self-review and one-shot review

|  | Independent context | Iterative follow-up | Heterogeneous reviewer | Captain stays cheap |
|---|---|---|---|---|
| Self-review (same agent grades itself) | no — author bias | n/a | no | no — author burns its own tokens |
| One-shot subagent review | yes | no — one prompt, one summary | per-spawn | yes |
| `Worf.review` (this) | yes — fresh pane, no shared memory | yes — pane is left running for more questions | yes — any `reviewer_cmd` (other model/CLI) | yes — verdict only crosses back |

The honest framing: when you just want one verdict and never a follow-up, a one-shot subagent review tool is simpler. Worf earns its place when the reviewer pane is something you want to **keep** — to drill into a finding, re-review after a fix, or run a different model than the implementer — and when the target is a live pane whose screen *is* the artifact (a running test suite, a TUI, a diff on screen).

## Where it earns its place

- **Adversarial review.** The Kirk doc's named use case, in one call. Implementer works in pane A; Worf reviews pane A's output with a clean-room reviewer in pane B. Stronger than asking the implementer to check its own work.
- **Cross-model second opinion.** Pass a different `reviewer_cmd` (a different model or CLI) so the critic is not the same brain that wrote the code.
- **Cheap for the captain.** Only the verdict crosses back via `send_with_done`; the captain never pays to read the full target content again — Worf piped it pane-to-pane. This mitigates the [token-economics / orchestrator-bottleneck cost](captain-kirk-pattern.md#the-honest-costs).
- **Keep-or-dismiss control.** The reviewer pane is left running so the captain can ask follow-ups against the same context; `Worf.dismiss(name)` tears it down when finished.

## The honest costs

- **Verdict quality is the reviewer's, not Worf's.** The mechanics are deterministic; the judgement is exactly as good as the model behind `reviewer_cmd`. With a shell reviewer you get a shell's "verdict" — Worf guarantees the round-trip, never the wisdom.
- **Inherits `send_with_done`'s marker contract.** The reviewer must end its reply with `done_marker`. A reviewer that never prints the marker yields an empty verdict after `timeout`, same failure mode (and same screen-scraping caveats) as [mesh](captain-kirk-pattern.md#the-honest-costs).
- **The capture is a point-in-time screen.** Worf reviews what's *visible* (full screen, or the last `lines` non-empty lines) — not scrollback, not files. If the artifact scrolled off, it isn't reviewed. Capture the right pane at the right moment.
- **It costs a pane.** Each review spawns a real reviewer process. Reuse the pane for follow-ups, then `dismiss` it; don't spawn-and-leak one per call.

These are real. Worf brings clean-room review down to one call; it does not pretend a stub reviewer is a senior engineer.

## Module shape — `agent_pty.worf`

Lives alongside the core and the rest of the crew in the same package. New module file `agent_pty/worf.py`. A `Worf` namespace class mirrors `Pty`, `Mesh`, and `Spock` (`staticmethod` wrappers over module-level functions). Parallel MCP tools under the `worf_*` namespace are exposed by the same `agent-pty-mcp` server.

**Actuator (by design).** Unlike Spock, Worf *acts*: it spawns a pane and sends keystrokes to it via `mesh.send_with_done`. It does so only on the reviewer pane it owns — it reads the target read-only (`io.snapshot`) and never types into it.

## Public API

```python
Worf.review(
    target_name: str,
    instruction: str,
    reviewer_name: str = "worf-reviewer",
    reviewer_cmd: str | None = None,   # None -> a plain shell; real use: "claude --print --output-format text"
    done_marker: str = "<<END>>",
    timeout: float = 60.0,
    lines: int | None = None,          # None -> full screen; N -> last N non-empty lines of the target
) -> str
    # 1. spawn an independent reviewer pane (no shared context with the target)
    # 2. capture the target's content (full screen, or its last `lines` non-empty lines)
    # 3. ask the reviewer via mesh.send_with_done, bounding the reply with done_marker
    # 4. return the verdict string. The reviewer pane is LEFT RUNNING.

Worf.dismiss(reviewer_name: str) -> None
    # kill the reviewer pane (convenience over session.kill).
```

The verdict is exactly the text the reviewer emitted between the sent prompt and the marker, per `mesh.send_with_done`. The caller owns the reviewer pane's lifecycle: keep it for follow-up questions, or `dismiss` it.

## Status

Pattern documented and **implemented** 2026-06-13 as M17. Composes on M6 (mesh) and the frozen core (M1–M5); changes no existing signature.
