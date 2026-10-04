#!/usr/bin/env python3
"""
Run the whole pipeline, one step after the other, and pack the results for review.

    python src\\run_all.py                 all steps
    python src\\run_all.py --from enrich   start at a step (cached model answers are reused, so reruns are cheap)
    python src\\run_all.py --steps lookup,changes,selfcheck

Steps: extract, consolidate, enrich, translate, resolve, lookup, changes, build_web, selfcheck
(build_web also rebuilds the demo page through build_site, so build_site is not a step of its own.)
A step that crashes stops the run. Two do not: a FAIL in selfcheck is a finding to read in the report, and translate
(the optional Spanish view) is skipped with a note when it cannot run, for example when there is no key and no saved answer.
Writes run_report.txt (everything printed) and review_bundle.zip (the main output files in one archive).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# NAVIGATOR_ROOT redirects every path (the tests run the pipeline on a scratch copy); common.py reads the same variable.
ROOT = Path(os.environ.get("NAVIGATOR_ROOT") or HERE.parent)
# Order matters: each step reads the files the earlier ones wrote.
STEPS = ["extract", "consolidate", "enrich", "translate", "resolve", "lookup", "changes", "build_web", "selfcheck"]
# Files zipped for a reviewer. A file that does not exist (for example no extraction log after a replay) is skipped.
PACK = ["outputs/rules.json", "outputs/rules_enriched.json", "outputs/rules_es.json", "outputs/lookups.json", "outputs/changes.json", "outputs/changes_detailed.json",
        "outputs/resolved_addresses.json", "outputs/extraction_log.json", "outputs/consolidation_log.json", "outputs/enrichment_log.json",
        "outputs/rejected.json", "outputs/selfcheck.json", "run_report.txt"]


def main() -> int:
    """Run the chosen steps in order, log everything to run_report.txt, then write review_bundle.zip.
    Returns 1 if a step crashed, otherwise 0."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", choices=STEPS, default=None)
    ap.add_argument("--steps", default="")
    args = ap.parse_args()
    steps = [s.strip() for s in args.steps.split(",") if s.strip()] or (STEPS[STEPS.index(args.start):] if args.start else STEPS)
    bad = [s for s in steps if s not in STEPS]
    if bad:
        sys.exit(f"unknown step(s): {bad}. Choose from {STEPS}")
    # UTF-8 so a legal symbol such as the section sign cannot crash a Windows console; unbuffered so lines show up live.
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    report = open(ROOT / "run_report.txt", "w", encoding="utf-8")

    def say(s=""):
        """Print a line and append it to run_report.txt. Flushed at once, so a crash still leaves the log."""
        print(s)
        report.write(s + "\n")
        report.flush()

    t0, failed = time.time(), None
    for i, s in enumerate(steps, 1):
        say(f"\n{'=' * 70}\n[{i}/{len(steps)}] {s}\n{'=' * 70}")
        # One process per step, so each step also works on its own and a crash cannot leave state behind in this
        # runner. stderr is merged into stdout so a traceback lands in run_report.txt.
        p = subprocess.Popen([sys.executable, str(HERE / f"{s}.py")], cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
        for line in p.stdout:
            say(line.rstrip("\n"))
        rc = p.wait()
        # selfcheck exits 1 when a check FAILs. That is a result for the reader, not a crash, so it does not stop the run.
        if rc != 0 and s == "translate":
            say("\nNote: the Spanish view was skipped (see above). Everything else continues; run  python src/translate.py  later.")
        elif rc != 0 and s != "selfcheck":
            failed = (s, rc)
            say(f"\nSTOPPED: step '{s}' ended with an error (code {rc}). Fix it and run again; run_report.txt has the full log.")
            break
    say(f"\nFinished in {time.time() - t0:.0f}s" + (f" (stopped at {failed[0]})" if failed else ""))
    report.close()
    # The bundle is written even after a failure, so a reviewer still gets the partial log.
    zp = ROOT / "review_bundle.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in PACK:
            if (ROOT / f).exists():
                z.write(ROOT / f, f)
    print(f"\nReview bundle written to: {zp}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
