# Requests from the evaluation harness

Owner of this file: the eval harness (argus_api/eval/). Each request names the module owner it is for. The
harness never edits those modules; it detects at runtime whether a request has landed and refuses to run the
affected condition until it has.

## R1. `review_clause(..., monitor_enabled: bool)` (owner: agent/, argus_api/agent/loop.py)

Status: done (2026-10-16). `review_clause` takes `monitor_enabled: bool = True`; `agent_no_monitor` runs.

Commit-ready description: "Add a monitor_enabled switch to review_clause for the agent_no_monitor eval
condition. With it off, the agent runs with the same system prompt, tools, tool-call limit, draft prompt and
models, but no monitor step runs after reads or on the draft and there is no revision. No step carries a
verdict, outcome.verdicts is empty and stopped/revised are False. The finding is the draft as written: label
kept, monitor_status passed, citation built from the corpus when the cited paragraph exists and None when it
does not. Default on, so the app is unchanged. Each agent_call log line records monitor_enabled."

Notes for the harness:
- The system prompt is unchanged, including its paragraph about the step monitor (point 1 asked for the same
  prompt). With the monitor off no monitor message ever arrives, so that paragraph is never acted on. The
  prompt version (AGENT_PROMPT_VERSION) is the same for both agent conditions.
- When the cited paragraph exists but the draft's quote is not in it, the citation still carries the corpus
  metadata (instrument, url, snapshot date, content hash, in force) with the draft's own quote, unchecked.
  Checking the quote is part of the guard being measured; `assess_citation` scores it (quote_verified False).
  This is the one place a Finding's citation quote is not verified, and only with the monitor off.
- Pinned by tests/test_agent.py (test_monitor_off_*) and
  tests/test_eval_runner.py::test_real_review_clause_runs_agent_no_monitor_without_a_monitor_call; both script
  FakeLLM with no monitor reply, so a monitor call would fail them.

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
