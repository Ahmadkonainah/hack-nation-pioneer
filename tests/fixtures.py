"""Loads the real saved rules and addresses once, so the data tests do not repeat the work."""
from __future__ import annotations

import csv
import functools

from common import OUT, ROOT, read_json


@functools.lru_cache(maxsize=1)
def rules() -> list[dict]:
    """Enriched rules, exactly what the web app and the lookups use."""
    return read_json(OUT / "rules_enriched.json")["rules"]


@functools.lru_cache(maxsize=1)
def addresses() -> dict[str, dict]:
    """The 500 sample addresses by id."""
    with open(ROOT / "data" / "sample_addresses.csv", newline="", encoding="utf-8-sig") as f:
        return {r["address_id"]: r for r in csv.DictReader(f)}


@functools.lru_cache(maxsize=1)
def resolved() -> dict[str, dict]:
    """Geocoder results (legal city per address) by id."""
    return {r["address_id"]: r for r in read_json(OUT / "resolved_addresses.json")["addresses"]}


def lookup(address_id: str, as_of: str = "2026-10-01", what_if: dict | None = None) -> dict:
    """Run the real engine on one sample address."""
    import engine
    return engine.lookup_address(addresses()[address_id], resolved()[address_id], rules(), as_of, what_if)


def results_by_rule(out: dict) -> dict[str, str]:
    """{rule id: result} for a lookup answer."""
    return {e["team_rule_id"]: e["result"] for e in out["entries"]}
