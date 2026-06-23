#!/usr/bin/env python3
"""AI Jobs Discovery Runner.

Pipeline:
  read watchlist_resolved.csv -> poll each company's ATS -> normalize ->
  filter to ML/SWE titles -> de-dupe against state/seen.json -> post new roles.

First run (no seen-store): seed the store with all current matching roles, post a
single "initialized" message, and stop -- so the channel is never spammed.

Usage:
  python runner.py            # real run (posts to Slack)
  python runner.py --dry-run  # run full pipeline, print instead of posting
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import logging
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

import config
import db
import dedupe
import slack
from normalize import dedupe_key, is_stale_or_intern, is_us_location, title_matches
from pollers import (amazon, ashby, greenhouse, lever, smartrecruiters, workable,
                     workday)

POLLERS = {
    "greenhouse": greenhouse,
    "ashby": ashby,
    "lever": lever,
    "smartrecruiters": smartrecruiters,
    "workable": workable,
    "workday": workday,
    "amazon": amazon,
}

log = logging.getLogger("ai-jobs-runner")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_FILE),
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def load_watchlist() -> list[dict]:
    """Return resolved companies with a usable platform + token."""
    if not config.WATCHLIST_RESOLVED.exists():
        log.error(
            "%s not found. Run `python resolve_ats.py` first (Phase 1).",
            config.WATCHLIST_RESOLVED.name,
        )
        sys.exit(1)

    rows = []
    with config.WATCHLIST_RESOLVED.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            platform = (row.get("ATS Platform") or "").strip().lower()
            token = (row.get("ATS Token") or "").strip()
            if platform in POLLERS and token:
                rows.append({
                    "company": row["Company"].strip(),
                    "platform": platform,
                    "token": token,
                    "category": (row.get("Category") or "").strip(),
                })
    return rows


def _cap_per_company(records: list[dict]) -> list[dict]:
    """Keep at most MAX_ROLES_PER_COMPANY_PER_RUN per company (input assumed
    newest-first). 0 disables the cap."""
    cap = config.MAX_ROLES_PER_COMPANY_PER_RUN
    if not cap or cap <= 0:
        return records
    counts: dict[str, int] = {}
    out = []
    for r in records:
        c = r["company"]
        if counts.get(c, 0) < cap:
            out.append(r)
            counts[c] = counts.get(c, 0) + 1
    return out


def _keep(record: dict) -> bool:
    """A role is kept if its title matches and (when US_ONLY) it's a US location."""
    if not title_matches(record["role_title"]):
        return False
    if config.US_ONLY and not is_us_location(record.get("location", ""),
                                             record.get("country", "")):
        return False
    if is_stale_or_intern(record.get("role_title", ""), record.get("description", "")):
        return False
    return True


def poll_all(companies: list[dict]) -> tuple[list[dict], int, int]:
    """Poll every company. Returns (matching_records, n_polled, n_errors).

    One company's failure never aborts the run.
    """
    matching: list[dict] = []
    polled = 0
    errors = 0

    for c in companies:
        module = POLLERS[c["platform"]]
        try:
            records = module.fetch(c["company"], c["token"])
            polled += 1
            kept = [r for r in records if _keep(r)]
            matching.extend(kept)
            log.info(
                "%s [%s/%s]: %d roles, %d matching",
                c["company"], c["platform"], c["token"], len(records), len(kept),
            )
        except Exception as exc:  # noqa: BLE001 -- isolate per-company failures
            errors += 1
            log.warning("%s [%s/%s] FAILED: %s", c["company"], c["platform"], c["token"], exc)
        time.sleep(config.SLEEP_BETWEEN_CALLS)

    return matching, polled, errors


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Jobs Discovery Runner")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="run the full pipeline but print instead of posting to Slack",
    )
    parser.add_argument(
        "--reseed", action="store_true",
        help="re-poll and rewrite the seen-store to the current open roles WITHOUT "
             "posting anything (use after changing the watchlist or title filter)",
    )
    parser.add_argument(
        "--check-slack", action="store_true",
        help="verify Slack credentials/scopes (and home-channel access) and exit",
    )
    parser.add_argument(
        "--init-structure", action="store_true",
        help="pre-create every category channel + a thread root per company "
             "(no role replies), then exit",
    )
    parser.add_argument(
        "--notify", action="store_true",
        help="post a Slack digest of new (high-impact, enriched) roles with UI "
             "links, mark them notified, then exit (no polling)",
    )
    args = parser.parse_args()

    load_dotenv(config.ROOT / ".env")
    setup_logging()

    if args.notify:
        slack.notify_new(dry_run=args.dry_run)
        sys.exit(0)

    # Single-instance lock so a manual run and the cron (or two crons) can't race
    # on the seen-store / Slack threads. Advisory flock auto-releases on exit.
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_fh = open(config.STATE_DIR / ".runner.lock", "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.warning("another runner is already active; exiting without running.")
        return

    if args.check_slack:
        ok = slack.check_config()
        sys.exit(0 if ok else 1)

    if args.init_structure:
        companies = load_watchlist()
        company_category = {c["company"]: c["category"] for c in companies}
        nch, nth = slack.init_structure(company_category, dry_run=args.dry_run)
        log.info("init-structure: %d channels, %d company threads%s",
                 nch, nth, " (dry-run)" if args.dry_run else "")
        sys.exit(0)

    companies = load_watchlist()
    company_category = {c["company"]: c["category"] for c in companies}
    log.info("Loaded %d pollable companies from watchlist", len(companies))

    matching, polled, errors = poll_all(companies)

    # Build de-dupe keys; collapse duplicate keys within this run.
    by_key: dict[str, dict] = {}
    for rec in matching:
        by_key[dedupe_key(rec)] = rec
    current_keys = set(by_key)
    log.info("Polled %d companies (%d errors); %d matching roles (%d unique keys)",
             polled, errors, len(matching), len(current_keys))

    now = datetime.now(timezone.utc).isoformat()

    # --- Persist to the SQLite store (source of truth for the web UI). Additive;
    # does not affect the Slack/seen.json path. Skipped on dry-run.
    if not args.dry_run:
        for rec in by_key.values():
            rec["company_category"] = company_category.get(rec["company"], "")
        new_in_db = db.upsert_roles(list(by_key.values()), now=now)
        closed = db.mark_closed(current_keys, now=now)
        log.info("DB: %d roles upserted (%d new), %d marked closed",
                 len(by_key), len(new_in_db), closed)

    # --- Reseed: rebaseline the store to the current open roles, post nothing.
    if args.reseed:
        if args.dry_run:
            print(f"[DRY RUN] Would reseed store to {len(current_keys)} current roles "
                  f"(no posts).")
            return
        dedupe.save_seen(current_keys, updated_at=now)
        log.info("Reseeded store to %d current roles; posted nothing.", len(current_keys))
        return

    # --- First-run seeding: no store yet -> seed, post one init message, stop.
    if not dedupe.store_exists():
        log.info("First run: seeding seen-store with %d roles (no per-role posts)",
                 len(current_keys))
        if args.dry_run:
            print(
                f"[DRY RUN] Would initialize: tracking {len(companies)} companies, "
                f"{len(current_keys)} open matching roles. Would post 1 init message, "
                f"seed the store, and post no individual roles."
            )
            return
        dedupe.save_seen(current_keys, updated_at=now)
        slack.post_init_message(len(companies), len(current_keys), dry_run=False)
        log.info("Initialized. Seeded %d roles; posted init message.", len(current_keys))
        return

    # --- Notify mode: the DB is the browse surface + alert source. Slack alerts
    # are sent separately (run with --notify, after enrichment). Nothing to post
    # here beyond the DB upsert done above.
    if config.SLACK_NOTIFY_MODE != "threads":
        log.info("Run complete: companies=%d errors=%d roles=%d (notify mode; "
                 "run --notify to alert)%s", polled, errors, len(current_keys),
                 " (dry-run)" if args.dry_run else "")
        return

    # --- Legacy threads mode: compute and post the delta to per-company threads.
    seen = dedupe.load_seen()
    new_keys = current_keys - seen
    new_records = [by_key[k] for k in new_keys]
    new_records.sort(key=lambda r: r.get("posted_at") or "", reverse=True)
    to_post = _cap_per_company(new_records)
    log.info("%d new roles vs %d seen; posting %d after per-company cap of %d",
             len(new_records), len(seen), len(to_post),
             config.MAX_ROLES_PER_COMPANY_PER_RUN)
    posted = slack.post_new_roles(to_post, company_category, dry_run=args.dry_run)
    if not args.dry_run:
        dedupe.save_seen(seen | new_keys, updated_at=now)
    log.info(
        "Run complete: companies=%d errors=%d roles_seen=%d new=%d posted=%d%s",
        polled, errors, len(current_keys), len(new_records), posted,
        " (dry-run, store unchanged)" if args.dry_run else "",
    )


if __name__ == "__main__":
    main()
