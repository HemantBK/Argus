"""Build the RBI paragraph library for NBFC digital lending from RBI's own HTML pages.

Usage (from argus/backend):
    .venv/bin/python -m argus_api.corpus.build_corpus            # fetch from rbi.org.in, then parse
    .venv/bin/python -m argus_api.corpus.build_corpus --offline  # re-parse the cached raw HTML only
    .venv/bin/python -m argus_api.corpus.build_corpus --offline --fixture-out tests/fixtures/corpus

Output (not committed; RBI text is rebuilt by this script):
    data/corpus/raw/<key>.html   the pages exactly as served, for reproducibility
    data/corpus/raw/manifest.json when each page was fetched, its URL and sha256; the snapshot date is the
                                 IST date of that fetch, so an --offline re-parse keeps the original date
    data/corpus/instruments.json one record per instrument, with status and the RBI evidence for it
    data/corpus/paragraphs.json  one record per official paragraph, sub-paragraph and list item

Only rbi.org.in HTML pages are fetched. The rbidocs.rbi.org.in PDFs sit behind a CAPTCHA and are never
requested. Every structural assumption is checked; anything unexpected stops the build with an error.
"""

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from argus_api.corpus.common import (
    DEFAULT_OUT_DIR,
    SAMPLE_EXPECTED_PATH,
    SUPERSESSION_PATH,
    content_hash,
    normalise,
)

USER_AGENT = "ArgusCorpusBuilder/0.1 (AI Builder Cup 2026 research project; one request every few seconds)"
REQUEST_DELAY_S = 4.0
REQUEST_TIMEOUT_S = 60
ALLOWED_HOSTS = ("https://www.rbi.org.in/", "https://rbi.org.in/")
WITHDRAWN_LIST_URL = "https://www.rbi.org.in/Scripts/NotificationUserWithdrawnCircular.aspx"
CONSOLIDATION_URL = "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=13170"  # RBI/2025-26/100
SNAPSHOT_TZ = ZoneInfo("Asia/Kolkata")  # RBI publishes on IST dates; the snapshot date is the IST fetch date
MANIFEST_NAME = "manifest.json"

Scheme = Literal["paren", "roman_dot", "dotted", "cover"]


class BuildError(RuntimeError):
    """Raised whenever a page does not look the way the parser expects."""


# --------------------------------------------------------------------------------------------------------
# Instruments
# --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Region:
    """A slice of the page between two heading paragraphs, parsed with one numbering scheme."""

    start: str  # text the first paragraph of the region starts with (the start paragraph itself is skipped)
    stop: str  # text of the first paragraph after the region
    scheme: Scheme
    expected_top: tuple[str, ...]  # the top-level paragraph numbers the region must contain, in order
    para_prefix: str = ""  # prefix for paragraph ids, e.g. "circular-" for a cover letter


@dataclass(frozen=True)
class InstrumentSpec:
    key: str
    instrument: str
    instrument_ref: str
    circular_no: str
    issued_on: date
    url: str
    in_force: bool
    valid_to: date | None
    superseded_by: str | None
    regions: tuple[Region, ...]
    scope_note: str
    # The instrument that withdrew or repealed this one (None while in force). Checked at build time against
    # the withdrawing page itself, which must show this reference (withdrawing_page below).
    withdrawn_by: str | None = None


def _seq(first: int, last: int) -> tuple[str, ...]:
    return tuple(str(n) for n in range(first, last + 1))


CF_REF = "RBI/DOR/2025-26/347"
DL_REF = "RBI/2025-26/36"
G22_REF = "RBI/2022-23/111"
DLG_REF = "RBI/2023-24/41"
CONSOLIDATION_REF = (
    "RBI/2025-26/100"  # Consolidation of Regulations - Withdrawal of circulars (CONSOLIDATION_URL)
)
CONSOLIDATION_DATE = date(2025, 11, 28)  # RBI/2025-26/100: DL Directions withdrawn "with immediate effect"
DL_ISSUE_DATE = date(2025, 5, 8)  # RBI/2025-26/36 para 30(i) + Annex III: 2022 and 2023 circulars repealed

INSTRUMENTS: tuple[InstrumentSpec, ...] = (
    InstrumentSpec(
        key="nbfc_cf_2025",
        instrument=(
            "Reserve Bank of India (Non-Banking Financial Companies – Credit Facilities) Directions, 2025"
        ),
        instrument_ref=CF_REF,
        circular_no="DOR.CRE.REC.No.266/07-01-008/2025-26",
        issued_on=CONSOLIDATION_DATE,
        url="https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12957",
        in_force=True,
        valid_to=None,
        superseded_by=None,
        regions=(
            Region("B. Applicability", "C. Definitions", "paren", ("3",)),
            Region(
                "Chapter III - Digital Lending", "Chapter IV - Lending against Gold", "paren", _seq(6, 30)
            ),
            Region(
                "A. Repeal and saving", "B. Application of other laws not barred", "paren", ("112", "113")
            ),
        ),
        scope_note="Para 3 (applicability), Chapter III Digital Lending (paras 6-30), paras 112-113.",
    ),
    InstrumentSpec(
        key="dl_directions_2025",
        instrument="Reserve Bank of India (Digital Lending) Directions, 2025",
        instrument_ref=DL_REF,
        circular_no="DOR.STR.REC.19/21.07.001/2025-26",
        issued_on=DL_ISSUE_DATE,
        url="https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12848",
        in_force=False,
        valid_to=CONSOLIDATION_DATE,
        superseded_by=CF_REF,
        withdrawn_by=CONSOLIDATION_REF,
        regions=(
            Region("Chapter I: Preliminary", "Annex I: Data to be submitted", "roman_dot", _seq(1, 30)),
        ),
        scope_note="Paras 1-30 (all chapters). Annexes are not parsed.",
    ),
    InstrumentSpec(
        key="dl_guidelines_2022",
        instrument="Guidelines on Digital Lending",
        instrument_ref=G22_REF,
        circular_no="DOR.CRE.REC.66/21.07.001/2022-23",
        issued_on=date(2022, 9, 2),
        url="https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12382",
        in_force=False,
        valid_to=DL_ISSUE_DATE,
        superseded_by=DL_REF,
        withdrawn_by=DL_REF,
        regions=(
            Region("Guidelines on Digital Lending", "Yours faithfully", "cover", _seq(1, 4), "circular-"),
            Region("Guidelines on Digital Lending", "\x00footnote", "dotted", _seq(1, 15)),
        ),
        scope_note="Cover circular paras 1-4 (ids circular-N) and Annex I paras 1-15. Annex II (KFS format) "
        "is a PDF and is not parsed.",
    ),
    InstrumentSpec(
        key="dlg_guidelines_2023",
        instrument="Guidelines on Default Loss Guarantee (DLG) in Digital Lending",
        instrument_ref=DLG_REF,
        circular_no="DOR.CRE.REC.21/21.07.001/2023-24",
        issued_on=date(2023, 6, 8),
        url="https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12514",
        in_force=False,
        valid_to=DL_ISSUE_DATE,
        superseded_by=DL_REF,
        withdrawn_by=DL_REF,
        regions=(
            Region(
                "Guidelines on Default Loss Guarantee (DLG) in Digital Lending",
                "Yours faithfully",
                "cover",
                _seq(1, 4),
                "circular-",
            ),
            Region(
                "Guidelines on Default Loss Guarantee (DLG) in Digital Lending", "____", "dotted", _seq(1, 14)
            ),
        ),
        scope_note="Cover circular paras 1-4 (ids circular-N) and Annex paras 1-14.",
    ),
)

# --------------------------------------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------------------------------------


def _block_text(el: Tag) -> tuple[str, list[str]]:
    """Visible text of one block with footnote markers removed; the footnote tooltips are returned apart."""
    clone = BeautifulSoup(str(el), "html.parser")
    notes: list[str] = []
    for sup in clone.find_all("sup"):
        title = sup.get("title")
        if isinstance(title, str) and title.strip():
            notes.append(normalise(title))
        sup.decompose()
    for br in clone.find_all("br"):
        br.replace_with("\n")
    # No separator: inline tags (links, spans) must not gain spaces the page does not show.
    return normalise(clone.get_text()), notes


def _roman_to_int(token: str) -> int:
    values = {"i": 1, "v": 5, "x": 10, "l": 50}
    total = 0
    for i, ch in enumerate(token):
        v = values[ch]
        total += -v if i + 1 < len(token) and values[token[i + 1]] > v else v
    return total


def _letter(n: int) -> str:
    if not 1 <= n <= 26:
        raise BuildError(f"list item number {n} is outside a-z")
    return chr(ord("a") + n - 1)


# --------------------------------------------------------------------------------------------------------
# Block stream: the region's paragraphs in document order
# --------------------------------------------------------------------------------------------------------


@dataclass
class Block:
    kind: Literal["p", "li", "table"]
    text: str
    is_head: bool
    footnotes: list[str] = field(default_factory=list)


def _is_head(p: Tag) -> bool:
    return "head" in p.get_attribute_list("class")


def _is_footnote(p: Tag) -> bool:
    return "footnote" in p.get_attribute_list("class")


def _region_blocks(soup: BeautifulSoup, region: Region, after: int) -> tuple[list[Block], int]:
    """Blocks strictly between the start paragraph and the stop paragraph.

    `after` is the index (in the p/table/ol/ul stream) from which the start marker is searched, so that two
    regions with the same start heading (cover letter, then annex) are found one after the other.
    Returns the blocks and the stream index of the stop marker.
    """
    stream = soup.find_all(["p", "table", "ol", "ul"])
    start_idx = None
    for i in range(after, len(stream)):
        el = stream[i]
        if el.name == "p" and normalise(el.get_text(" ")).startswith(region.start):
            start_idx = i
            break
    if start_idx is None:
        raise BuildError(f"region start {region.start!r} not found")

    blocks: list[Block] = []
    skip_inside: Tag | None = None
    for i in range(start_idx + 1, len(stream)):
        el = stream[i]
        if skip_inside is not None and skip_inside in el.parents:
            continue
        skip_inside = None
        if el.name == "p":
            text, notes = _block_text(el)
            if region.stop == "\x00footnote":
                if _is_footnote(el):
                    return blocks, i
            elif text.startswith(region.stop):
                return blocks, i
            if not text:
                continue
            in_li = el.find_parent("li") is not None
            blocks.append(Block("li" if in_li else "p", text, _is_head(el) and not in_li, notes))
        elif el.name == "table":
            text = normalise(el.get_text(" "))
            blocks.append(Block("table", text[:120], False))
            skip_inside = el
        else:  # ol / ul inside the region: each item must be exactly one <p>, which the stream visits
            for li in el.find_all("li", recursive=False):
                ps = li.find_all("p")
                if len(ps) != 1:
                    raise BuildError(
                        f"list item without exactly one <p>: {normalise(li.get_text(' '))[:80]!r}"
                    )
                stray = [s for s in li.children if isinstance(s, NavigableString) and s.strip()]
                if stray:
                    raise BuildError(f"text outside <p> in list item: {stray[0]!r}")
    raise BuildError(f"region stop {region.stop!r} not found after {region.start!r}")


# --------------------------------------------------------------------------------------------------------
# Paragraph tree
# --------------------------------------------------------------------------------------------------------


@dataclass
class Unit:
    para: str
    parent: str | None
    level: int
    is_item: bool
    section: str | None
    title: str
    footnotes: list[str] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)


@dataclass
class ParsedRegion:
    units: dict[str, Unit]
    lines: list[tuple[str, str]]  # (owning para id, line text) in document order


TOP_RE = re.compile(r"^(\d+)\.(?:\s|$)")
PAREN_SUB_RE = re.compile(r"^\((\d+)\)(?:\s|$)")
PAREN_ITEM_RE = re.compile(r"^\(([ivx]+)\)(?:\s|$)")
ROMAN_SUB_RE = re.compile(r"^([ivx]+)\.\s")
DOTTED_RE = re.compile(r"^(\d+(?:\.\d+)*)\.(?:\s|$)")


def _parse_region(blocks: list[Block], region: Region) -> ParsedRegion:
    units: dict[str, Unit] = {}
    lines: list[tuple[str, str]] = []
    section: str | None = None
    top: Unit | None = None  # current top-level paragraph
    sub: Unit | None = None  # current numbered sub-paragraph (paren / roman_dot)
    current: Unit | None = None  # deepest unit; plain text attaches to it unless it is a list item
    tops_seen: list[str] = []
    pfx = region.para_prefix

    def new_unit(para: str, parent: Unit | None, is_item: bool, title: str, notes: list[str]) -> Unit:
        if para in units:
            raise BuildError(f"duplicate paragraph {para!r}")
        unit = Unit(
            para,
            parent.para if parent else None,
            (parent.level + 1) if parent else 1,
            is_item,
            section,
            title,
            list(notes),
        )
        units[para] = unit
        return unit

    def attach_plain(text: str, notes: list[str]) -> None:
        nonlocal current
        if current is None:
            raise BuildError(f"text before the first numbered paragraph: {text[:80]!r}")
        owner = current
        while owner.is_item:
            assert owner.parent is not None
            owner = units[owner.parent]
        current = owner
        owner.footnotes.extend(notes)
        lines.append((owner.para, text))

    def start_top(num: str, text: str, notes: list[str]) -> None:
        nonlocal top, sub, current
        expected = str(int(tops_seen[-1]) + 1) if tops_seen else region.expected_top[0]
        if num != expected:
            raise BuildError(f"top-level paragraph {num} where {expected} was expected: {text[:80]!r}")
        tops_seen.append(num)
        top = new_unit(pfx + num, None, False, text, notes)
        sub = None
        current = top
        lines.append((top.para, text))

    for b in blocks:
        if b.kind == "table":
            if current is None:
                raise BuildError("table before the first paragraph")
            current.omitted.append(f"table: {b.text}")
            continue
        text, notes = b.text, b.footnotes

        if b.kind == "li":
            if current is None:
                raise BuildError(f"list item before any paragraph: {text[:80]!r}")
            parent = current
            while parent.is_item:
                assert parent.parent is not None
                parent = units[parent.parent]
            n_items = sum(1 for u in units.values() if u.parent == parent.para and u.is_item)
            item = new_unit(f"{parent.para}({_letter(n_items + 1)})", parent, True, "", notes)
            current = item
            lines.append((item.para, text))
            continue

        if region.scheme == "cover":
            m = TOP_RE.match(text)
            if m:
                start_top(m.group(1), text, notes)
            elif not tops_seen:
                start_top("1", text, notes)  # the opening paragraph of an RBI circular carries no number
            else:
                attach_plain(text, notes)
            continue

        if region.scheme == "dotted":
            m = DOTTED_RE.match(text)
            if m:
                parts = m.group(1).split(".")
                if len(parts) == 1:
                    start_top(parts[0], text, notes)
                else:
                    parent_id = ".".join(parts[:-1])
                    dotted_parent = units.get(parent_id)
                    if dotted_parent is None:
                        raise BuildError(f"paragraph {m.group(1)} has no parent {parent_id}")
                    want = sum(1 for u in units.values() if u.parent == parent_id and not u.is_item) + 1
                    if int(parts[-1]) != want:
                        raise BuildError(f"paragraph {m.group(1)} out of sequence (expected .{want})")
                    current = new_unit(m.group(1), dotted_parent, False, text, notes)
                    lines.append((current.para, text))
                continue
            if b.is_head and current is not None and not tops_seen:
                raise BuildError(f"unnumbered heading before the first paragraph: {text!r}")
            if b.is_head:
                section = text
                continue
            attach_plain(text, notes)
            continue

        # paren (NBFC CF Directions) and roman_dot (DL Directions)
        m_top = TOP_RE.match(text)
        if m_top and (region.scheme == "paren" or b.is_head):
            start_top(m_top.group(1), text, notes)
            continue
        if b.is_head:
            section = text
            continue
        sub_re = PAREN_SUB_RE if region.scheme == "paren" else ROMAN_SUB_RE
        m_sub = sub_re.match(text)
        if m_sub:
            if top is None:
                raise BuildError(f"sub-paragraph before any paragraph: {text[:80]!r}")
            token = m_sub.group(1)
            n = int(token) if region.scheme == "paren" else _roman_to_int(token)
            want = sum(1 for u in units.values() if u.parent == top.para and not u.is_item) + 1
            if n != want:
                raise BuildError(f"sub-paragraph {token} of {top.para} out of sequence (expected #{want})")
            sub = new_unit(f"{top.para}({token})", top, False, "", notes)
            current = sub
            lines.append((sub.para, text))
            continue
        if region.scheme == "paren":
            m_item = PAREN_ITEM_RE.match(text)
            if m_item:
                item_parent = sub if sub is not None else top
                if item_parent is None:
                    raise BuildError(f"clause before any paragraph: {text[:80]!r}")
                token = m_item.group(1)
                n_items = sum(1 for u in units.values() if u.parent == item_parent.para and u.is_item)
                if _roman_to_int(token) != n_items + 1:
                    raise BuildError(f"clause ({token}) of {item_parent.para} out of sequence")
                current = new_unit(f"{item_parent.para}({token})", item_parent, True, "", notes)
                lines.append((current.para, text))
                continue
        attach_plain(text, notes)

    if tuple(tops_seen) != region.expected_top:
        raise BuildError(
            f"region {region.start!r}: found paragraphs {tops_seen}, expected {region.expected_top}"
        )
    return ParsedRegion(units, lines)


# --------------------------------------------------------------------------------------------------------
# Fetching and RBI status evidence
# --------------------------------------------------------------------------------------------------------


def fetch(url: str) -> bytes:
    if not url.startswith(ALLOWED_HOSTS):
        raise BuildError(f"refusing to fetch a non-rbi.org.in URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        status = resp.status
        body: bytes = resp.read()
    if status != 200:
        raise BuildError(f"{url} returned HTTP {status}")
    print(f"fetched {url} ({len(body)} bytes)", file=sys.stderr)
    return body


def _decode(raw: bytes, url: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError(f"{url} is not UTF-8") from exc


def _visible_text(page: str) -> str:
    soup = BeautifulSoup(page, "html.parser")
    for sup in soup.find_all("sup"):
        sup.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    return normalise(soup.get_text())


def _require_text(page: str, needle: str, where: str) -> str:
    """Fail unless `needle` appears in the page's visible text (tags and footnote markers removed)."""
    if normalise(needle) not in _visible_text(page):
        raise BuildError(f"expected text not found on {where}: {needle!r}")
    return needle


@dataclass(frozen=True)
class Evidence:
    url: str
    quote: str

    def as_json(self) -> dict[str, str]:
        return {"url": self.url, "quote": self.quote}


def _withdrawn_row(soup: BeautifulSoup, circular_no: str) -> tuple[str, str, list[str]]:
    """Find the row for a circular on RBI's withdrawn-circulars list: (section heading, row number, cells)."""
    for a in soup.find_all("a"):
        if normalise(a.get_text(" ")) != circular_no:
            continue
        tr = a.find_parent("tr")
        if tr is None:
            continue
        cells = [normalise(td.get_text(" ")) for td in tr.find_all("td")]
        heading = tr.find_previous("h3")
        return (normalise(heading.get_text(" ")) if heading else "", cells[0].rstrip("."), cells)
    raise BuildError(f"{circular_no} is not on the withdrawn-circulars list")


def _dl_annex3_row(dl_page: str, spec: InstrumentSpec) -> str:
    """The row of the DL Directions' Annex III (circulars repealed) that links to this instrument's page."""
    soup = BeautifulSoup(dl_page, "html.parser")
    anchor = soup.find("a", id="AN3")
    if anchor is None:
        raise BuildError("Annex III anchor not found on the Digital Lending Directions page")
    table = anchor.find_next("table")
    if table is None:
        raise BuildError("Annex III table not found on the Digital Lending Directions page")
    page_id = spec.url.rsplit("Id=", 1)[1]
    for tr in table.find_all("tr"):
        link = tr.find("a", href=True)
        href = str(link["href"]) if link else ""
        if f"Id={page_id}&" in href or href.endswith(f"Id={page_id}"):
            row = " | ".join(normalise(td.get_text(" ")) for td in tr.find_all("td"))
            if spec.instrument_ref not in row:
                raise BuildError(
                    f"Annex III row links to {spec.url} but does not show {spec.instrument_ref}: {row}"
                )
            return row
    raise BuildError(f"{spec.instrument_ref} is not listed in Annex III of the Digital Lending Directions")


def status_evidence(
    spec: InstrumentSpec, pages: dict[str, str], withdrawn: BeautifulSoup, consolidation: str
) -> list[Evidence]:
    """Check each instrument's in-force status against RBI's own pages and return the quotes relied on."""
    out: list[Evidence] = []
    if spec.in_force != (spec.withdrawn_by is None):
        raise BuildError(
            f"{spec.instrument_ref}: an instrument in force has no withdrawn_by and a withdrawn one needs it"
        )
    if spec.in_force:
        row_links = [normalise(a.get_text(" ")) for a in withdrawn.find_all("a")]
        if spec.circular_no in row_links:
            raise BuildError(f"{spec.instrument_ref} is listed as withdrawn but configured in force")
        quote = (
            "With the issue of these Directions, the existing Directions, instructions, and guidelines "
            "relating to credit facilities for Non-Banking Financial Companies, stand repealed, "
            "as communicated vide notification dated November 28, 2025."
        )
        out.append(Evidence(spec.url, _require_text(pages[spec.key], quote, spec.url)))
        return out

    section, row_no, cells = _withdrawn_row(withdrawn, spec.circular_no)
    if "Department of Regulation" not in section:
        raise BuildError(f"{spec.circular_no} found under {section!r}, not the Department of Regulation list")
    if any("@" in c for c in cells):
        raise BuildError(f"{spec.circular_no} carries '@' (repeal deferred); status dates need review")
    out.append(Evidence(WITHDRAWN_LIST_URL, f"{section}, row {row_no}: " + " | ".join(cells[1:])))
    legend = "@-circulars shall be repealed with effect from January 01, 2026"
    if legend not in normalise(withdrawn.get_text()):
        raise BuildError(
            f"the '@' legend is missing from {WITHDRAWN_LIST_URL}; re-check how deferral is marked"
        )
    out.append(Evidence(WITHDRAWN_LIST_URL, legend + " (this row carries no '@')"))
    withdrawn_by_checked = False

    if spec.key == "dl_directions_2025":
        operative = (
            "9445 circulars listed in the Annex, which are either circulars whose instructions have been "
            "consolidated or circulars which have become obsolete / redundant, are withdrawn by the Reserve "
            "Bank with immediate effect and are hereby repealed."
        )
        out.append(Evidence(CONSOLIDATION_URL, _require_text(consolidation, operative, CONSOLIDATION_URL)))
        _require_text(consolidation, "November 28, 2025", CONSOLIDATION_URL)
        # The circular that did the withdrawing is the one on that page.
        if spec.withdrawn_by != CONSOLIDATION_REF:
            raise BuildError(
                f"{spec.instrument_ref} is withdrawn by {CONSOLIDATION_URL}, i.e. {CONSOLIDATION_REF}"
            )
        _require_text(consolidation, CONSOLIDATION_REF, CONSOLIDATION_URL)
        withdrawn_by_checked = True

    if spec.key in ("dl_guidelines_2022", "dlg_guidelines_2023"):
        banner = (
            "The Circular has been repealed. "
            "Please refer to Reserve Bank of India (Digital Lending) Directions"
        )
        out.append(Evidence(spec.url, _require_text(pages[spec.key], banner, spec.url)))
        dl_spec = next(s for s in INSTRUMENTS if s.key == "dl_directions_2025")
        annex_row = _dl_annex3_row(pages[dl_spec.key], spec)
        repeal = (
            "With the issuance of these Directions, the instructions/guidelines contained in the circulars "
            "mentioned in the Annex - III issued by RBI stand repealed."
        )
        out.append(Evidence(dl_spec.url, _require_text(pages[dl_spec.key], repeal, dl_spec.url)))
        out.append(Evidence(dl_spec.url, "Annex III row: " + annex_row))
        commencement = "These Directions shall come into force immediately"
        out.append(Evidence(dl_spec.url, _require_text(pages[dl_spec.key], commencement, dl_spec.url)))
        # Repealed by the Directions whose Annex III lists it.
        if spec.withdrawn_by != dl_spec.instrument_ref:
            raise BuildError(
                f"{spec.instrument_ref} is repealed by Annex III of {dl_spec.instrument_ref}, "
                f"not {spec.withdrawn_by}"
            )
        _require_text(pages[dl_spec.key], dl_spec.instrument_ref, dl_spec.url)
        withdrawn_by_checked = True
    if not withdrawn_by_checked:
        raise BuildError(f"{spec.instrument_ref}: no RBI page checked yet shows who withdrew it")
    return out


# --------------------------------------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------------------------------------

UPDATED_AS_ON_RE = re.compile(r"updated as on ([A-Z][a-z]+ \d{1,2}, \d{4})")


def _as_on_date(page: str, spec: InstrumentSpec) -> date | None:
    found = {m.group(1) for m in UPDATED_AS_ON_RE.finditer(page)}
    if spec.key == "nbfc_cf_2025":
        if len(found) != 1:
            raise BuildError(f"expected one 'updated as on' date on {spec.url}, found {sorted(found)}")
        return datetime.strptime(found.pop(), "%B %d, %Y").date()
    if found:
        raise BuildError(f"unexpected 'updated as on' date on {spec.url}: {sorted(found)}")
    return None


def _check_header(soup: BeautifulSoup, spec: InstrumentSpec) -> None:
    head = normalise(soup.get_text(" "))
    for needle in (spec.instrument_ref, spec.circular_no):
        if needle not in head:
            raise BuildError(f"{spec.url} does not show {needle!r}; is this the right page (or a CAPTCHA)?")


def _paragraph_records(spec: InstrumentSpec, page: str, snapshot: date) -> list[dict[str, object]]:
    soup = BeautifulSoup(page, "html.parser")
    _check_header(soup, spec)
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    cursor = 0
    for region in spec.regions:
        blocks, cursor = _region_blocks(soup, region, cursor)
        parsed = _parse_region(blocks, region)
        for para, unit in parsed.units.items():
            if para in seen:
                raise BuildError(f"{spec.key}: paragraph {para} appears in two regions")
            seen.add(para)
            subtree = _descendants(parsed.units, para)
            text = "\n".join(line for owner, line in parsed.lines if owner in subtree)
            if not normalise(text):
                raise BuildError(f"{spec.key} {para}: empty text")
            footnotes = [n for p in sorted(subtree) for n in parsed.units[p].footnotes]
            omitted = [o for p in sorted(subtree) for o in parsed.units[p].omitted]
            records.append(
                {
                    "id": f"{spec.key}:{para}",
                    "instrument": spec.instrument,
                    "instrument_ref": spec.instrument_ref,
                    "instrument_key": spec.key,
                    "para": para,
                    "parent_para": unit.parent,
                    "level": unit.level,
                    "section": unit.section,
                    "text": text,
                    "url": spec.url,
                    "snapshot_date": snapshot.isoformat(),
                    "content_hash": content_hash(text),
                    "in_force": spec.in_force,
                    "valid_from": spec.issued_on.isoformat(),
                    "valid_to": spec.valid_to.isoformat() if spec.valid_to else None,
                    "superseded_by": spec.superseded_by,
                    "footnotes": sorted(set(footnotes)),
                    "omitted": omitted,
                }
            )
    return records


def _descendants(units: dict[str, Unit], root: str) -> set[str]:
    out = {root}
    changed = True
    while changed:
        changed = False
        for para, unit in units.items():
            if unit.parent in out and para not in out:
                out.add(para)
                changed = True
    return out


def _targets() -> list[tuple[str, str]]:
    return [(s.key, s.url) for s in INSTRUMENTS] + [
        ("withdrawn_circulars", WITHDRAWN_LIST_URL),
        ("consolidation_circular", CONSOLIDATION_URL),
    ]


def _ist_date(timestamp: str) -> date:
    return datetime.fromisoformat(timestamp).astimezone(SNAPSHOT_TZ).date()


def _fetch_all(raw_dir: Path) -> date:
    """Fetch every page into raw/ and record the fetch in raw/manifest.json. Returns the snapshot date."""
    manifest_path = raw_dir / MANIFEST_NAME
    # A stale manifest must never vouch for pages from a new, possibly partial, fetch.
    manifest_path.unlink(missing_ok=True)
    pages: dict[str, dict[str, object]] = {}
    for i, (key, url) in enumerate(_targets()):
        if i:
            time.sleep(REQUEST_DELAY_S)
        fetched_at = datetime.now(UTC)
        raw = fetch(url)
        (raw_dir / f"{key}.html").write_bytes(raw)
        pages[key] = {
            "url": url,
            "fetched_at": fetched_at.isoformat(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }
    days = {_ist_date(str(p["fetched_at"])) for p in pages.values()}
    if len(days) != 1:
        raise BuildError(f"the fetch spans more than one IST date {sorted(days)}; run the fetch again")
    snapshot = days.pop()
    manifest = {"snapshot_date": snapshot.isoformat(), "timezone": str(SNAPSHOT_TZ), "pages": pages}
    _write_json(manifest_path, manifest)
    return snapshot


def read_manifest(raw_dir: Path) -> date:
    """Check raw/ against its fetch record and return the snapshot date. Fails on any mismatch."""
    manifest_path = raw_dir / MANIFEST_NAME
    if not manifest_path.exists():
        raise BuildError(
            f"{manifest_path} is missing, so the fetch date is unknown; run once without --offline"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot = date.fromisoformat(manifest["snapshot_date"])
    if manifest["timezone"] != str(SNAPSHOT_TZ):
        raise BuildError(f"{manifest_path}: timezone {manifest['timezone']!r}, expected {SNAPSHOT_TZ}")
    pages = manifest["pages"]
    targets = dict(_targets())
    if set(pages) != set(targets):
        raise BuildError(f"{manifest_path} lists {sorted(pages)}, expected {sorted(targets)}")
    for key, url in targets.items():
        record = pages[key]
        if record["url"] != url:
            raise BuildError(f"{manifest_path}: {key} was fetched from {record['url']}, expected {url}")
        fetched_on = _ist_date(record["fetched_at"])
        if fetched_on != snapshot:
            raise BuildError(f"{manifest_path}: {key} was fetched on {fetched_on}, not on {snapshot}")
        path = raw_dir / f"{key}.html"
        if not path.exists():
            raise BuildError(f"--offline but {path} is missing; run once without --offline")
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise BuildError(f"{path} changed since it was fetched on {snapshot}; run once without --offline")
    return snapshot


def _load_pages(out_dir: Path, offline: bool) -> tuple[dict[str, str], dict[str, str], str, str, date]:
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    snapshot = read_manifest(raw_dir) if offline else _fetch_all(raw_dir)
    # Re-check what was just written, so online and offline builds take the same path from here on.
    if read_manifest(raw_dir) != snapshot:
        raise BuildError("the fetch record does not match the snapshot date")
    pages: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for key, url in _targets():
        raw = (raw_dir / f"{key}.html").read_bytes()
        pages[key] = _decode(raw, url)
        hashes[key] = hashlib.sha256(raw).hexdigest()
    withdrawn = pages.pop("withdrawn_circulars")
    consolidation = pages.pop("consolidation_circular")
    return pages, hashes, withdrawn, consolidation, snapshot


def build(out_dir: Path, offline: bool) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Parse the pages. The snapshot date is the recorded fetch date, never the date of this run."""
    pages, page_hashes, withdrawn_html, consolidation, snapshot = _load_pages(out_dir, offline)
    withdrawn = BeautifulSoup(withdrawn_html, "html.parser")
    instruments: list[dict[str, object]] = []
    paragraphs: list[dict[str, object]] = []
    for spec in INSTRUMENTS:
        records = _paragraph_records(spec, pages[spec.key], snapshot)
        evidence = status_evidence(spec, pages, withdrawn, consolidation)
        as_on = _as_on_date(pages[spec.key], spec)
        instruments.append(
            {
                "key": spec.key,
                "instrument": spec.instrument,
                "instrument_ref": spec.instrument_ref,
                "circular_no": spec.circular_no,
                "issued_on": spec.issued_on.isoformat(),
                "updated_as_on": as_on.isoformat() if as_on else None,
                "url": spec.url,
                "snapshot_date": snapshot.isoformat(),
                "page_sha256": page_hashes[spec.key],
                "in_force": spec.in_force,
                "valid_from": spec.issued_on.isoformat(),
                "valid_to": spec.valid_to.isoformat() if spec.valid_to else None,
                "superseded_by": spec.superseded_by,
                "withdrawn_by": spec.withdrawn_by,
                "status_evidence": [e.as_json() for e in evidence],
                "scope": spec.scope_note,
                "paragraph_count": len(records),
            }
        )
        paragraphs.extend(records)
    return instruments, paragraphs


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def sample_citations() -> list[dict[str, str]]:
    """Every citation and replaced_citation in the sample findings (argus_api/samples/expected.json)."""
    expected = json.loads(SAMPLE_EXPECTED_PATH.read_text(encoding="utf-8"))
    out: list[dict[str, str]] = []
    for findings in expected["policies"].values():
        for finding in findings.values():
            out.append(finding["citation"])
            if finding["replaced_citation"] is not None:
                out.append(finding["replaced_citation"])
    return out


def fixture_subset(
    instruments: list[dict[str, object]], paragraphs: list[dict[str, object]]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Test fixture: paragraphs cited by the supersession map or by the sample findings (expected.json), with
    their parents and sub-paragraphs."""
    rows = json.loads(SUPERSESSION_PATH.read_text(encoding="utf-8"))["rows"]
    wanted: set[tuple[object, object]] = set()
    for row in rows:
        wanted.add((row["old_instrument_ref"], row["old_para"]))
        wanted.add((row["new_instrument_ref"], row["new_para"]))
    for citation in sample_citations():
        wanted.add((citation["instrument_ref"], citation["para"]))
    by_key = {(p["instrument_ref"], p["para"]): p for p in paragraphs}
    missing = wanted - by_key.keys()
    if missing:
        raise BuildError(
            "the supersession map or the sample findings cite paragraphs the corpus does not have: "
            f"{sorted(map(str, missing))}"
        )
    cited = set(wanted)
    for ref, para in cited:
        parent = by_key[(ref, para)]["parent_para"]
        while parent is not None:
            wanted.add((ref, parent))
            parent = by_key[(ref, parent)]["parent_para"]
    for p in paragraphs:
        parent = p["parent_para"]
        while parent is not None:
            if (p["instrument_ref"], parent) in cited:
                wanted.add((p["instrument_ref"], p["para"]))
                break
            parent = by_key[(p["instrument_ref"], parent)]["parent_para"]
    subset = [p for p in paragraphs if (p["instrument_ref"], p["para"]) in wanted]
    return instruments, subset


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--offline", action="store_true", help="parse the cached raw HTML instead of fetching")
    ap.add_argument("--fixture-out", type=Path, help="also write the test fixture subset to this directory")
    args = ap.parse_args(argv)

    instruments, paragraphs = build(args.out, args.offline)
    _write_json(args.out / "instruments.json", instruments)
    _write_json(args.out / "paragraphs.json", paragraphs)
    for inst in instruments:
        records = [p for p in paragraphs if p["instrument_ref"] == inst["instrument_ref"]]
        top = [p for p in records if p["level"] == 1]
        omitted = [(p["para"], o) for p in records if p["level"] == 1 for o in p["omitted"]]  # type: ignore[attr-defined]
        print(f"{inst['instrument_ref']:<22} {len(records):>4} records ({len(top)} top-level paragraphs)")
        for para, what in omitted:
            print(f"    not parsed in para {para}: {what}")
    if args.fixture_out:
        fx_inst, fx_paras = fixture_subset(instruments, paragraphs)
        _write_json(args.fixture_out / "instruments.json", fx_inst)
        _write_json(args.fixture_out / "paragraphs.json", fx_paras)
        print(f"fixture: {len(fx_paras)} paragraphs -> {args.fixture_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
