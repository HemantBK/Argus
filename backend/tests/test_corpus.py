"""Tests for the RBI library. Expected values come from the committed fixture, which is a subset of the real
parsed output (written by `build_corpus --fixture-out tests/fixtures/corpus`), never typed by hand."""

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from argus_api.corpus import build_corpus as bc
from argus_api.corpus.common import SUPERSESSION_PATH, content_hash
from argus_api.corpus.store import (
    COLLECTION_INSTRUMENTS,
    COLLECTION_PARAGRAPHS,
    COLLECTION_SUPERSESSION,
    NUMBER_WORDS,
    WORD_JOINERS,
    Corpus,
    CorpusError,
    Instrument,
    Paragraph,
    is_number_token,
    load_supersession,
    load_to_firestore,
    quote_in_text,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "corpus"


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return Corpus.load(FIXTURE_DIR)


def _raw(name: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    return data


def _in_force_ref(corpus: Corpus) -> str:
    refs = [ref for ref, inst in corpus.instruments.items() if inst.in_force]
    assert len(refs) == 1
    return refs[0]


# ---- lookup ---------------------------------------------------------------------------------------------


def test_every_fixture_paragraph_is_found_by_ref_and_para(corpus: Corpus) -> None:
    for raw in _raw("paragraphs.json"):
        p = corpus.paragraph(raw["instrument_ref"], raw["para"])
        assert p is not None
        assert p.text == raw["text"]
        assert p.content_hash == content_hash(raw["text"])


def test_sub_paragraphs_are_addressable_and_contained_in_their_parent(corpus: Corpus) -> None:
    subs = [p for p in corpus.paragraphs.values() if p.parent_para is not None]
    assert subs
    for p in subs:
        parent = corpus.paragraph(p.instrument_ref, p.parent_para or "")
        assert parent is not None
        assert p.text in parent.text
        assert p.para.startswith(parent.para)


def test_unknown_paragraph_returns_none(corpus: Corpus) -> None:
    ref = _in_force_ref(corpus)
    top_numbers = [
        int(p.para) for p in corpus.paragraphs.values() if p.instrument_ref == ref and p.para.isdigit()
    ]
    assert corpus.paragraph(ref, str(max(top_numbers) + 1000)) is None
    assert corpus.verify_quote(ref, str(max(top_numbers) + 1000), "any text").paragraph_found is False


def test_unknown_instrument_raises(corpus: Corpus) -> None:
    with pytest.raises(CorpusError):
        corpus.in_force("RBI/0000-00/0")


# ---- quote matching -------------------------------------------------------------------------------------


def _figure(corpus: Corpus) -> tuple[Paragraph, list[str], int]:
    """A current-instrument paragraph and the index of a multi-digit figure with words on both sides."""
    ref = _in_force_ref(corpus)
    for p in corpus.paragraphs.values():
        if p.instrument_ref != ref or p.level != 2:
            continue
        words = p.text.split()
        for i in range(3, len(words) - 1):
            if words[i].isdigit() and len(words[i]) >= 2 and words[i + 1].isalpha():
                return p, words, i
    raise AssertionError("fixture has no figure followed by a unit")


def _spelled_figure(corpus: Corpus) -> tuple[Paragraph, list[str], int]:
    """A paragraph and the index of a spelled-out number (a production NUMBER_WORDS entry) before a word."""
    for p in corpus.paragraphs.values():
        words = p.text.split()
        for i in range(3, len(words) - 1):
            if words[i] in NUMBER_WORDS and words[i + 1][0].isalpha():
                return p, words, i
    raise AssertionError("fixture has no spelled-out number followed by a word")


def _check(corpus: Corpus, p: Paragraph, words: list[str]) -> bool:
    return corpus.verify_quote(p.instrument_ref, p.para, " ".join(words)).matches


def test_exact_quote_matches(corpus: Corpus) -> None:
    p, words, i = _figure(corpus)
    check = corpus.verify_quote(p.instrument_ref, p.para, " ".join(words[i - 3 : i + 2]))
    assert check.paragraph_found and check.matches


def test_quote_with_different_whitespace_matches(corpus: Corpus) -> None:
    p, words, i = _figure(corpus)
    span = words[i - 3 : i + 2]
    quote = "\n   ".join(span[:2]) + "\t\t" + "  ".join(span[2:]) + "  \n"
    assert corpus.verify_quote(p.instrument_ref, p.para, quote).matches


def test_quote_with_altered_number_fails(corpus: Corpus) -> None:
    p, words, i = _figure(corpus)
    altered = words[i - 3 : i + 2]
    altered[3] = str(int(words[i]) + 1)
    assert not _check(corpus, p, altered)


def test_quote_cut_inside_a_number_fails(corpus: Corpus) -> None:
    p, words, i = _figure(corpus)
    assert not _check(corpus, p, words[i - 3 : i] + [words[i][:-1]])


def test_quote_ending_on_a_number_without_its_unit_fails(corpus: Corpus) -> None:
    p, words, i = _figure(corpus)
    assert not _check(corpus, p, words[i - 3 : i + 1])
    assert _check(corpus, p, words[i - 3 : i + 2])


def test_quote_ending_on_a_spelled_number_without_what_follows_fails(corpus: Corpus) -> None:
    p, words, i = _spelled_figure(corpus)
    assert not _check(corpus, p, words[i - 3 : i + 1])
    assert _check(corpus, p, words[i - 3 : i + 2])


def test_quote_cut_inside_a_word_fails(corpus: Corpus) -> None:
    p, words, i = _figure(corpus)
    span = words[i - 3 : i + 2]
    assert _check(corpus, p, span)
    longest = max(range(len(span)), key=lambda k: len(span[k]))
    assert len(span[longest]) > 3
    if longest == 0:
        assert not _check(corpus, p, [span[0][1:]] + span[1:])
    assert not _check(corpus, p, span[:longest] + [span[longest][:-1]])


@pytest.mark.parametrize(
    ("quote", "text", "expected"),
    [
        ("5 per cent", "up to 2.5 per cent of the loan", False),
        ("up to 2", "up to 2.5 per cent of the loan", False),
        ("000 crore", "a limit of 1,000 crore", False),
        ("Bank", "the Banks shall", False),
        ("Directions, 2025", "as per the Directions, 2025", True),
        ("Directions, 2025", "as per the Directions, 2025.", True),
        ("Directions, 2025", "the Directions, 2025 shall apply", False),
        ("(4) An NBFC", "(4) An NBFC shall", True),
        ("twenty", "within twenty-four hours", False),
        ("four hours", "within twenty-four hours", False),
        ("-four hours", "within twenty-four hours", False),
        ("within twenty-", "within twenty-four hours", False),
        ("within twenty-four", "within twenty-four hours", False),
        ("within twenty-four hours", "within twenty-four hours", True),
        ("a one", "a one-time fee", False),
        ("of one", "of one-third", False),
        ("a one-time fee", "a one-time fee", True),
        ("10", "within 10-15 days", False),
        ("15 days", "within 10-15 days", False),
        ("the NBFC", "the NBFC - LSP", True),
    ],
)
def test_quote_boundary_rules(quote: str, text: str, expected: bool) -> None:
    assert quote_in_text(quote, text) is expected


@pytest.mark.parametrize("joiner", sorted(WORD_JOINERS))
def test_quote_may_not_cut_a_joined_compound(joiner: str) -> None:
    text = f"within twenty{joiner}four hours"
    assert quote_in_text(f"twenty{joiner}four hours", text)
    assert not quote_in_text("within twenty", text)
    assert not quote_in_text("four hours", text)


def _hyphenated_compounds(corpus: Corpus) -> list[tuple[Paragraph, str, int]]:
    """(paragraph, normalised text, index of the hyphen) for every letter-hyphen-letter in the fixture."""
    found = []
    for p in corpus.paragraphs.values():
        t = " ".join(p.text.split())
        for i in range(1, len(t) - 1):
            if t[i] in WORD_JOINERS and t[i - 1].isalnum() and t[i + 1].isalnum():
                found.append((p, t, i))
    return found


def test_no_fixture_quote_can_end_or_start_inside_a_hyphenated_compound(corpus: Corpus) -> None:
    compounds = _hyphenated_compounds(corpus)
    assert compounds, "fixture has no hyphenated compound"
    whole_word_checked = 0
    for p, t, i in compounds:
        # The whitespace-delimited token holding the compound, e.g. "one-time" or "(RE-LSP)".
        left = t.rfind(" ", 0, i) + 1
        right = t.find(" ", i)
        right = len(t) if right == -1 else right
        token = t[left:right]
        last_part = re.findall(r"[^\W_]+", token)[-1]
        if not is_number_token(last_part):
            assert corpus.verify_quote(p.instrument_ref, p.para, token).matches, (p.id, token)
            whole_word_checked += 1
        # Either half may also occur elsewhere in the paragraph as a word of its own, so cut it out of the
        # compound's own token: the quote edge inside the compound must be refused there.
        for half in (t[left:i], t[i + 1 : right]):
            if half.strip():
                assert not quote_in_text(half, token), (p.id, token, half)
    assert whole_word_checked, "no compound was checked as a whole word"


def test_quote_with_altered_word_fails(corpus: Corpus) -> None:
    p, all_words, i = _figure(corpus)
    words = all_words[i - 3 : i + 2]
    assert _check(corpus, p, words)
    target = max(range(len(words)), key=lambda i: len(words[i]))
    altered = list(words)
    altered[target] = words[target][::-1] + "x"
    assert not corpus.verify_quote(p.instrument_ref, p.para, " ".join(altered)).matches


def test_quote_from_another_paragraph_fails(corpus: Corpus) -> None:
    row = load_supersession()[0]
    old = corpus.paragraph(row.old_instrument_ref, row.old_para)
    new = corpus.paragraph(row.new_instrument_ref, row.new_para)
    assert old is not None and new is not None
    first_line_of_old = old.text.split("\n")[0]
    assert not corpus.verify_quote(new.instrument_ref, new.para, first_line_of_old).matches


def test_empty_quote_is_rejected(corpus: Corpus) -> None:
    p, _, _ = _figure(corpus)
    with pytest.raises(ValueError):
        corpus.verify_quote(p.instrument_ref, p.para, "  \n ")


# ---- in-force status ------------------------------------------------------------------------------------


def test_in_force_status_matches_instrument_records(corpus: Corpus) -> None:
    for raw in _raw("instruments.json"):
        assert corpus.in_force(raw["instrument_ref"]) is raw["in_force"]
        if raw["in_force"]:
            assert raw["valid_to"] is None and raw["superseded_by"] is None
            assert raw["updated_as_on"] is not None
        else:
            assert raw["valid_to"] is not None and raw["superseded_by"] is not None
            assert raw["status_evidence"], "a replaced instrument needs RBI evidence for its status"


def test_paragraph_status_follows_its_instrument(corpus: Corpus) -> None:
    for p in corpus.paragraphs.values():
        inst = corpus.instrument(p.instrument_ref)
        assert (p.in_force, p.valid_to, p.superseded_by) == (inst.in_force, inst.valid_to, inst.superseded_by)


def test_every_replaced_instrument_leads_to_the_one_in_force(corpus: Corpus) -> None:
    current = _in_force_ref(corpus)
    for ref in corpus.instruments:
        assert corpus.current_instrument(ref).instrument_ref == current


def test_exactly_one_current_instrument_and_three_replaced(corpus: Corpus) -> None:
    states = sorted(inst.in_force for inst in corpus.instruments.values())
    assert states == [False] * (len(bc.INSTRUMENTS) - 1) + [True]
    assert {s.instrument_ref for s in bc.INSTRUMENTS} == set(corpus.instruments)


# ---- dates and who withdrew what ------------------------------------------------------------------------


def _specs() -> dict[str, bc.InstrumentSpec]:
    return {s.instrument_ref: s for s in bc.INSTRUMENTS}


def test_withdrawn_by_comes_from_the_build_specs(corpus: Corpus) -> None:
    specs = _specs()
    for ref, inst in corpus.instruments.items():
        assert inst.withdrawn_by == specs[ref].withdrawn_by
        assert (inst.withdrawn_by is None) is inst.in_force
    # The Digital Lending Directions were withdrawn by the consolidation circular RBI/2025-26/100 of
    # 28 Nov 2025; the 2022 and 2023 circulars by Annex III of the Directions themselves.
    dl = next(s for s in bc.INSTRUMENTS if s.key == "dl_directions_2025")
    assert corpus.instrument(dl.instrument_ref).withdrawn_by == bc.CONSOLIDATION_REF == "RBI/2025-26/100"
    for key in ("dl_guidelines_2022", "dlg_guidelines_2023"):
        spec = next(s for s in bc.INSTRUMENTS if s.key == key)
        assert corpus.instrument(spec.instrument_ref).withdrawn_by == dl.instrument_ref


def test_rbi_as_on_is_the_in_force_instruments_updated_as_on(corpus: Corpus) -> None:
    current = corpus.instrument(_in_force_ref(corpus))
    assert current.updated_as_on is not None
    assert corpus.rbi_as_on() == current.updated_as_on
    assert corpus.rbi_as_on() != current.snapshot_date  # RBI's date, not the fetch date


def _rebuilt(corpus: Corpus, ref: str, **update: Any) -> Corpus:
    instruments = [
        inst.model_copy(update=update) if inst.instrument_ref == ref else inst
        for inst in corpus.instruments.values()
    ]
    return Corpus(instruments, list(corpus.paragraphs.values()), corpus.supersession)


def test_load_fails_when_the_instrument_in_force_has_no_updated_as_on(corpus: Corpus) -> None:
    with pytest.raises(CorpusError, match="no updated_as_on"):
        _rebuilt(corpus, _in_force_ref(corpus), updated_as_on=None)


def test_load_fails_when_a_withdrawn_instrument_lacks_withdrawn_by_or_valid_to(corpus: Corpus) -> None:
    ref = next(r for r, i in corpus.instruments.items() if not i.in_force)
    for update in ({"withdrawn_by": None}, {"valid_to": None}):
        with pytest.raises(CorpusError, match="lacks valid_to or withdrawn_by"):
            _rebuilt(corpus, ref, **update)


def test_load_fails_when_an_instrument_in_force_says_it_was_withdrawn(corpus: Corpus) -> None:
    ref = _in_force_ref(corpus)
    with pytest.raises(CorpusError, match="in force but has a valid_to or withdrawn_by"):
        _rebuilt(corpus, ref, withdrawn_by=bc.CONSOLIDATION_REF)


def test_load_fails_when_a_paragraph_disagrees_with_its_instrument_on_status(corpus: Corpus) -> None:
    paragraphs = list(corpus.paragraphs.values())
    p = next(q for q in paragraphs if not q.in_force)
    flipped = [q.model_copy(update={"in_force": True}) if q is p else q for q in paragraphs]
    with pytest.raises(CorpusError, match="differs from its instrument"):
        Corpus(list(corpus.instruments.values()), flipped, corpus.supersession)


def test_rbi_as_on_refuses_a_library_without_exactly_one_instrument_in_force(corpus: Corpus) -> None:
    current = corpus.instrument(_in_force_ref(corpus))
    second = current.model_copy(update={"instrument_ref": "RBI/TEST/2", "key": "second"})
    for instruments in ([current, second], []):
        with pytest.raises(CorpusError, match="exactly one instrument in force"):
            Corpus(instruments, [], []).rbi_as_on()


def test_build_refuses_a_spec_whose_withdrawn_by_disagrees_with_its_status() -> None:
    import dataclasses

    from bs4 import BeautifulSoup

    empty = BeautifulSoup("", "html.parser")
    for spec in bc.INSTRUMENTS:
        wrong = dataclasses.replace(spec, withdrawn_by=None if spec.withdrawn_by else bc.CONSOLIDATION_REF)
        with pytest.raises(bc.BuildError, match="withdrawn_by"):
            bc.status_evidence(wrong, {}, empty, "")


# ---- supersession ---------------------------------------------------------------------------------------


def test_supersession_rows_reference_existing_instruments_and_paragraphs(corpus: Corpus) -> None:
    rows = load_supersession()
    assert 10 <= len(rows) <= 15
    assert len({(r.old_instrument_ref, r.old_para) for r in rows}) == len(rows)
    for r in rows:
        assert r.old_instrument_ref in corpus.instruments
        assert r.new_instrument_ref in corpus.instruments
        assert corpus.paragraph(r.old_instrument_ref, r.old_para) is not None
        assert corpus.paragraph(r.new_instrument_ref, r.new_para) is not None
        assert not corpus.in_force(r.old_instrument_ref)
        assert corpus.in_force(r.new_instrument_ref)
        assert r.source_url.startswith("https://www.rbi.org.in/") and r.old_url.startswith(
            "https://www.rbi.org.in/"
        )
        assert r.source_url in {i.url for i in corpus.instruments.values()}
        assert r.relation in {"carried_over", "amended"} and r.note


def test_successors_found_through_parent_paragraph(corpus: Corpus) -> None:
    row = next(r for r in load_supersession() if "(" not in r.old_para)
    child = next(
        p
        for p in corpus.paragraphs.values()
        if p.instrument_ref == row.old_instrument_ref and p.parent_para == row.old_para
    )
    assert row in corpus.successors(row.old_instrument_ref, child.para)


def test_supersession_to_unknown_paragraph_is_rejected(corpus: Corpus, tmp_path: Path) -> None:
    data = json.loads(SUPERSESSION_PATH.read_text(encoding="utf-8"))
    ref = data["rows"][0]["new_instrument_ref"]
    top_numbers = [
        int(p.para) for p in corpus.paragraphs.values() if p.instrument_ref == ref and p.para.isdigit()
    ]
    data["rows"][0]["new_para"] = str(max(top_numbers) + 1000)
    bad = tmp_path / "supersession.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CorpusError):
        Corpus.load(FIXTURE_DIR, bad)


def test_tampered_text_is_rejected(corpus: Corpus) -> None:
    instruments = [Instrument.model_validate(r) for r in _raw("instruments.json")]
    paragraphs = _raw("paragraphs.json")
    paragraphs[0]["text"] = paragraphs[0]["text"] + " extra"
    with pytest.raises(CorpusError):
        Corpus(instruments, [Paragraph.model_validate(p) for p in paragraphs], corpus.supersession)


# ---- parser round trip on real text ---------------------------------------------------------------------


def _blocks_for(p: Paragraph, corpus: Corpus) -> list[bc.Block]:
    blocks = [bc.Block("p", line, False) for line in p.text.split("\n")]
    assert blocks and corpus.paragraph(p.instrument_ref, p.para) is not None
    return blocks


def test_parser_rebuilds_a_real_paragraph(corpus: Corpus) -> None:
    ref = _in_force_ref(corpus)
    p = next(
        x
        for x in corpus.paragraphs.values()
        if x.instrument_ref == ref
        and x.level == 1
        and x.para.isdigit()
        and "Note" not in x.text
        and not x.omitted
    )
    parsed = bc._parse_region(_blocks_for(p, corpus), bc.Region("", "", "paren", (p.para,)))
    for para in parsed.units:
        expected = corpus.paragraph(ref, para)
        assert expected is not None
        subtree = bc._descendants(parsed.units, para)
        text = "\n".join(line for owner, line in parsed.lines if owner in subtree)
        assert content_hash(text) == expected.content_hash


def test_parser_fails_on_a_missing_sub_paragraph(corpus: Corpus) -> None:
    ref = _in_force_ref(corpus)
    p = next(
        x
        for x in corpus.paragraphs.values()
        if x.instrument_ref == ref and x.level == 1 and x.para.isdigit() and x.text.count("\n(") >= 2
    )
    blocks = [b for b in _blocks_for(p, corpus) if not b.text.startswith("(1) ")]
    with pytest.raises(bc.BuildError):
        bc._parse_region(blocks, bc.Region("", "", "paren", (p.para,)))


# ---- Firestore loader -----------------------------------------------------------------------------------


class _FakeDoc:
    def __init__(self, path: tuple[str, str]) -> None:
        self.path = path


class _FakeCollection:
    def __init__(self, name: str) -> None:
        self.name = name

    def document(self, doc_id: str) -> _FakeDoc:
        return _FakeDoc((self.name, doc_id))


class _FakeBatch:
    def __init__(self, store: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.store = store
        self.pending: list[tuple[tuple[str, str], dict[str, Any]]] = []

    def set(self, ref: _FakeDoc, data: dict[str, Any]) -> None:
        self.pending.append((ref.path, data))

    def commit(self) -> None:
        self.store.update(self.pending)


class _FakeClient:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], dict[str, Any]] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(name)

    def batch(self) -> _FakeBatch:
        return _FakeBatch(self.store)


def test_load_to_firestore_writes_all_three_collections(corpus: Corpus) -> None:
    client = _FakeClient()
    counts = load_to_firestore(client, corpus)
    assert counts == {
        COLLECTION_INSTRUMENTS: len(corpus.instruments),
        COLLECTION_PARAGRAPHS: len(corpus.paragraphs),
        COLLECTION_SUPERSESSION: len(corpus.supersession),
    }
    assert len(client.store) == sum(counts.values())
    assert all("/" not in doc_id for _, doc_id in client.store)
    some = next(iter(corpus.paragraphs.values()))
    stored = client.store[(COLLECTION_PARAGRAPHS, f"{some.instrument_key}__{some.para}")]
    assert stored["text"] == some.text and stored["snapshot_date"] == some.snapshot_date.isoformat()


# ---- snapshot date comes from the recorded fetch --------------------------------------------------------


def _fake_raw(tmp_path: Path, fetched_at: str = "2026-10-16T11:47:03+00:00") -> Path:
    """A raw/ directory with a tiny page per target and the manifest a fetch would write for it."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    pages = {}
    for key, url in bc._targets():
        body = f"<html><body>{key}</body></html>".encode()
        (raw_dir / f"{key}.html").write_bytes(body)
        pages[key] = {
            "url": url,
            "fetched_at": fetched_at,
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
        }
    snapshot = datetime.fromisoformat(fetched_at).astimezone(bc.SNAPSHOT_TZ).date()
    manifest = {"snapshot_date": snapshot.isoformat(), "timezone": str(bc.SNAPSHOT_TZ), "pages": pages}
    (raw_dir / bc.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    return raw_dir


def test_offline_snapshot_date_is_the_recorded_fetch_date(tmp_path: Path) -> None:
    fetched_at = "2026-10-16T20:00:00+00:00"  # already 17 Oct in IST
    _fake_raw(tmp_path, fetched_at)
    *_, snapshot = bc._load_pages(tmp_path, offline=True)
    assert snapshot == datetime.fromisoformat(fetched_at).astimezone(bc.SNAPSHOT_TZ).date()
    assert snapshot != datetime.fromisoformat(fetched_at).date()


def test_offline_without_a_manifest_fails(tmp_path: Path) -> None:
    raw_dir = _fake_raw(tmp_path)
    (raw_dir / bc.MANIFEST_NAME).unlink()
    with pytest.raises(bc.BuildError, match="fetch date is unknown"):
        bc._load_pages(tmp_path, offline=True)


def test_offline_with_a_changed_page_fails(tmp_path: Path) -> None:
    raw_dir = _fake_raw(tmp_path)
    key = bc._targets()[0][0]
    (raw_dir / f"{key}.html").write_bytes(b"<html>edited</html>")
    with pytest.raises(bc.BuildError, match="changed since it was fetched"):
        bc._load_pages(tmp_path, offline=True)


def test_manifest_whose_pages_disagree_with_its_date_fails(tmp_path: Path) -> None:
    raw_dir = _fake_raw(tmp_path)
    path = raw_dir / bc.MANIFEST_NAME
    manifest = json.loads(path.read_text(encoding="utf-8"))
    recorded = date.fromisoformat(manifest["snapshot_date"])
    manifest["snapshot_date"] = (recorded + timedelta(days=1)).isoformat()
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(bc.BuildError, match="was fetched on"):
        bc._load_pages(tmp_path, offline=True)


def test_snapshot_date_cannot_be_set_by_hand() -> None:
    with pytest.raises(SystemExit):
        bc.main(["--offline", "--snapshot-date", "2030-01-01"])
