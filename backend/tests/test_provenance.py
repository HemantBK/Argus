"""Fake data never passes for a real review: the seed script writes only to the emulator and marks its run
seeded, and save_sample (library and CLI) refuses a seeded run. The refusals of a real run that is not a
Vertex review with the pinned models are in tests/test_runs.py (test_save_sample_refuses_*)."""

from typing import Any

import pytest

from argus_api import runs, save_sample, seed_fake_run
from argus_api.schema import Run, RunStatus
from argus_api.store_fake import MemoryStore


class ExplodingFirestore:
    """A Firestore client that fails the test on any use: proof that nothing was written."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"Firestore was touched ({name}) before the emulator guard")


class RecordingFirestore:
    """Just enough of firestore.Client for seed_fake_run.write: records every set() by document path."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def collection(self, name: str, prefix: str = "") -> "RecordingFirestore._Collection":
        return RecordingFirestore._Collection(self, f"{prefix}{name}")

    class _Collection:
        def __init__(self, db: "RecordingFirestore", path: str) -> None:
            self.db, self.path = db, path

        def document(self, doc_id: str) -> "RecordingFirestore._Document":
            return RecordingFirestore._Document(self.db, f"{self.path}/{doc_id}")

    class _Document:
        def __init__(self, db: "RecordingFirestore", path: str) -> None:
            self.db, self.path = db, path

        def set(self, data: dict[str, Any]) -> None:
            self.db.docs[self.path] = data

        def collection(self, name: str) -> "RecordingFirestore._Collection":
            return self.db.collection(name, prefix=f"{self.path}/")


def test_seed_main_refuses_without_the_emulator_before_any_client_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(seed_fake_run.EMULATOR_ENV, raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "argus-test")

    def no_client(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a Firestore client was created before the emulator guard")

    monkeypatch.setattr(seed_fake_run.firestore, "Client", no_client)
    with pytest.raises(SystemExit, match=seed_fake_run.EMULATOR_ENV):
        seed_fake_run.main()


@pytest.mark.parametrize("value", ["", "   "])
def test_seed_write_refuses_without_the_emulator_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(seed_fake_run.EMULATOR_ENV, value)
    with pytest.raises(SystemExit, match=seed_fake_run.EMULATOR_ENV):
        seed_fake_run.write(ExplodingFirestore())  # type: ignore[arg-type]


def test_seed_writes_a_seeded_run_on_the_emulator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(seed_fake_run.EMULATOR_ENV, "localhost:8081")
    db = RecordingFirestore()
    seed_fake_run.write(db)  # type: ignore[arg-type]
    for root in (runs.RUNS, runs.SAMPLES):
        run = Run.model_validate(db.docs[f"{root}/{seed_fake_run.RUN_ID}"])
        assert run.seeded is True


def _store_with_seeded_run() -> MemoryStore:
    store = MemoryStore()
    run, *_ = seed_fake_run.build()
    assert run.seeded and run.status == RunStatus.DONE  # refused for being seeded, not for anything else
    store.set(runs.run_path(run.run_id), run.model_dump(mode="json"))
    return store


def test_save_sample_refuses_a_seeded_run() -> None:
    store = _store_with_seeded_run()
    with pytest.raises(runs.NotASample, match="seed script"):
        runs.save_sample(store, seed_fake_run.RUN_ID)
    assert store.get(runs.run_path(seed_fake_run.RUN_ID, runs.SAMPLES)) is None


def test_save_sample_cli_exits_with_the_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store_with_seeded_run()
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "argus-test")
    monkeypatch.setattr(save_sample.firestore, "Client", lambda project: ExplodingFirestore())
    monkeypatch.setattr(save_sample, "FirestoreStore", lambda client: store)
    with pytest.raises(SystemExit, match="save-sample refused: .*seed script"):
        save_sample.main([seed_fake_run.RUN_ID])
    assert store.get(runs.run_path(seed_fake_run.RUN_ID, runs.SAMPLES)) is None


@pytest.mark.parametrize("flag", ["--force", "--allow-seeded", "--any-provider"])
def test_save_sample_cli_has_no_override_flag(flag: str) -> None:
    with pytest.raises(SystemExit) as exc:
        save_sample.main([seed_fake_run.RUN_ID, flag])
    assert exc.value.code == 2  # argparse: unrecognised argument
