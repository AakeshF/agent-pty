# Red Alert pattern

The escalation siren for the fleet. [Spock](spock-pattern.md) computes a deadlock flag and names dead panes — but a report only helps if someone reads it, and the captain may not look for several turns. Red Alert closes that loop: it watches the fleet on a background thread and *pushes a notification to the human* the moment something needs them.

## The shape

Spock reports; Red Alert escalates. It polls `spock.assess`, and on a *new* problem fires a side-effecting notifier — a desktop toast via `notify-send`, a line on stderr, or any `callable(str)` you hand it (Slack, email, pager). It is the layer that turns the [Captain Kirk pattern](captain-kirk-pattern.md)'s two quietest failure modes — "permission deadlocks" and "a session crash is detected only when the next operation errors" — into an active interrupt instead of a silent stall.

agent-pty's core is a transport; mesh is the commander's toolkit; Spock is the instrument panel; Red Alert is the klaxon wired to the panel. Like the others it is opt-in, read-only on panes, and composes on existing primitives — it changes no core, mesh, or Spock signature.

## How it relates to polling Spock yourself

|  | Who notices | When | Side effect | Dedup |
|---|---|---|---|---|
| Captain calls `Spock.assess` each turn | the captain (if it remembers to look) | next supervision turn | none | manual |
| `RedAlert.watch` (this) | the human, out-of-band | within one poll (~0.5s) | notification | identical consecutive alerts fire once |

The honest framing: when the captain is actively driving and *will* call `assess` every turn anyway, Red Alert adds nothing. It earns its place exactly when the captain is busy elsewhere (deep in one pane) or absent (long-horizon babysitting) — the windows where a deadlock or death goes unnoticed and the whole fleet idles waiting on a human.

## Where it earns its place

- **Permission deadlocks.** The Kirk doc's [headline cost](captain-kirk-pattern.md#the-honest-costs): a sub-agent blocks on a prompt and the captain doesn't notice. Red Alert turns Spock's `deadlock` flag into a toast on the human's desktop in well under a second.
- **Silent deaths.** A crashed session is otherwise [detected only on the next operation](captain-kirk-pattern.md#what-the-core-lacks-for-this-to-be-practical). Red Alert names dead panes and escalates immediately.
- **Unattended runs.** During a long build/migrate the human can walk away; the klaxon brings them back only when needed, not on a timer.

It deliberately leans on Spock for *what* counts as trouble — it never re-derives the deadlock or blocked heuristics. Red Alert owns only one new decision: *when to bother a human.*

## API

```python
@dataclass
class Alert:
    kind: str         # "deadlock" | "death"
    detail: str       # one-line, human/LLM readable (Spock's summary for deadlock)
    names: list[str]  # implicated session names (blocked panes, or dead panes)

RedAlert.check(names=None) -> Alert | None
    # spock.assess(names): deadlock -> Alert("deadlock", summary, [blocked names]);
    # any dead pane -> Alert("death", ...). Prefer deadlock when both hold.
    # None when the fleet is fine.

RedAlert.notify(message, notifier=None) -> None
    # Default notifier: notify-send if on PATH, else stderr. Custom: any
    # callable(str). Dependency-free and non-fatal.

RedAlert.watch(names=None, notifier=None, poll=0.5) -> Alerter
    # Background thread; on each poll calls check(); on a NEW alert (deduped
    # against the previous one) calls notify(alert.detail, notifier).
    # Alerter.close()/.stop(); also a context manager.
```

Dedup is consecutive-identical: a persisting deadlock fires once, a return to a healthy fleet resets the state, and a fresh problem re-alerts.

## The honest costs

- **It inherits every Spock/mesh limit.** `deadlock` is exactly Spock's flag (regex blocked-detection — false positives on a `read -p "Continue?"`, false negatives on custom prompts); `death` is exactly "not in `list_sessions` / `snapshot` raised." A signal, not a guarantee — it can cry wolf or stay silent.
- **Polling latency and cost.** The watcher pays one `spock.assess` per poll, each of which sleeps one shared `SETTLE_INTERVAL`. Tighten `poll` for faster alerts at higher cost; the default 0.5s is a deliberate floor.
- **The notifier is best-effort and fire-and-forget.** `notify-send` failures fall back to stderr; a broken custom notifier is swallowed so it can't kill the watcher. Delivery is not guaranteed or acknowledged.
- **No auto-remediation.** Red Alert escalates; it does not answer the prompt or respawn the pane. That is a separate, security-sensitive decision (see the Kirk doc's permission-auto-approval open question).

These are real. Red Alert exists to make sure a human *finds out*, not to fix the problem for them.

## Module shape — `agent_pty.red_alert`

Lives alongside core/mesh/spock in the same package. New module file `agent_pty/red_alert.py`. A `RedAlert` namespace class mirrors `Pty`/`Mesh`/`Spock` (`staticmethod` wrappers plus the `Alert` dataclass). Parallel MCP tools under the `red_alert_*` namespace can be exposed by the same `agent-pty-mcp` server in the Integrate phase.

**Read-only-on-panes invariant.** `red_alert.py` imports nothing that sends keystrokes. It composes only on `spock.assess` (itself read-only) plus stdlib for the notification side effect (`shutil.which`, `subprocess.run` of `notify-send`, `sys.stderr`). A reviewer can grep for the absence of `agent_pty.io.send` / `Pty.send` / `send-keys`.

## Status

Pattern documented and **implemented** 2026-06-13 as M13. Composes on M7 (Spock) and M6 (mesh) and the frozen core (M1–M5); changes no existing signature.
