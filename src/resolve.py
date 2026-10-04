#!/usr/bin/env python3
"""
Step B1 - find the city, county and state each address is really in.

The postal city on an address is not always the legal city (Van Nuys is inside the City of Los Angeles,
Dorchester is inside Boston, some "Los Angeles" addresses are in unincorporated county land, and
"Berkeley" can mean Kensington). We ask the public US Census Geocoder (free, no key) for the
incorporated place and county of each address, and keep every answer in a cache so reruns are free.
The cache also lets the rest of the pipeline replay offline and gives the same answers every time.

Only an incorporated place counts as a legal city. When the Census cannot place an address, the result
carries a fallback city for information only. The lookup engine trusts it only if it came from a
city-specific dataset.

Run from the repo root with the virtual environment active:
    python src\\resolve.py                (about 500 addresses, a few minutes)
    python src\\resolve.py --limit 10     (quick test)
Output: outputs/resolved_addresses.json
Needs only the Python standard library and an internet connection.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("NAVIGATOR_ROOT") or Path(__file__).resolve().parent.parent)
DATA = ROOT / "data" / "sample_addresses.csv"
OUT = ROOT / "outputs"
CACHE = ROOT / "cache" / "geocode"

API = os.environ.get("CENSUS_API_URL") or "https://geocoding.geo.census.gov/geocoder/geographies/address"
BENCHMARK = "Public_AR_Current"
VINTAGE = "Current_Current"
SLEEP = 0.15          # seconds between requests per worker, to be polite to a free public service
TIMEOUT = 40          # seconds to wait for one answer before it counts as a failed attempt and is retried

# Legal cities in scope: (state, Census place name without the " city" ending) -> label used in rules.json
# The label must match the jurisdiction names in the rules exactly, or city rules would never be found.
IN_SCOPE = {
    ("CA", "los angeles"): "Los Angeles, CA", ("CA", "san francisco"): "San Francisco, CA",
    ("CA", "san diego"): "San Diego, CA", ("CA", "berkeley"): "Berkeley, CA", ("CA", "santa ana"): "Santa Ana, CA",
    ("NJ", "jersey city"): "Jersey City, NJ", ("NJ", "hoboken"): "Hoboken, NJ", ("NJ", "newark"): "Newark, NJ",
    ("MA", "boston"): "Boston, MA", ("MA", "cambridge"): "Cambridge, MA",
}
# City-specific datasets: every parcel in them is in that city by construction (a second, independent signal).
# Matched by prefix of the row's source_dataset. Used to retry a query with the right city name, and as a
# fallback when the geocoder finds nothing.
DATASET_CITY = {"Boston Property Assessment": "Boston, MA", "Cambridge Property Database": "Cambridge, MA",
                "DataSF": "San Francisco, CA"}
FIPS = {"06": "CA", "34": "NJ", "25": "MA"}      # state FIPS code -> postal abbreviation, for a missing STUSAB
# Census place names end in a type word ("Hoboken city"). It is stripped so the name matches IN_SCOPE.
PLACE_SUFFIX = re.compile(r"\s+(city|town|village|borough|municipality|cdp|city and county)$", re.I)


# --------------------------------------------------------------------------------------
# Query variants
# --------------------------------------------------------------------------------------

def street_variants(street: str) -> list[str]:
    """Clean forms of a street address, best first.
    Ranges ("1031-1035 CLINTON ST") use their first number; "600 JACKSON/601 HARRISON" tries both halves;
    zero-padded ordinals ("397 05TH AV") lose the zero."""
    s = re.sub(r"\s+", " ", (street or "").strip())
    # Cut unit designators and anything after them: the geocoder places a street number, not an apartment.
    s = re.sub(r"\s*(#|\bAPT\b|\bUNIT\b|\bSTE\b|\bSUITE\b|\bLOT\b)\s*\S*.*$", "", s, flags=re.I).strip()
    parts = [p.strip() for p in s.split("/") if p.strip()] or [s]
    out = []
    for part in parts:
        part = re.sub(r"\b0(\d(?:ST|ND|RD|TH))\b", r"\1", part, flags=re.I)
        m = re.match(r"^(\d+)[A-Za-z]?(?:\.\d+)?\s*-\s*\d+[A-Za-z]?(?:\.\d+)?\s+(.*)$", part)
        if m:
            out.append(f"{m.group(1)} {m.group(2)}")
        if part and part not in out:
            out.append(part)
    return [x for i, x in enumerate(out) if x and x not in out[:i]]     # drop empties and repeats, keep order


def dataset_city(source_dataset: str) -> str | None:
    """The city label implied by a city-specific source dataset name (prefix match), or None."""
    for k, v in DATASET_CITY.items():
        if (source_dataset or "").startswith(k):
            return v
    return None


def attempts_for(row: dict) -> list[dict]:
    """Ordered list of query parameter sets to try for one address (most precise first).
    Returns [] when the address has no house number, so nothing is sent for it."""
    streets = street_variants(row["street_address"])
    if not streets or not re.match(r"^\d", streets[-1]):
        return []                       # no house number: the geocoder cannot place it
    cities = [row["postal_city"].strip()]
    hint = dataset_city(row.get("source_dataset", ""))
    # Retry under the dataset's own city name: a postal name such as "Dorchester" may not match, "Boston" will.
    if hint:
        h = hint.split(",")[0]
        if h.lower() != cities[0].lower():
            cities.append(h)
    # Try the given zip first, then no zip at all: some rows carry a zip from another state, and a wrong
    # zip must not stop a match.
    zips = [row["zip"].strip(), ""] if row.get("zip", "").strip() else [""]
    out = []
    for st in streets:
        for city in cities:
            for z in zips:
                p = {"street": st, "city": city, "state": row["state"].strip()}
                if z:
                    p["zip"] = z
                out.append(p)
    return out


# --------------------------------------------------------------------------------------
# Calling the Census Geocoder
# --------------------------------------------------------------------------------------

def _key(params: dict) -> str:
    """Cache file name for a query: a hash of its parameters in a fixed key order, so the same query
    always maps to the same file. BENCHMARK and VINTAGE are added later and are not part of the key,
    so after changing them, run with --refresh."""
    return hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:20]


def fetch_json(params: dict, retries: int = 4) -> dict:
    """GET one geocoder query. Retries on timeouts, 5xx and non-JSON answers (the service is flaky).
    The wait doubles after each failure (1.5 s, 3 s, 6 s ...). Raises RuntimeError with the last reason."""
    q = dict(params, benchmark=BENCHMARK, vintage=VINTAGE, layers="all", format="json")
    url = API + "?" + urllib.parse.urlencode(q)
    last = "unknown error"
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hack-nation-navigator/1.0 (student project)"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                body = r.read().decode("utf-8", errors="replace")
            js = json.loads(body)
            if isinstance(js, dict) and "result" in js:
                return js
            last = "answer had no 'result'"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            # A 4xx means our request is bad, and repeating it cannot help. 429 (slow down) is the exception.
            if 400 <= e.code < 500 and e.code != 429:
                raise RuntimeError(last) from e          # our request is wrong: do not retry
        except Exception as e:  # noqa: BLE001 - timeouts, resets, bad JSON
            last = f"{e.__class__.__name__}: {e}"
        time.sleep(1.5 * (2 ** i))
    raise RuntimeError(last)


def cached_query(params: dict, refresh: bool = False) -> tuple[dict, bool]:
    """Return (json, from_cache). Answers are cached on disk, including "no match" answers, so a rerun
    sends nothing and the same addresses always resolve the same way, even though the Census "current"
    data can change. A failed request raises and is not cached, so it is tried again next run.
    ``refresh=True`` ignores the cache."""
    p = CACHE / f"{_key(params)}.json"
    if p.exists() and not refresh:
        return json.loads(p.read_text(encoding="utf-8")), True
    time.sleep(SLEEP)                   # only before a real request, so replaying from the cache is fast
    js = fetch_json(params)
    CACHE.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(js), encoding="utf-8")
    return js, False


# ---- reading the geocoder answer ----

def _layer(geo: dict, *names: str) -> dict:
    """First record of the geography layer called one of ``names``; {} if there is none.
    Looks for an exact layer name, then for a name that contains it, so a slightly different layer name still works."""
    for n in names:                                     # exact name first, then a loose match
        v = geo.get(n)
        if v:
            return v[0]
    for k, v in geo.items():
        if v and any(n.lower() in k.lower() for n in names):
            return v[0]
    return {}


def parse_match(js: dict) -> dict | None:
    """Pick the useful fields out of a geocoder answer, or None if it found no address match.
    Takes the first match (the best one the service returns)."""
    res = js.get("result") or {}
    ms = res.get("addressMatches") or []
    if not ms:
        return None
    m = ms[0]
    geo = m.get("geographies") or {}
    place = _layer(geo, "Incorporated Places")
    # A census-designated place is an unincorporated area (for example Kensington). It has no city
    # government, so it is kept only to explain a result, never used as the legal city.
    cdp = _layer(geo, "Census Designated Places")
    county = _layer(geo, "Counties")
    state = _layer(geo, "States")
    sub = _layer(geo, "County Subdivisions")
    xy = m.get("coordinates") or res.get("coordinates") or {}
    # Prefer the state's own abbreviation. If it is missing, map the FIPS code from any layer that has one.
    st = state.get("STUSAB") or FIPS.get(str(state.get("STATE") or county.get("STATE") or place.get("STATE") or ""))
    return {
        "matched_address": m.get("matchedAddress") or res.get("matchedAddress"),
        "lon": xy.get("x"), "lat": xy.get("y"), "state": st,
        "county": county.get("NAME"), "county_geoid": county.get("GEOID"),
        "place": place.get("NAME"), "place_geoid": place.get("GEOID"),
        "census_designated_place": cdp.get("NAME"),
        "county_subdivision": sub.get("NAME"),
    }


def legal_city(state: str | None, place: str | None) -> str | None:
    """Rules label (for example "Boston, MA") for a Census incorporated place, or None if the place
    is missing or is not one of the 10 cities in scope. State rules still apply when this is None."""
    if not state or not place:
        return None
    return IN_SCOPE.get((state, PLACE_SUFFIX.sub("", place.strip()).lower()))


# --------------------------------------------------------------------------------------
# One address
# --------------------------------------------------------------------------------------

def resolve_one(row: dict, refresh: bool = False) -> dict:
    """Resolve one sample-address row to a record with its legal city, county and how it was found.

    ``status`` is "matched", "unmatched" (the service answered but found nothing), "error" (every attempt
    failed, for example the service was down) or "no_house_number". ``postal_matches_legal`` is
    three-valued: None if the address was not placed, else True or False (False also when the Census place
    is outside the 10 cities). It tries the query variants in order and stops at the first match.
    """
    postal_city_label = IN_SCOPE.get((row["state"].strip(), row["postal_city"].strip().lower()))
    hint = dataset_city(row.get("source_dataset", ""))
    rec = {
        "address_id": row["address_id"], "street_address": row["street_address"], "postal_city": row["postal_city"],
        "state_given": row["state"], "zip_given": row["zip"], "dataset_city_hint": hint,
        "matched": False, "attempts": 0, "query_used": None, "errors": [],
    }
    tries = attempts_for(row)
    if not tries:
        rec.update(status="no_house_number", note="No house number, so the Census geocoder cannot place it.")
    else:
        for i, params in enumerate(tries, 1):
            rec["attempts"] = i
            try:
                js, _ = cached_query(params, refresh)
            except Exception as e:  # noqa: BLE001
                rec["errors"].append(f"attempt {i}: {e}")
                continue
            parsed = parse_match(js)
            if parsed:
                rec.update(parsed, matched=True, query_used=params, status="matched")
                break
        if not rec["matched"]:
            # Keep "error" and "unmatched" apart: an outage says nothing about the address, so a
            # rerun should fix it. An unmatched address will stay unmatched.
            rec["status"] = "error" if len(rec["errors"]) == len(tries) else "unmatched"
    # the legal city, as far as the Census says
    if rec["matched"]:
        rec["legal_city"] = legal_city(rec.get("state"), rec.get("place"))
        rec["jurisdiction_basis"] = "census_geocoder"
        if rec["legal_city"] is None:
            where = rec.get("place") or (f"unincorporated ({rec['census_designated_place']})" if rec.get("census_designated_place") else "unincorporated land")
            rec["note"] = f"Census places this address in: {where}, {rec.get('county')}. Not one of the 10 cities in scope."
    else:
        rec["legal_city"] = None
        rec["jurisdiction_basis"] = "unresolved"
        # fallback is information only; the lookup decides how far to trust it
        # (engine.city_for trusts a city-specific dataset; a postal-city fallback stays unverified, so a
        # city rule that would apply is answered "unknown", not "applies").
        rec["fallback_city"] = hint or postal_city_label
        rec["fallback_reason"] = ("city-specific dataset" if hint else "postal city name") if rec["fallback_city"] else None
    rec["postal_matches_legal"] = (rec["legal_city"] is not None and rec["legal_city"] == postal_city_label) if rec["matched"] else None
    return rec


# --------------------------------------------------------------------------------------
# Report + main
# --------------------------------------------------------------------------------------

def report(recs: list[dict]) -> None:
    """Print a plain-text summary of the run: counts by status, postal city versus legal city, and
    the addresses that need a look (outside the 10 cities, not resolved). Prints only; changes nothing."""
    n = len(recs)
    c = Counter(r["status"] for r in recs)
    print(f"\n{n} addresses.  matched: {c['matched']}   unmatched: {c['unmatched']}   "
          f"no house number: {c['no_house_number']}   errors: {c['error']}")
    first = sum(1 for r in recs if r["matched"] and r["attempts"] == 1)
    print(f"matched on the first try: {first}   needed a clean-up retry: {c['matched'] - first}")
    print("\nLegal city according to the Census (rows = postal city on the address):")
    table = defaultdict(Counter)
    for r in recs:
        table[r["postal_city"]][r.get("legal_city") or ("(outside the 10 cities)" if r["matched"] else "(not resolved)")] += 1
    for pc in sorted(table):
        print(f"  {pc:15s} " + ",  ".join(f"{k}: {v}" for k, v in table[pc].most_common()))
    diff = [r for r in recs if r["matched"] and r["postal_matches_legal"] is False and r["legal_city"]]
    print(f"\nAddresses whose postal city differs from the legal city (but it is one of our cities): {len(diff)}")
    for r in diff[:15]:
        print(f"  {r['address_id']}  {r['street_address']}, {r['postal_city']}  ->  {r['legal_city']}")
    out = [r for r in recs if r["matched"] and not r["legal_city"]]
    print(f"\nMatched but outside the 10 cities (only state rules apply): {len(out)}")
    for r in out[:15]:
        print(f"  {r['address_id']}  {r['street_address']}, {r['postal_city']}  ->  {r.get('note','')}")
    bad = [r for r in recs if not r["matched"]]
    print(f"\nNot resolved: {len(bad)}  (the lookup will say 'unknown' for city-level rules unless a city-specific dataset names the city)")
    for r in bad[:25]:
        print(f"  {r['address_id']}  {r['street_address']}, {r['postal_city']} {r['zip_given']}  [{r['status']}]  fallback: {r.get('fallback_city')}")
    if len(bad) > 25:
        print(f"  ... and {len(bad) - 25} more (see the file)")


def main(argv=None) -> int:
    """Command-line entry point: read the sample addresses, resolve each one (several in parallel),
    write outputs/resolved_addresses.json and print the report. Returns the process exit code."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")     # printing must not crash on legacy Windows consoles
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="Resolve each address to its legal city and county (US Census Geocoder)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N addresses (test)")
    ap.add_argument("--only", default="", help="comma-separated address ids")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--refresh", action="store_true", help="ignore the cache and ask the Census again")
    ap.add_argument("--dry-run", action="store_true", help="show what would be asked, send nothing")
    args = ap.parse_args(argv)

    rows = list(csv.DictReader(open(DATA, newline="", encoding="utf-8-sig")))     # utf-8-sig tolerates a leading BOM
    if args.only:
        want = {x.strip() for x in args.only.split(",")}
        rows = [r for r in rows if r["address_id"] in want]
    if args.limit:
        rows = rows[: args.limit]
    # Counts only each address's first query, so this is a quick progress hint and not the exact request count.
    cached = sum(1 for r in rows for p in attempts_for(r)[:1] if (CACHE / f"{_key(p)}.json").exists())
    print(f"{len(rows)} addresses to resolve ({cached} first queries already cached). Service: {API}")
    if args.dry_run:
        for r in rows[:5]:
            print(r["address_id"], attempts_for(r)[:2])
        print("Dry run only. Nothing was sent.")
        return 0

    recs, t0 = [], time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(resolve_one, r, args.refresh): r for r in rows}
        for k, f in enumerate(as_completed(futs), 1):
            recs.append(f.result())
            if k % 25 == 0 or k == len(rows):
                print(f"  {k}/{len(rows)} done ({time.time() - t0:.0f}s)")
    # Worker threads finish in any order. Sorting puts the records in the same order on every run.
    recs.sort(key=lambda r: r["address_id"])
    OUT.mkdir(exist_ok=True)
    (OUT / "resolved_addresses.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "service": "US Census Geocoder (geographies/address)", "benchmark": BENCHMARK, "vintage": VINTAGE,
        "addresses": recs}, ensure_ascii=False, indent=1), encoding="utf-8")
    report(recs)
    print("\nWrote outputs/resolved_addresses.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
