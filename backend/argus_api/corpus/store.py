"""Read-only access to the RBI library built by build_corpus.py, plus the Firestore loader.

The deterministic checks the step monitor needs live here:
  paragraph(ref, para)          does this official paragraph exist in the snapshot?
  verify_quote(ref, para, q)    does the quoted text appear verbatim, on word boundaries, figures uncut?
  in_force(ref)                 is the instrument in force on the snapshot date?
  current_instrument(ref)       follow superseded_by to the instrument in force now
"""

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from argus_api.corpus.common import DEFAULT_OUT_DIR, SUPERSESSION_PATH, content_hash, normalise

FIRESTORE_BATCH_LIMIT = 500
COLLECTION_INSTRUMENTS = "rbi_instruments"
COLLECTION_PARAGRAPHS = "rbi_paragraphs"
COLLECTION_SUPERSESSION = "supersession"


class CorpusError(RuntimeError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Evidence(_Strict):
    url: str
    quote: str


class Instrument(_Strict):
    key: str
    instrument: str
    instrument_ref: str
    circular_no: str
    issued_on: date
    updated_as_on: date | None
    url: str
    snapshot_date: date
    page_sha256: str
    in_force: bool
    valid_from: date
    valid_to: date | None
    superseded_by: str | None
    status_evidence: list[Evidence]
    scope: str
    paragraph_count: int


class Paragraph(_Strict):
    id: str
    instrument: str
    instrument_ref: str
    instrument_key: str
    para: str
    parent_para: str | None
    level: int
    section: str | None
    text: str
    url: str
    snapshot_date: date
    content_hash: str
    in_force: bool
    valid_from: date
    valid_to: date | None
    superseded_by: str | None
    footnotes: list[str]
    omitted: list[str]


class SupersessionRow(_Strict):
    old_instrument_ref: str
    old_para: str
    new_instrument_ref: str
    new_para: str
    relation: str
    topic: str
    source_url: str
    old_url: str
    note: str


class QuoteCheck(_Strict):
    paragraph_found: bool
    matches: bool


# Spelled-out quantities. A quote that stops on one of these (or on digits) has cut a figure off from what
# follows it in the source ("less than one" of "less than one day", "five" of "five per cent").
NUMBER_WORDS = frozenset(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand "
    "lakh lakhs crore crores million billion".split()
)
_LAST_WORD = re.compile(r"([^\W_]+)$")
_CONTINUES_AFTER_NUMBER = re.compile(r"\s*[^\W_]|[.,]\d")
_NUMBER_BEFORE = re.compile(r"\d[.,]$")
# Characters that join two words into one compound or range when they sit between letters or digits with no
# space: hyphen-minus, Unicode hyphen, non-breaking hyphen, and the en dash of ranges ("10–15").
# "twenty-four", "one-time" and "10-15" are each one word: a quote may not start or end inside them.
WORD_JOINERS = frozenset("-\u2010\u2011\u2013")


def is_number_token(token: str) -> bool:
    return token.isdigit() or token.lower() in NUMBER_WORDS


def _inside_word(t: str, i: int) -> bool:
    """Is the position between t[i-1] and t[i] inside one word (so a quote edge there would cut it)?

    A word is a run of letters and digits, and a joiner between two such characters keeps the run going:
    in "twenty-four" there is no word edge on either side of the hyphen.
    """
    if i <= 0 or i >= len(t):
        return False
    before, after = t[i - 1], t[i]
    if before.isalnum() and after.isalnum():
        return True
    if before.isalnum() and after in WORD_JOINERS:
        return i + 1 < len(t) and t[i + 1].isalnum()
    if before in WORD_JOINERS and after.isalnum():
        return i >= 2 and t[i - 2].isalnum()
    return False


def _clean_occurrence(q: str, t: str, start: int) -> bool:
    """Is the match of `q` at `t[start:]` a whole-word quote that keeps every figure intact?"""
    end = start + len(q)
    # Start and end must fall on word edges: "2" must not match inside "24", "Bank" inside "Banks", "twenty"
    # inside "twenty-four", nor "four hours" inside "twenty-four hours".
    if _inside_word(t, start) or _inside_word(t, end):
        return False
    # A figure must not start mid-number: "5 per cent" is not a quote of "2.5 per cent".
    if q[0].isdigit() and _NUMBER_BEFORE.search(t[:start]):
        return False
    # A figure must not be cut from what follows it: its unit, the rest of a decimal, or the next words.
    last = _LAST_WORD.search(q)
    if last is not None and is_number_token(last.group(1)) and _CONTINUES_AFTER_NUMBER.match(t, end):
        return False
    return True


def quote_in_text(quote: str, text: str) -> bool:
    """True when `quote` appears verbatim in `text` (both whitespace-normalised) as a clean quote.

    Clean means the quote starts and ends on word edges (a hyphenated compound such as "twenty-four" or
    "one-time" is one word) and never cuts a figure: a quote may not end on a
    number (digits or a spelled-out number) while the source goes on with more words, and may not start in the
    middle of a decimal or grouped number. Case, punctuation and wording are never forgiven.
    """
    q = normalise(quote)
    if not q:
        raise ValueError("empty quote: an empty string would match every paragraph")
    t = normalise(text)
    start = t.find(q)
    while start != -1:
        if _clean_occurrence(q, t, start):
            return True
        start = t.find(q, start + 1)
    return False


def _read_list(path: Path) -> list[Any]:
    if not path.exists():
        raise CorpusError(f"{path} not found; build it with: python -m argus_api.corpus.build_corpus")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise CorpusError(f"{path} must hold a JSON list")
    return data


def load_supersession(path: Path = SUPERSESSION_PATH) -> list[SupersessionRow]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [SupersessionRow.model_validate(row) for row in data["rows"]]


class Corpus:
    def __init__(
        self, instruments: list[Instrument], paragraphs: list[Paragraph], supersession: list[SupersessionRow]
    ) -> None:
        self.instruments: dict[str, Instrument] = {}
        for inst in instruments:
            if inst.instrument_ref in self.instruments:
                raise CorpusError(f"duplicate instrument {inst.instrument_ref}")
            self.instruments[inst.instrument_ref] = inst
        self.paragraphs: dict[tuple[str, str], Paragraph] = {}
        for p in paragraphs:
            key = (p.instrument_ref, p.para)
            if key in self.paragraphs:
                raise CorpusError(f"duplicate paragraph {key}")
            if p.instrument_ref not in self.instruments:
                raise CorpusError(f"paragraph {p.id} belongs to unknown instrument {p.instrument_ref}")
            if content_hash(p.text) != p.content_hash:
                raise CorpusError(f"paragraph {p.id}: text does not match its content_hash")
            self.paragraphs[key] = p
        self.supersession = supersession
        self._check_supersession()

    @classmethod
    def load(cls, directory: Path = DEFAULT_OUT_DIR, supersession_path: Path = SUPERSESSION_PATH) -> "Corpus":
        instruments = [Instrument.model_validate(r) for r in _read_list(directory / "instruments.json")]
        paragraphs = [Paragraph.model_validate(r) for r in _read_list(directory / "paragraphs.json")]
        return cls(instruments, paragraphs, load_supersession(supersession_path))

    def _check_supersession(self) -> None:
        for row in self.supersession:
            for ref, para in ((row.old_instrument_ref, row.old_para), (row.new_instrument_ref, row.new_para)):
                if (ref, para) not in self.paragraphs:
                    raise CorpusError(f"supersession row cites {ref} para {para}, which is not in the corpus")
            if self.instrument(row.old_instrument_ref).in_force:
                raise CorpusError(f"supersession row maps from {row.old_instrument_ref}, which is in force")
            if not self.instrument(row.new_instrument_ref).in_force:
                raise CorpusError(f"supersession row maps to {row.new_instrument_ref}, which is not in force")
            if self.current_instrument(row.old_instrument_ref).instrument_ref != row.new_instrument_ref:
                raise CorpusError(f"{row.old_instrument_ref} does not lead to {row.new_instrument_ref}")

    def instrument(self, instrument_ref: str) -> Instrument:
        try:
            return self.instruments[instrument_ref]
        except KeyError:
            raise CorpusError(f"unknown instrument {instrument_ref!r}") from None

    def paragraph(self, instrument_ref: str, para: str) -> Paragraph | None:
        """The paragraph, or None when the snapshot has no such paragraph (for example a made-up number)."""
        return self.paragraphs.get((instrument_ref, para))

    def verify_quote(self, instrument_ref: str, para: str, quote: str) -> QuoteCheck:
        p = self.paragraph(instrument_ref, para)
        if p is None:
            return QuoteCheck(paragraph_found=False, matches=False)
        return QuoteCheck(paragraph_found=True, matches=quote_in_text(quote, p.text))

    def in_force(self, instrument_ref: str) -> bool:
        return self.instrument(instrument_ref).in_force

    def current_instrument(self, instrument_ref: str) -> Instrument:
        """Follow superseded_by until an instrument in force is reached."""
        seen: list[str] = []
        inst = self.instrument(instrument_ref)
        while not inst.in_force:
            seen.append(inst.instrument_ref)
            if inst.superseded_by is None:
                raise CorpusError(f"{inst.instrument_ref} is not in force and has no successor")
            if inst.superseded_by in seen:
                raise CorpusError(f"supersession cycle: {seen + [inst.superseded_by]}")
            inst = self.instrument(inst.superseded_by)
        return inst

    def successors(self, instrument_ref: str, para: str) -> list[SupersessionRow]:
        """Supersession rows for an old paragraph (also matches rows for its parent paragraphs)."""
        p = self.paragraph(instrument_ref, para)
        chain = {para}
        while p is not None and p.parent_para is not None:
            chain.add(p.parent_para)
            p = self.paragraph(instrument_ref, p.parent_para)
        return [
            r for r in self.supersession if r.old_instrument_ref == instrument_ref and r.old_para in chain
        ]

    def rule_moves(self, instrument_ref: str, para: str) -> list[SupersessionRow]:
        """Where the rule of an old paragraph now sits: successors() for the paragraph and its parents, plus
        the rows of its sub-paragraphs. A policy that cites a whole paragraph ("para 10") whose rule moved
        sub-paragraph by sub-paragraph ("10(i)") needs the rows of its children as well."""
        rows = self.successors(instrument_ref, para)
        for p in self.paragraphs.values():
            if p.instrument_ref != instrument_ref or p.para == para:
                continue
            parent = p.parent_para
            while parent is not None and parent != para:
                up = self.paragraph(instrument_ref, parent)
                parent = up.parent_para if up is not None else None
            if parent == para:
                rows += [r for r in self.successors(instrument_ref, p.para) if r not in rows]
        return rows

    def paragraphs_under(self, instrument_ref: str, para: str) -> list[str]:
        """The paragraph ids of an instrument that share `para`'s top-level number ("14(9)" -> "14",
        "14(1)", ...), sorted: what a reader is shown when a cited paragraph does not exist."""
        top = re.match(r"[^(.]+", para)
        stem = top.group(0) if top else para
        return sorted(
            q
            for (ref, q) in self.paragraphs
            if ref == instrument_ref and re.match(rf"{re.escape(stem)}\b", q)
        )


class FirestoreLike(Protocol):
    def collection(self, collection_id: str) -> Any: ...

    def batch(self) -> Any: ...


def _doc_id(value: str) -> str:
    """Firestore ids may not contain '/'; instrument refs do."""
    out = value.replace("/", "_")
    if not out or out in (".", "..") or out.startswith("__"):
        raise CorpusError(f"unusable Firestore document id {value!r}")
    return out


def load_to_firestore(client: FirestoreLike, corpus: Corpus | None = None) -> dict[str, int]:
    """Write rbi_instruments, rbi_paragraphs and supersession. Returns the number of documents per collection.

    Documents are overwritten (set), so re-running the load after a rebuild is safe.
    """
    corpus = corpus if corpus is not None else Corpus.load()
    writes: list[tuple[str, str, dict[str, Any]]] = []
    for inst in corpus.instruments.values():
        writes.append((COLLECTION_INSTRUMENTS, _doc_id(inst.key), inst.model_dump(mode="json")))
    for p in corpus.paragraphs.values():
        writes.append(
            (COLLECTION_PARAGRAPHS, _doc_id(f"{p.instrument_key}__{p.para}"), p.model_dump(mode="json"))
        )
    for i, row in enumerate(corpus.supersession, start=1):
        writes.append((COLLECTION_SUPERSESSION, f"row-{i:02d}", row.model_dump(mode="json")))

    ids = [(c, d) for c, d, _ in writes]
    if len(set(ids)) != len(ids):
        raise CorpusError("two documents map to the same Firestore id")
    for start in range(0, len(writes), FIRESTORE_BATCH_LIMIT):
        batch = client.batch()
        for collection, doc_id, data in writes[start : start + FIRESTORE_BATCH_LIMIT]:
            batch.set(client.collection(collection).document(doc_id), data)
        batch.commit()
    counts: dict[str, int] = {}
    for collection, _, _ in writes:
        counts[collection] = counts.get(collection, 0) + 1
    return counts
