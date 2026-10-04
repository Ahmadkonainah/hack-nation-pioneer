"""
Date logic shared by the extraction step and the lookup engine.

This is the single copy of "what does a date string mean" and "is this rule in force on that date".
It is pure (no files, no network), so both ``extract.py`` and ``engine.py`` can import it, and the
JavaScript twin in ``web/engine.js`` is checked against it by ``src/parity_test.py``.

Why dates are parsed so carefully: source pages give dates as ``2026-01-01``, ``2026-01`` or just
``2026``. A partial date is read as the first day of that period. Anything else (or an impossible date
such as ``2026-02-31``) is *unknown*, never a guess.
"""
from __future__ import annotations

import re
from datetime import date

# The default "today" of the whole project. Every answer says which date it was computed for.
QUERY_DATE = "2026-10-01"

_ISO_PARTIAL = re.compile(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")


def parse_date(s: str | None) -> date | None:
    """Read ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD`` as a date (first day of the period); None if it is not a real date."""
    m = _ISO_PARTIAL.fullmatch((s or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1))
    except ValueError:                      # e.g. month 13 or 31 February
        return None


def minus_years(d: date, n: int) -> date:
    """``d`` moved back by ``n`` years (29 February becomes 28 February), used for rolling tests such as "older than 15 years"."""
    try:
        return d.replace(year=d.year - n)
    except ValueError:                      # 29 Feb in a non-leap target year
        return d.replace(year=d.year - n, day=28)


def status_at(legal_state: str, effective_date: str | None, as_of: str) -> str:
    """Whether a rule is in force on ``as_of``. Computed by code, never written by the model.

    ``legal_state`` is what the law *is* (enacted, pending, failed). The date then decides the rest:
      * failed   -> "failed"   (struck down, defeated or expired bill: never in force)
      * pending  -> "pending"  (a bill; the effective date, if any, is only a hope)
      * enacted  -> "not_yet_effective" before its effective date, otherwise "in_force".
    A missing effective date on an enacted rule is treated as already in force; the rule record keeps the
    gap visible through its ``date_basis`` field.
    """
    if legal_state in ("failed", "pending"):
        return legal_state
    eff, asof = parse_date(effective_date), parse_date(as_of)
    if eff and asof and asof < eff:
        return "not_yet_effective"
    return "in_force"
