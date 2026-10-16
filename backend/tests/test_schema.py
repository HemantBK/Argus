from argus_api.schema import FindingLabel, Run
from argus_api.seed_fake_run import build


def test_fake_run_round_trips_through_json() -> None:
    run, steps, findings, audit = build()
    assert Run.model_validate(run.model_dump(mode="json")) == run
    assert {f.label for f in findings} <= set(FindingLabel)
    assert [s.seq for s in steps] == sorted(s.seq for s in steps)


def test_fake_run_is_labelled_fake() -> None:
    run, _, findings, _ = build()
    assert "FAKE" in run.policy_title
    assert all("FAKE" in f.clause_text for f in findings)
