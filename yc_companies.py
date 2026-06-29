#!/usr/bin/env python3
"""Import YC startups into the watchlist as a 'Startups (YC)' category.

Source: yc-oss public companies JSON (the YC company universe, not the gated job
board). We filter to recent + hiring + AI companies, resolve each to its public
ATS (Ashby/Greenhouse/Lever/Workable) concurrently, and append the resolved ones
to watchlist_resolved.csv so the normal runner polls/enriches/scores them.

Usage:
  python yc_companies.py            # resolve + append new startups
  python yc_companies.py --dry-run  # just print what would resolve
"""
from __future__ import annotations

import argparse
import csv
import re
from concurrent.futures import ThreadPoolExecutor

import httpx

import config
from pollers import ashby, greenhouse, lever, workable
from resolve_ats import candidate_slugs

YC_URL = "https://yc-oss.github.io/api/companies/all.json"
CATEGORY = "Startups (YC)"
MIN_BATCH_YEAR = 2023
H = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}
# Probe order: the ATSes startups actually use. (company_name verified for GH.)
PROBE = [ashby, greenhouse, lever, workable]


def _batch_year(batch: str) -> int:
    m = re.search(r"20\d\d", batch or "")
    return int(m.group(0)) if m else 0


def _is_ai(c: dict) -> bool:
    blob = " ".join((c.get("industries") or []) + (c.get("tags") or [])
                    + [c.get("industry") or "", c.get("one_liner") or ""]).lower()
    return (" ai " in f" {blob} ") or "artificial intelligence" in blob \
        or "machine learning" in blob or "deep learning" in blob


def fetch_candidates() -> list[dict]:
    data = httpx.get(YC_URL, headers=H, timeout=90, follow_redirects=True).json()
    return [c for c in data
            if c.get("isHiring") and _is_ai(c)
            and _batch_year(c.get("batch")) >= MIN_BATCH_YEAR]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def resolve(company: str) -> tuple[str, str] | None:
    """Probe candidate slugs against each ATS; return (platform, token)."""
    slugs = candidate_slugs(company)[:6]
    cn = _norm(company)
    with httpx.Client(headers=H, timeout=8, follow_redirects=True) as cli:
        for slug in slugs:
            for mod in PROBE:
                try:
                    r = cli.get(mod.probe_url(slug))
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    if not mod.is_valid_payload(data):
                        continue
                    jobs = data.get("jobs") if isinstance(data, dict) else data
                    if not jobs:
                        continue   # require an active board (skips empty/placeholder)
                    # Verify the board belongs to this company where a name exists.
                    if mod.PLATFORM == "greenhouse":
                        comp = _norm(jobs[0].get("company_name"))
                        if comp and not (cn in comp or comp in cn or comp[:6] == cn[:6]):
                            continue
                    elif mod.PLATFORM == "workable":
                        nm = _norm(data.get("name"))
                        if nm and not (cn in nm or nm in cn or nm[:6] == cn[:6]):
                            continue
                    # Ashby/Lever expose no company name; the company-derived slug
                    # + an active board is the signal (role-level filters dedup noise).
                    return mod.PLATFORM, slug
                except Exception:  # noqa: BLE001
                    continue
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cands = fetch_candidates()
    if args.limit:
        cands = cands[: args.limit]
    print(f"{len(cands)} recent+hiring+AI YC companies to resolve...")

    # existing companies (skip dupes by name)
    existing = set()
    rows, fieldnames = [], None
    if config.WATCHLIST_RESOLVED.exists():
        with config.WATCHLIST_RESOLVED.open(newline="", encoding="utf-8") as f:
            rd = csv.DictReader(f); fieldnames = rd.fieldnames
            for r in rd:
                rows.append(r); existing.add(_norm(r["Company"]))
    fieldnames = fieldnames or ["Company", "Category", "What they do", "Careers / Jobs URL",
                                "ATS Platform", "ATS Token", "Relevant Team(s)", "Notes",
                                "Jobs API / Feed URL (auto)"]

    todo = [c for c in cands if _norm(c["name"]) not in existing]
    print(f"{len(todo)} not already in watchlist; resolving concurrently...")

    resolved = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for c, res in zip(todo, ex.map(lambda c: resolve(c["name"]), todo)):
            if res:
                resolved.append((c, res[0], res[1]))
    print(f"resolved {len(resolved)}/{len(todo)} to an ATS")

    for c, plat, tok in resolved[:25]:
        print(f"  {c['name']:28s} {plat}:{tok}")

    if args.dry_run:
        return

    new_rows = [{**{k: "" for k in fieldnames},
                 "Company": c["name"], "Category": CATEGORY,
                 "What they do": c.get("one_liner", ""),
                 "ATS Platform": plat, "ATS Token": tok,
                 "Notes": f"YC {c.get('batch','')}"} for c, plat, tok in resolved]
    with config.WATCHLIST_RESOLVED.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows(rows + new_rows)
    print(f"appended {len(new_rows)} startups -> {config.WATCHLIST_RESOLVED.name}")


if __name__ == "__main__":
    main()
