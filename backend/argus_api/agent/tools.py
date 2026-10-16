"""The agent's three tools over the RBI library, as ToolSpecs for the model and plain Python functions.

search_library(query, include_replaced)  top paragraphs by BM25 keyword scoring (no embeddings)
read_paragraph(instrument_ref, para)     full text, in-force status and where a replaced rule now sits
check_status(instrument_ref)             in force or not, end date, replacement, instrument now in force

A tool call the model gets wrong (unknown tool, bad arguments, a paragraph that does not exist) is answered
with a ToolResult whose `error` says what was wrong, so the model can correct itself; the step trace shows
the failure. Corpus integrity problems (CorpusError) are not caught here and stop the review.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import jsonschema

from argus_api.corpus.common import normalise
from argus_api.corpus.store import Corpus, Paragraph
from argus_api.llm import ToolSpec

SEARCH_TOP_K = 8
SNIPPET_CHARS = 300
# BM25 parameters (the usual defaults).
BM25_K1 = 1.5
BM25_B = 0.75

TOOL_SEARCH = "search_library"
TOOL_READ = "read_paragraph"
TOOL_STATUS = "check_status"

TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name=TOOL_SEARCH,
        description=(
            "Keyword search over the paragraphs of the RBI library snapshot. Returns up to "
            f"{SEARCH_TOP_K} paragraphs with instrument_ref, para, in_force and the first {SNIPPET_CHARS} "
            "characters. Set include_replaced to true only to look up withdrawn or replaced instruments "
            "(for example the one a policy clause cites)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Plain keywords, e.g. 'cooling off period'."},
                "include_replaced": {
                    "type": "boolean",
                    "description": "False: only instruments in force. True: also withdrawn ones.",
                },
            },
            "required": ["query", "include_replaced"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name=TOOL_READ,
        description=(
            "Read one official paragraph in full, with its in-force status and, for a replaced rule, the "
            "paragraph that now carries it. Paragraph ids look like '10(4)', '9(ii)', '8(iv)(a)' or '5.2.1'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "instrument_ref": {"type": "string", "description": "e.g. 'RBI/DOR/2025-26/347'."},
                "para": {"type": "string", "description": "Official paragraph number, e.g. '10(4)'."},
            },
            "required": ["instrument_ref", "para"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name=TOOL_STATUS,
        description=(
            "Status of one RBI instrument in the library: in force or not, the date it ended, what replaced "
            "it, the instrument in force now, and RBI's own wording as evidence."
        ),
        parameters={
            "type": "object",
            "properties": {"instrument_ref": {"type": "string", "description": "e.g. 'RBI/2025-26/36'."}},
            "required": ["instrument_ref"],
            "additionalProperties": False,
        },
    ),
)
TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOL_SPECS}


@dataclass(frozen=True)
class ToolResult:
    """What a tool returned: `output` for the model, plus a one-line plain-English summary for the trace."""

    output: dict[str, Any]
    summary: str
    error: str | None = None
    paragraphs: list[Paragraph] = field(default_factory=list)  # paragraphs whose full text was returned
    # read_paragraph only: the (instrument_ref, para) the agent asked to read, found or not, for the step
    # monitor's check. None for other tools and for calls whose arguments did not validate.
    reference: tuple[str, str] | None = None


# ---- BM25 keyword index --------------------------------------------------------------------------------

_WORD = re.compile(r"[^\W_]+")
STOPWORDS = frozenset(
    "a an and any are as at be by for from has have in is it its of on or shall such that the their this "
    "to under which with all also been being can may must not other was were will".split()
)


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall(normalise(text).lower()) if w not in STOPWORDS]


class LibraryIndex:
    """BM25 over every paragraph of the corpus, built once per Corpus."""

    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self._docs: list[tuple[Paragraph, Counter[str], int]] = []
        df: Counter[str] = Counter()
        for p in corpus.paragraphs.values():
            terms = Counter(tokenize(p.text))
            self._docs.append((p, terms, sum(terms.values())))
            df.update(terms.keys())
        n = len(self._docs)
        if n == 0:
            raise ValueError("cannot index an empty corpus")
        self._avg_len = sum(length for _, _, length in self._docs) / n
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(
        self, query: str, include_replaced: bool, k: int = SEARCH_TOP_K
    ) -> list[tuple[Paragraph, float]]:
        q = set(tokenize(query))
        scored: list[tuple[Paragraph, float]] = []
        for p, terms, length in self._docs:
            if not include_replaced and not p.in_force:
                continue
            score = 0.0
            for t in q:
                tf = terms.get(t, 0)
                if tf:
                    norm = BM25_K1 * (1 - BM25_B + BM25_B * length / self._avg_len)
                    score += self._idf[t] * tf * (BM25_K1 + 1) / (tf + norm)
            if score > 0:
                scored.append((p, score))
        # Ties broken by instrument and paragraph so results are reproducible.
        scored.sort(key=lambda ps: (-ps[1], ps[0].instrument_ref, ps[0].para))
        return scored[:k]


@lru_cache(maxsize=4)
def library_index(corpus: Corpus) -> LibraryIndex:
    return LibraryIndex(corpus)


# ---- the tools -------------------------------------------------------------------------------------------


def _status_words(corpus: Corpus, instrument_ref: str) -> str:
    inst = corpus.instrument(instrument_ref)
    if inst.in_force:
        return "in force"
    ended = f" since {inst.valid_to.isoformat()}" if inst.valid_to else ""
    return f"not in force{ended}"


def search_library(index: LibraryIndex, query: str, include_replaced: bool) -> ToolResult:
    if not query.strip():
        return _error(TOOL_SEARCH, "query is empty", "Tried to search the library with an empty query.")
    hits = index.search(query, include_replaced)
    scope = "all instruments" if include_replaced else "instruments in force"
    results = [
        {
            "instrument_ref": p.instrument_ref,
            "para": p.para,
            "in_force": p.in_force,
            "text_start": normalise(p.text)[:SNIPPET_CHARS],
        }
        for p, _ in hits
    ]
    if hits:
        top = hits[0][0]
        found = (
            f"{len(hits)} result{'s' if len(hits) != 1 else ''}, top: para {top.para} of {top.instrument_ref}"
        )
    else:
        found = "no results"
    return ToolResult(
        output={"query": query, "include_replaced": include_replaced, "results": results},
        summary=f'Searched {scope} for "{_one_line(query)}": {found}.',
    )


def read_paragraph(corpus: Corpus, instrument_ref: str, para: str) -> ToolResult:
    if instrument_ref not in corpus.instruments:
        return _error(
            TOOL_READ,
            f"{instrument_ref!r} is not an instrument in the library; known: {sorted(corpus.instruments)}",
            f"Tried to read para {para} of {instrument_ref}: not an instrument in the library.",
            reference=(instrument_ref, para),
        )
    p = corpus.paragraph(instrument_ref, para)
    if p is None:
        top = re.match(r"[^(.]+", para)
        stem = top.group(0) if top else para
        return _error(
            TOOL_READ,
            f"paragraph {para!r} does not exist in {instrument_ref} in the snapshot; paragraphs under "
            f"{stem!r} in the library: {corpus.paragraphs_under(instrument_ref, para)}",
            f"Tried to read para {para} of {instrument_ref}: no such paragraph in the snapshot.",
            reference=(instrument_ref, para),
        )
    successors = [
        {
            "new_instrument_ref": r.new_instrument_ref,
            "new_para": r.new_para,
            "relation": r.relation,
            "note": r.note,
        }
        for r in corpus.rule_moves(instrument_ref, para)
    ]
    output: dict[str, Any] = {
        "instrument": p.instrument,
        "instrument_ref": p.instrument_ref,
        "para": p.para,
        "in_force": p.in_force,
        "valid_to": p.valid_to.isoformat() if p.valid_to else None,
        "text": p.text,
        "now_at": successors,
    }
    status = _status_words(corpus, instrument_ref)
    moved = (
        "; rule now at " + ", ".join(f"para {s['new_para']} of {s['new_instrument_ref']}" for s in successors)
        if successors
        else ""
    )
    return ToolResult(
        output=output,
        summary=f"Read para {para} of {instrument_ref} ({status}{moved}).",
        paragraphs=[p],
        reference=(instrument_ref, para),
    )


def check_status(corpus: Corpus, instrument_ref: str) -> ToolResult:
    if instrument_ref not in corpus.instruments:
        return _error(
            TOOL_STATUS,
            f"{instrument_ref!r} is not an instrument in the library; known: {sorted(corpus.instruments)}",
            f"Tried to check the status of {instrument_ref}: not an instrument in the library.",
        )
    inst = corpus.instrument(instrument_ref)
    current = corpus.current_instrument(instrument_ref)
    output = {
        "instrument": inst.instrument,
        "instrument_ref": inst.instrument_ref,
        "in_force": inst.in_force,
        "valid_from": inst.valid_from.isoformat(),
        "valid_to": inst.valid_to.isoformat() if inst.valid_to else None,
        "replaced_by": inst.superseded_by,
        "current_instrument_ref": current.instrument_ref,
        "current_instrument": current.instrument,
        "status_evidence": [e.model_dump() for e in inst.status_evidence],
    }
    if inst.in_force:
        summary = f"Checked the status of {instrument_ref}: in force."
    else:
        summary = (
            f"Checked the status of {instrument_ref}: {_status_words(corpus, instrument_ref)}, "
            f"now replaced by {current.instrument_ref}."
        )
    return ToolResult(output=output, summary=summary)


def run_tool(corpus: Corpus, index: LibraryIndex, name: str, args: dict[str, Any]) -> ToolResult:
    """Validate the model's arguments against the tool's schema, then run it."""
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        return _error(
            name,
            f"unknown tool {name!r}; tools: {sorted(TOOLS_BY_NAME)}",
            f"Asked for an unknown tool {name!r}.",
        )
    try:
        jsonschema.validate(args, spec.parameters)
    except jsonschema.ValidationError as e:
        return _error(
            name, f"bad arguments: {e.message}", f"Called {name} with bad arguments: {_one_line(e.message)}."
        )
    if name == TOOL_SEARCH:
        return search_library(index, args["query"], args["include_replaced"])
    if name == TOOL_READ:
        return read_paragraph(corpus, args["instrument_ref"], args["para"])
    return check_status(corpus, args["instrument_ref"])


def _error(tool: str, message: str, summary: str, reference: tuple[str, str] | None = None) -> ToolResult:
    return ToolResult(output={"error": message}, summary=summary, error=message, reference=reference)


def _one_line(text: str, limit: int = 120) -> str:
    flat = normalise(text)
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."
