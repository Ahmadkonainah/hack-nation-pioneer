"""
Shared constants and small helpers used by every step of the pipeline.

Keeping them here means each fact lives in exactly one place: where the files are, which jurisdictions and
categories are in scope, and how JSON is read and written. Steps import what they need from this module
instead of reaching into each other.

Paths can be redirected with the ``NAVIGATOR_ROOT`` environment variable (the tests use that to run the
pipeline on a scratch copy of the repository without touching the real one).
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---- where things live ------------------------------------------------------------------------
ROOT = Path(os.environ.get("NAVIGATOR_ROOT") or Path(__file__).resolve().parent.parent)
CORPUS = ROOT / "corpus"        # source page text + manifest (the input of Module A)
OUT = ROOT / "outputs"          # everything the pipeline writes
CACHE_ROOT = ROOT / "cache"     # saved model and geocoder answers (the audit trail, and what makes re-runs free)
WEB = ROOT / "web"
DOCS = ROOT / "docs"

# ---- what is in scope -------------------------------------------------------------------------
DEFAULT_MODEL = "claude-sonnet-5-5"

CATEGORIES = [
    "rent_increase_limits",
    "just_cause_eviction",
    "security_deposits",
    "application_screening_fees",
    "screening_restrictions",
    "algorithmic_rent_setting",
]
STATES = ["CA", "NJ", "MA"]
CITIES = [
    "Los Angeles, CA", "San Francisco, CA", "San Diego, CA", "Berkeley, CA", "Santa Ana, CA",
    "Jersey City, NJ", "Hoboken, NJ", "Newark, NJ", "Boston, MA", "Cambridge, MA",
]
JURISDICTIONS = STATES + CITIES


def state_of(jurisdiction: str) -> str:
    """Two-letter state of a jurisdiction: ``"CA"`` -> ``"CA"``, ``"Berkeley, CA"`` -> ``"CA"``."""
    return jurisdiction.rsplit(",", 1)[-1].strip()


def is_state_level(jurisdiction: str) -> bool:
    """True for a state-wide jurisdiction (``"CA"``), False for a city (``"Berkeley, CA"``)."""
    return "," not in jurisdiction


# ---- JSON files -------------------------------------------------------------------------------
def read_json(path: Path | str) -> Any:
    """Load a UTF-8 JSON file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path | str, data: Any, *, indent: int | None = 1) -> None:
    """Write JSON as UTF-8 (non-ASCII kept readable), creating the folder if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")


# ---- small text and time helpers --------------------------------------------------------------
def collapse_ws(s: str | None) -> str:
    """Trim and turn every run of whitespace (including newlines) into one space."""
    return re.sub(r"\s+", " ", s or "").strip()


# Typographic characters that differ between a web page and what a model copies back: curly quotes,
# dashes, non-breaking spaces and soft hyphens. Folding them makes a quote comparison fair.
TYPOGRAPHY = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u00a0": " ", "\u00ad": "",
})


def fold_typography(s: str) -> str:
    """Replace curly quotes, long dashes and invisible characters by their plain ASCII twins."""
    return s.translate(TYPOGRAPHY)


def utc_now_iso() -> str:
    """Current time as an ISO-8601 UTC string without microseconds (used in logs and cache files)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def use_utf8_console() -> None:
    """Make printing never crash on Windows consoles that default to a legacy code page."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001  (a redirected or already-wrapped stream: nothing to do)
        pass


def load_env() -> None:
    """Read ``KEY=value`` lines from ``.env`` into the environment (no extra dependency).

    Variables that are already set win, so a CI job or a shell export overrides the file.
    The file is git-ignored; the key it holds is never printed or logged.
    """
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and not os.environ.get(k):
            os.environ[k] = v


# ---- the corpus manifest ----------------------------------------------------------------------
def load_manifest() -> list[dict]:
    """Manifest rows that have a text file.

    A row with no supplied text is still used when the team saved the page it read by hand as
    ``corpus/text/<doc_id>.txt``; such rows are marked ``team-added`` so the source of every
    rule stays honest. Rows whose text file is missing or empty are skipped.
    """
    rows = []
    with open(CORPUS / "corpus_manifest.csv", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            tf = (r.get("text_file") or "").strip()
            if not tf:
                guess = f"text/{r['doc_id']}.txt"
                if (CORPUS / guess).exists():
                    r = dict(r, text_file=guess, source_type="team-added (page read by hand): " + (r.get("source_type") or ""),
                             retrieved_at=r.get("retrieved_at") or "2026-10-03")
                    tf = guess
            if tf and (CORPUS / tf).exists() and (CORPUS / tf).stat().st_size > 0:
                rows.append(r)
    return rows
