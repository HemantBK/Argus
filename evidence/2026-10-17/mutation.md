# Mutation check of the citation guarantees (17 Oct 2026)

We broke each of three citation guarantees on purpose, in a scratch copy of the repository, and ran the
offline backend tests to see which tests notice. No mutation was ever committed, deployed or run against
Gemini; the tests use the offline fake model.

- Code: commit `7a4a5d1` (its `backend/argus_api` is the same as the deployed build `7c3072d`).
- Library: `backend/data/corpus` with corpus hash `4ba88a4ca44614a09e911c0ecb990db82c7cd25a9d1b34c97c1d7944b9ac6db6`,
  the hash the live API reported on 17 Oct (`release/PROMOTED-SMOKE.json`, `details.health.corpus_hash`).
- Python 3.11.15, packages from `backend/requirements.txt` and `backend/requirements-dev.txt`.

## Result

| Mutation | What it breaks | Tests that failed |
|---|---|---|
| None (baseline) | nothing | 0 failed, 882 passed, 3 skipped |
| M1 | `quote_in_text` always returns true, so any quote "matches" | 37 failed |
| M2 | the in-force check always passes, so a withdrawn rule counts as in force | 11 failed |
| M3 | gate 1 in `finding()` is skipped, so a failed citation check no longer clears the citation | 13 failed |

Every mutation was caught by at least one test that checks behaviour, not only by a change detector. The
property test `tests/test_citation_property.py` failed under all three.

## The mutations

```diff
# M1: backend/argus_api/corpus/store.py, quote_in_text
     """
+    return True  # MUTATION M1: quote_in_text always true
     q = normalise(quote)

# M2: backend/argus_api/monitor/checks.py, _in_force_check
 def _in_force_check(corpus: Corpus, instrument_ref: str, para: str) -> Check:
+    return Check(name=CheckName.IN_FORCE, passed=True, detail="MUTATION M2: in-force check ignored.")
     if instrument_ref not in corpus.instruments:

# M3: backend/argus_api/agent/loop.py, _Review.finding
-        if citation_failures:
+        if False:  # MUTATION M3: citation gate 1 skipped
             details = " ".join(c.detail for c in citation_failures)
```

M1 sits before the empty-quote check, so it also disables that check; one of its 37 failures
(`test_empty_quote_is_rejected`) comes from that.

## Commands

```bash
# <scratch> is any empty directory outside the repository.
git clone <this repository> <scratch>/base && git -C <scratch>/base checkout 7a4a5d1
python3.11 -m venv <scratch>/venv
<scratch>/venv/bin/pip install -r <scratch>/base/backend/requirements.txt \
  -r <scratch>/base/backend/requirements-dev.txt
# The library is not committed: build it (make corpus fetches the RBI pages) or copy a built one into
# <scratch>/base/backend/data/corpus, then check its hash:
<scratch>/venv/bin/python <scratch>/base/scripts/corpus_hash.py <scratch>/base/backend/data/corpus
for m in m1 m2 m3; do cp -R <scratch>/base <scratch>/$m; done
# apply the three edits above, one per copy, then in each of base, m1, m2 and m3:
cd <scratch>/<copy>/backend && PYTHONDONTWRITEBYTECODE=1 <scratch>/venv/bin/python -m pytest -q -p no:cacheprovider -rfE tests
```

## Failing tests, per mutation

### M1: `quote_in_text` always true (37)

- `tests/test_agent.py::test_monitor_off_reports_the_drafts_quote_unchecked`
- `tests/test_citation_property.py::test_every_monitored_finding_has_no_citation_or_a_verbatim_one`, cases
  `bd-02` and `bd-03`, each with `off_task`, `passes_everything` and `unsupported` (6)
- `tests/test_cloud_e2e_offline.py::test_hero_oracle_catches_each_defect[policy_a-<lambda>-r.quotes_verbatim]`
- `tests/test_corpus.py` (27): `test_quote_with_altered_number_fails`, `test_quote_cut_inside_a_number_fails`,
  `test_quote_ending_on_a_number_without_its_unit_fails`,
  `test_quote_ending_on_a_spelled_number_without_what_follows_fails`, `test_quote_cut_inside_a_word_fails`,
  `test_quote_boundary_rules` (14 cases), `test_quote_may_not_cut_a_joined_compound` (4 cases),
  `test_no_fixture_quote_can_end_or_start_inside_a_hyphenated_compound`, `test_quote_with_altered_word_fails`,
  `test_quote_from_another_paragraph_fails`, `test_empty_quote_is_rejected`
- `tests/test_eval_dataset.py::test_unverified_quote_is_reported`
- `tests/test_monitor.py::test_altered_quote_is_revised`

### M2: in-force check ignored (11)

- `tests/test_agent.py::test_outdated_reference_path`
- `tests/test_agent.py::test_withdrawn_rule_cited_as_current_is_revised_to_the_rule_in_force`
- `tests/test_citation_property.py::test_every_monitored_finding_has_no_citation_or_a_verbatim_one`, case
  `bd-04` with `off_task`, `passes_everything` and `unsupported` (3)
- `tests/test_eval_runner.py::test_monitor_only_calls_monitor_finding_directly`
- `tests/test_monitor.py::test_withdrawn_instrument_cited_as_current_is_revised`
- `tests/test_monitor.py::test_unknown_instrument_fails_every_citation_check`
- `tests/test_monitor.py::test_read_of_a_withdrawn_paragraph_is_revised_toward_the_rule_in_force`
- `tests/test_monitor.py::test_read_of_an_unknown_instrument_is_revised`
- `tests/test_custom.py::test_agent_prompts_tools_and_monitor_code_are_untouched`: a change detector (it fails
  on any uncommitted edit under `monitor/`), not a behaviour test

### M3: citation gate 1 skipped (13)

- `tests/test_agent.py::test_fake_paragraph_still_wrong_after_revision_becomes_insufficient_evidence`
- `tests/test_citation_property.py::test_every_monitored_finding_has_no_citation_or_a_verbatim_one`, cases
  `bd-01` to `bd-04`, each with `off_task`, `passes_everything` and `unsupported` (12)

## Limits

- Three hand-picked mutations, not a mutation-testing tool run over the whole code base.
- Gate 2 (`citation()` rebuilds the citation from the library and raises when the paragraph is missing or the
  quote does not match) was left in place for M3, so M3 tests gate 1 alone.
- The baseline needs the library in `backend/data/corpus`; without it 20 tests error or fail (see
  `docs/engineering-quality.md`, Tests row).
