# Requests from the evaluation harness

Owner of this file: the eval harness (argus_api/eval/). Each request names the module owner it is for. The
harness never edits those modules; it detects at runtime whether a request has landed and refuses to run the
affected condition until it has.

## R1. `review_clause(..., monitor_enabled: bool)` (owner: agent/, argus_api/agent/loop.py)

Status: open (not present in review_clause as of 2026-10-16). Blocks the `agent_no_monitor` condition.

PLAN section 7 compares "the same agent without the monitor" with full Argus: identical corpus, prompts and
model versions. The harness needs one switch on `argus_api.agent.review_clause`:

```python
def review_clause(
    llm,
    settings,
    corpus,
    clause,
    emit,
    *,
    next_seq=None,
    today=None,
    run_id=None,
    monitor_enabled: bool = True,
) -> ReviewOutcome: ...
```

Wanted behaviour with `monitor_enabled=False`:

1. Same system prompt, tools, tool-call limit, draft prompt and models as with the monitor on. Only the monitor
   is removed: no `monitor_read` after reads, no `monitor_finding` on the draft, no revision step.
   (If the system prompt's paragraph about the step monitor has to go, say so here; the harness records the
   prompt version per condition.)
2. No step carries a `verdict`. The harness fails loudly if any step of an `agent_no_monitor` trace has one
   (that would mean the switch did not turn the monitor off), and also if a `full_argus` trace has none.
3. The final finding is the draft as written: its label stands, `monitor_status` is `passed`, and its
   citation is built from the corpus as today when the cited paragraph exists. When the cited paragraph does
   not exist, `citation` is `None` and the label still stands (the downgrade to `insufficient_evidence` for an
   unverifiable citation is part of the guard being measured, so it belongs to the monitor-on path).
4. `ReviewOutcome` totals (tokens, cost, latency) cover the calls actually made.

Default `True`, so the app (runs.py) is unchanged. A unit test that `monitor_enabled=False` makes no
monitor model call (FakeLLM with no monitor reply scripted) would pin it.

How the harness uses it: `runner.check_reviewer` inspects the signature before any model call;
`agent_no_monitor` raises `MonitorSwitchMissing` if the parameter is absent. `full_argus` passes
`monitor_enabled=True` when the parameter exists and otherwise calls review_clause as the app does (monitor
always on).
