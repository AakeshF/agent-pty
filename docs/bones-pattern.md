# Bones pattern

The ship's doctor to Spock's science officer. Where [Spock](spock-pattern.md) reports the coarse *state* of each pane (`dead`/`blocked`/`idle`/`busy`), Bones diagnoses *sickness* in a still-running pane — the failure modes that look like work but aren't. Like Spock, Bones only *observes*: it NEVER sends a keystroke and NEVER mutates a pane.

## The shape

Spock says "this pane is busy." Bones asks "but is it *healthy*?" A pane can be busy and dying: spewing a traceback, stuck in a thrashing loop printing the same line forever, or hung mid-task with a frozen screen that never reaches a prompt. To Spock's double-sample those last two read as "busy" or "idle" respectively — technically true, diagnostically useless. Bones is the pathology layer: it takes a pane name, samples the screen, and returns a `Diagnosis` listing concrete *symptoms*. No symptoms means healthy.

agent-pty's core is a transport; mesh is a commander's toolkit; Spock is the instrument panel; Bones is the medical scanner you point at the one pane the instrument panel flagged. Like both, it is opt-in and composes on the existing read-only primitives — it changes no core, mesh, or Spock signature.

## How it relates to Spock

| | Spock | Bones |
|---|---|---|
| Question answered | what *state* is each pane in? | is this pane *sick*, and how? |
| Output | `state` ∈ dead/blocked/idle/busy | `symptoms` ⊆ {dead, errors, thrashing, hung} |
| "busy" pane spewing a traceback | reported `busy` | reported `errors` |
| "idle" pane frozen mid-task | reported `idle` | reported `hung` |
| Granularity | one state per pane | zero-or-more symptoms per pane |

They are complementary, not redundant. The captain uses Spock to triage *attention* across the fleet, then Bones to triage *intervention* on a suspect pane.

## Where it earns its place

- **Catches the "looks like work" failures.** A thrashing loop and a healthy build both keep Spock's screen changing → both read `busy`. Bones distinguishes them. This is the direct mitigation of the [brittle screen-scraping cost](captain-kirk-pattern.md#the-honest-costs): the error/thrash/hung heuristics live in one place instead of being re-derived ad hoc per supervision turn.
- **"Hung mid-task" detection.** A pane whose screen has frozen but is *not* sitting at a ready prompt is stuck, not done — a different signal from Spock's `idle` (which a finished-and-waiting pane also produces). Bones separates "frozen mid-task" from "done and waiting" via the prompt-ending check.
- **Cheap intervention triage.** `triage` returns panes sickest-first (dead worst, then most symptoms) so the captain spends a fix on the pane that's actually broken, not the three that are merely loud.
- **Token economy.** One `Diagnosis` per pane replaces re-reading and re-judging raw screens every turn — the same [orchestrator-bottleneck](captain-kirk-pattern.md#the-honest-costs) mitigation Spock provides, at the pathology layer.

## The honest costs

- **Every detector is a screen heuristic.** Bones reads the rendered screen, not process state. Errors that scrolled off the top, a process wedged with a clean screen, or a non-zero exit with no message are all invisible to it. It's a signal, not a guarantee.
- **`errors` matches *text*, not failures.** The signatures (`traceback`, `fatal`, `panic`, `segmentation fault`, `error:`, `exception`, `command not found`) fire on any line that contains them — including `echo "no error: all good"`, a log discussing past errors, or a test that prints expected-failure output. False positives are real.
- **`thrashing` only sees the *visible* screen.** A loop printing the same line saturates the pane to identical rows, which trips `THRASH_REPEATS`. But a loop printing an *incrementing* counter never repeats a line and reads as healthy (it's genuine progress, or at least genuinely changing). The detector catches stuck-repeating, not all runaway loops.
- **`hung` is a best-effort double-sample.** Unchanged across `SETTLE_INTERVAL` (0.15s) and not ending in a known prompt char (`$ # >>> >`) → hung. A pane that merely paused during that window (a slow network call) false-positives; a custom prompt without a known ending also false-positives; a thrasher whose screen *is* changing won't be flagged hung (it'll be flagged thrashing instead). Don't call it in a tight loop.
- **A diagnosis is point-in-time.** The pane can recover or crash the instant after `examine` returns. Treat it as a recent observation, never a lock.

These are real. Bones exists to surface the *kind* of sickness cheaply, not to be an oracle — confirm before acting on a single sample.

## Module shape — `agent_pty.bones`

Lives alongside the core, mesh, and Spock in the same package. New module file `agent_pty/bones.py`. A `Bones` namespace class mirrors `Pty`, `Mesh`, and `Spock` (`staticmethod` wrappers over module-level functions), and re-exposes the `Diagnosis` dataclass. Thresholds are module constants: `SETTLE_INTERVAL`, `THRASH_REPEATS`, the error-signature regex list, and the prompt-ending set.

**Read-only invariant (hard).** `bones.py` imports nothing that sends keystrokes. It never calls `agent_pty.io.send`, `Pty.send`, `mesh.pipe`, or `tmux send-keys`. It composes only on read-only primitives: `io.snapshot`, `session.list_sessions`, and `session.SessionNotFoundError`. A reviewer can grep for this.

## Public API

```python
@dataclass
class Diagnosis:
    name: str
    healthy: bool            # True iff symptoms == []
    symptoms: list[str]      # subset of {"dead", "errors", "thrashing", "hung"}, stable order

Bones.examine(name: str) -> Diagnosis
    # Sample one pane and list its symptoms. healthy == (no symptoms).

Bones.triage(names: list[str] | None = None) -> list[Diagnosis]
    # names=None -> all managed sessions; names given -> only those.
    # An unmanaged name diagnoses as "dead". Sorted sickest-first:
    # dead always worst, then descending symptom count, ties by name.
```

### Symptom detection (deterministic, read-only)

1. **dead** — name not in `list_sessions()`, or `snapshot` raises `SessionNotFoundError` (including a death inside the settle window). Diagnosed alone; the worst symptom.
2. **errors** — the screen matches any case-insensitive error signature.
3. **thrashing** — some non-empty visible line is repeated more than `THRASH_REPEATS` (8) times.
4. **hung** — the screen is unchanged across `SETTLE_INTERVAL` and the bottom non-empty line does not end in a ready-prompt char (`$`, `#`, `>>>`, `>`).

`errors`, `thrashing`, and `hung` can co-occur on one pane; `dead` short-circuits and stands alone.

## Status

Pattern documented and **implemented** 2026-06-13 as M15. Composes on the frozen core (M1–M5) and the read-only conventions established by M7 (Spock); changes no existing signature.
