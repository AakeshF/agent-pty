# Uhura pattern

The communications officer to Kirk's commander. Uhura turns the captain's brittle "prompt, then screen-scrape for a sentinel" loop into a clean framed request/response — and adds a fleet broadcast. Where [mesh](captain-kirk-pattern.md) gave us *done-detection* (the lowest layer of the protocol), Uhura gives us the *handshake* on top of it.

## The shape

The captain calls `ask(name, request)` and gets back a single string: the sub-agent's reply, bounded by the framing and a done-marker, trimmed of the surrounding terminal noise. Under the hood Uhura appends a standard instruction to the request ("when you are done, end your reply with `<<END>>` on its own line"), sends it, and delegates the actual send-and-wait to `mesh.send_with_done` — it does **not** reimplement done-detection. Optionally it parses the reply as JSON, so the captain gets a `dict`/`list` instead of a string. `broadcast(names, request)` fans the same framed request out to N panes concurrently and returns the replies keyed by name.

agent-pty's core is a transport; mesh is the commander's toolkit; Spock is the instrument panel; Uhura is the comms protocol. Like the rest of the bridge crew it is opt-in and composes on the existing primitives — it changes no core or mesh signature.

## Which gap it fills

This is the project's own #1-ranked gap. The Captain Kirk doc lists, among [the honest costs](captain-kirk-pattern.md#the-honest-costs):

> **No structured handshake** unless we provide one. Sub-agents return freeform; the captain has to parse.

and among [what the core lacks](captain-kirk-pattern.md#what-the-core-lacks-for-this-to-be-practical), ranked first:

> **Done-detection / message framing.** Single biggest gap.

Mesh's `send_with_done` solved the *matching* half (wait for a marker, return what's between). Uhura solves the *framing* half: it standardizes the instruction that gets the sub-agent to emit that marker in the first place, and the optional JSON contract that turns a freeform reply into a structured value. Together they close the gap end-to-end.

## How it relates to scraping a reply by hand

|  | Tells the sub-agent how to terminate | Returns | Multi-pane | Brittle to ANSI / status bars |
|---|---|---|---|---|
| Captain writes its own "end with X" prompt + `wait_for` | ad-hoc, per call | raw screen slice | one at a time | yes — caller re-derives the slice |
| `mesh.send_with_done` | caller still writes the instruction | reply between sent text and marker | one at a time | handled once, in mesh |
| `Uhura.ask` / `broadcast` (this) | yes — one standard framing | reply string, or parsed JSON | `broadcast` fans out concurrently | handled; JSON contract on top |

The honest framing: when the captain drives **one** pane once, writing the marker instruction inline and calling `send_with_done` is fine. Uhura earns its place when the same handshake repeats — every supervision turn, across a fleet — and when you want a *typed* answer (JSON) rather than a string to re-parse.

## Where it earns its place

- **One framing, everywhere.** The "end your reply with `<<END>>`" convention lives in exactly one place instead of being re-typed (and mistyped) in every prompt. Change the contract once.
- **Typed replies.** `want_json=True` extracts the first JSON object/array from the reply (fenced ` ```json ` block or bare `{...}`/`[...]`), so the captain branches on `result["status"]` instead of regexing a screen. Mitigates the [screen-scraping cost](captain-kirk-pattern.md#the-honest-costs) at the *semantic* level, not just the framing level.
- **Fleet broadcast.** `broadcast` asks N panes the same question concurrently (one thread each) and returns a `name -> reply` dict. This is the natural query primitive for the [worktree swarm and A/B-implementation](captain-kirk-pattern.md#where-it-earns-its-place) patterns: "all of you, report your test status."
- **Graceful degradation.** A pane that never emits the marker times out to `""` in a broadcast rather than sinking the whole call; a reply that isn't valid JSON comes back as `{"_raw": ..., "_error": ...}` rather than raising. The captain always gets something to act on.

It deliberately leans on mesh for the hard part: done-detection and literal-`<` round-tripping live in `mesh.send_with_done`, so Uhura is a thin, honest framing layer rather than a second copy of the matching logic.

## The honest costs

- **Framing is an instruction, not a protocol.** Uhura *asks* the sub-agent to end with the marker. A sub-agent that ignores the instruction (never prints the marker) will time out — there is no enforcement, only convention. This is the same best-effort contract as `send_with_done`, inherited deliberately.
- **JSON extraction is heuristic.** `want_json` takes the first fenced ` ```json ` block, else the first balanced `{...}`/`[...]` span (string-aware, so braces inside strings don't fool it). It can pick the wrong object if the reply contains several, and it returns `{"_raw", "_error"}` rather than raising on malformed JSON. Treat a parsed result as a best-effort decode, not a schema-validated one.
- **Broadcast hides per-pane failure as `""`.** A timed-out or dead pane maps to an empty string so one bad pane never sinks the batch. The flip side: an empty reply and a failed reply look identical. If the distinction matters, follow up with `Spock.diagnose`.
- **It still costs tokens and a round-trip per pane.** `broadcast` parallelizes wall-clock time, not token spend — N panes is N replies in the captain's context. The [orchestrator-bottleneck cost](captain-kirk-pattern.md#the-honest-costs) is reduced in latency, not eliminated in tokens.

These are real. Uhura exists to make the handshake clean and reusable, not to pretend a prompted convention is a guaranteed wire protocol.

## Module shape — `agent_pty.uhura`

Lives alongside the core, mesh, and spock in the same package. New module file `agent_pty/uhura.py`. A `Uhura` namespace class mirrors `Pty`, `Mesh`, and `Spock` (`staticmethod` wrappers over module-level functions). Parallel MCP tools under the `uhura_*` namespace, exposed by the same `agent-pty-mcp` server.

**Actuator (not read-only).** Unlike Spock, Uhura *sends*: `ask` and `broadcast` inject keystrokes into panes (via `mesh.send_with_done`, which uses `agent_pty.io.send`). It is a comms officer who actually keys the mic, not an observer.

## Public API

```python
Uhura.ask(name, request, done_marker="<<END>>", timeout=60.0, want_json=False) -> str | dict | list
    # Frame `request` with a standard "end your reply with <done_marker> on its
    # own line" instruction, then delegate to mesh.send_with_done. Return the
    # reply string. If want_json: extract the first JSON object/array from the
    # reply (```json fenced blocks or bare {...}/[...]) and json.loads it,
    # returning the dict/list. On parse failure return {"_raw": reply, "_error": msg}.

Uhura.broadcast(names, request, done_marker="<<END>>", timeout=60.0) -> dict[str, str]
    # Send the same framed request to each name concurrently (one thread per
    # pane) and collect replies keyed by name. A pane that times out (or whose
    # session is gone) maps to "". One key per input name.
```

### The framing

`ask` appends a fixed instruction line after the request:

```
<your request>
When you are done, end your reply with <<END>> on its own line.
```

so a cooperating sub-agent's screen ends with its reply followed by the marker on its own line — exactly the shape `mesh.send_with_done` extracts.

### JSON extraction order

1. First ` ```json ` (or bare ` ``` `) fenced block, if present and not tagged a non-JSON language.
2. Else the first balanced `{...}` or `[...]` span, scanned with string/escape awareness so braces inside JSON strings don't unbalance the count.
3. Else `{"_raw": reply, "_error": "no JSON object or array found in reply"}`.

A located-but-invalid span returns `{"_raw": reply, "_error": "JSON parse failed: ..."}`.

## Status

Pattern documented and **implemented** 2026-06-13 as M8 in the [build plan](build-plan.md). Composes on M6 ([mesh](captain-kirk-pattern.md), specifically `send_with_done`) and the frozen core (M1–M5); changes no existing signature.

Implementation choices worth knowing about:
- Uhura never reimplements done-detection or literal-`<` escaping — both live in `mesh.send_with_done`, which `ask` calls directly.
- `broadcast` swallows *all* exceptions per pane (timeout, `SessionNotFoundError`, etc.) into `""`, so the returned dict always has one key per requested name.
- JSON balancing is string-aware (it ignores `{`/`}`/`[`/`]` inside double-quoted strings and respects `\` escapes), so a brace in a JSON string value won't truncate the span.
