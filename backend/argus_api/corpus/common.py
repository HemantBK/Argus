"""Shared by the build script and the loader: file locations and the one text normalisation."""

import hashlib
import unicodedata
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = BACKEND_DIR / "data" / "corpus"
SUPERSESSION_PATH = Path(__file__).resolve().parent / "supersession.json"
SAMPLE_EXPECTED_PATH = Path(__file__).resolve().parents[1] / "samples" / "expected.json"


def normalise(text: str) -> str:
    """Unicode NFC, every run of whitespace (including no-break spaces) collapsed to one space, trimmed."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def content_hash(text: str) -> str:
    """sha256 of the normalised text."""
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()
