#!/usr/bin/env python3
"""Phase 1 -- resolve each company's ATS platform + board token by probing live
public APIs, then write watchlist_resolved.csv.

For each company we generate candidate slugs from the name and probe, in order:
  Greenhouse -> Ashby -> Lever
stopping at the first slug that returns a valid jobs payload.

Most startups resolve to Greenhouse/Ashby/Lever. Big-tech rows (Google, Meta,
Amazon, NVIDIA, ...) are on Workday / custom sites and will come back
`unresolved` -- that is expected; they are left for the v2 handlers.

Usage:
  python resolve_ats.py            # probe all, write watchlist_resolved.csv
  python resolve_ats.py --limit 10 # probe only the first 10 (smoke test)
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import time
from collections import Counter

import httpx

import config
import http_client
from pollers import ashby, greenhouse, lever, smartrecruiters, workable, workday

log = logging.getLogger("ai-jobs-runner")

# Probe order. Each module exposes probe_url(slug) + is_valid_payload(data).
# SmartRecruiters is probed last (it 200s for unknown companies; validity gated
# on totalFound > 0).
PROBE_MODULES = [greenhouse, ashby, lever, workable, smartrecruiters]

# Suffix words to drop when deriving slugs.
_DROP_WORDS = {"ai", "labs", "lab", "inc", "technologies", "the", "company", "co"}


def candidate_slugs(name: str) -> list[str]:
    """Generate ordered, de-duplicated candidate slugs for a company name."""
    # Strip parentheticals, e.g. "Anysphere (Cursor)" -> "Anysphere", but also
    # remember the parenthetical itself as a candidate ("Cursor", "Devin").
    paren = re.findall(r"\(([^)]+)\)", name)
    base = re.sub(r"\([^)]*\)", " ", name)
    # Handle "X / Y" style names by considering both sides.
    parts = re.split(r"[/]", base)

    seeds: list[str] = []
    for src in parts + paren:
        seeds.append(src)

    candidates: list[str] = []

    def add(s: str) -> None:
        s = s.strip().lower()
        s = re.sub(r"[^a-z0-9\s_-]", "", s)  # drop punctuation like . ' &
        s = s.strip()
        if not s:
            return
        words = [w for w in re.split(r"[\s_-]+", s) if w]
        if not words:
            return
        # Full forms (keep all words).
        full_nohyphen = "".join(words)
        full_hyphen = "-".join(words)
        full_underscore = "_".join(words)
        for v in (full_nohyphen, full_hyphen, full_underscore):
            if v and v not in candidates:
                candidates.append(v)
        # Trimmed forms (drop trailing filler words like "ai"/"labs").
        trimmed = [w for w in words if w not in _DROP_WORDS]
        if trimmed and trimmed != words:
            t_nohyphen = "".join(trimmed)
            t_hyphen = "-".join(trimmed)
            for v in (t_nohyphen, t_hyphen):
                if v and v not in candidates:
                    candidates.append(v)
        # Single leading token as a last resort (e.g. "openai" -> already; helps
        # "Weights & Biases" -> "weights").
        if words[0] not in candidates:
            candidates.append(words[0])

    for seed in seeds:
        add(seed)

    return candidates


_MODULES_BY_PLATFORM = {m.PLATFORM: m for m in PROBE_MODULES}


def _verify_override(platform: str, token: str) -> tuple[bool, int]:
    """Live-check an override. Returns (ok, job_count)."""
    if platform == "workday":
        recs = workday.fetch("", token)  # raises on bad token
        return True, len(recs)
    if platform == "amazon":
        data = http_client.get_json(
            "https://www.amazon.jobs/en/search.json",
            params={"base_query": "engineer", "result_limit": 1})
        return (isinstance(data, dict) and isinstance(data.get("jobs"), list)), data.get("hits", 0)
    module = _MODULES_BY_PLATFORM[platform]
    data = http_client.get_json(module.probe_url(token))
    if not module.is_valid_payload(data):
        return False, 0
    if platform == "smartrecruiters":
        return True, data.get("totalFound", 0)
    return True, len(data.get("jobs", []) if isinstance(data, dict) else data)


def probe_company(name: str) -> tuple[str, str, str]:
    """Return (platform, token, note). platform == 'unresolved' if nothing hit."""
    # Manual override (verified live before trusting it).
    if name in config.ATS_OVERRIDES:
        platform, token = config.ATS_OVERRIDES[name]
        try:
            ok, n = _verify_override(platform, token)
            if ok:
                return platform, token, f"manual override ({n} jobs on probe)"
            log.warning("override for %s (%s/%s) returned invalid payload",
                        name, platform, token)
        except Exception as exc:  # noqa: BLE001
            log.warning("override for %s (%s/%s) failed live check: %s",
                        name, platform, token, exc)
        finally:
            time.sleep(config.SLEEP_BETWEEN_CALLS)

    slugs = candidate_slugs(name)
    for slug in slugs:
        for module in PROBE_MODULES:
            url = module.probe_url(slug)
            try:
                data = http_client.get_json(url)
            except httpx.HTTPStatusError:
                pass  # 404 etc. -> not this board
            except Exception as exc:  # noqa: BLE001
                log.debug("probe error %s (%s): %s", module.PLATFORM, slug, exc)
            else:
                if module.is_valid_payload(data):
                    n = len(data.get("jobs", []) if isinstance(data, dict) else data)
                    return module.PLATFORM, slug, f"auto-resolved ({n} jobs on probe)"
            finally:
                # Politeness between probes; extra for Lever.
                time.sleep(config.LEVER_CRAWL_DELAY if module is lever
                           else config.SLEEP_BETWEEN_CALLS)
    return "unresolved", "", f"no ATS match; tried slugs: {', '.join(slugs[:8])}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1 ATS resolver")
    parser.add_argument("--limit", type=int, default=0, help="probe only first N companies")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    logging.getLogger("httpx").setLevel(logging.WARNING)

    with config.WATCHLIST_RAW.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = [r for r in reader if (r.get("Company") or "").strip()]

    if args.limit:
        rows = rows[: args.limit]

    counts: Counter[str] = Counter()
    unresolved: list[str] = []

    for i, row in enumerate(rows, 1):
        name = row["Company"].strip()
        platform, token, note = probe_company(name)
        row["ATS Platform"] = platform
        row["ATS Token"] = token
        existing_note = (row.get("Notes") or "").strip()
        row["Notes"] = f"{existing_note} {note}".strip() if existing_note else note
        counts[platform] += 1
        if platform == "unresolved":
            unresolved.append(name)
        log.info("[%d/%d] %-28s -> %s%s", i, len(rows), name, platform,
                 f" ({token})" if token else "")

    with config.WATCHLIST_RESOLVED.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print("\n=== Phase 1 summary ===")
    for platform, n in counts.most_common():
        print(f"  {platform:12s} {n}")
    print(f"\nResolved {sum(n for p, n in counts.items() if p != 'unresolved')}"
          f"/{len(rows)} companies.")
    print(f"Wrote {config.WATCHLIST_RESOLVED}")
    if unresolved:
        print(f"\nUnresolved ({len(unresolved)}) -- expected for Workday/custom big-tech:")
        print("  " + ", ".join(unresolved))


if __name__ == "__main__":
    main()
