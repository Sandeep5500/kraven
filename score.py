#!/usr/bin/env python3
"""Score each role's fit (0-100) against the stored resume via the LLM.

Resume-dependent, so scores are cleared when the resume changes (the API does
this on upload). Run this to (re)fill scores for unscored active roles.

Usage:
  python score.py            # score all unscored active roles
  python score.py --limit 20
  python score.py --all      # rescore everything (clear first)
"""
from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

import config
import db
import enrich

log = logging.getLogger("ai-jobs-runner")

_SYSTEM = ("You are a precise technical recruiter scoring how well a candidate's "
           "resume fits a specific role. Output ONLY a JSON object.")


def build_messages(role: dict, resume: str) -> list[dict]:
    desc = enrich._strip_html(role.get("description") or "")[:3500]
    user = (
        "Score 0-100 how well this candidate fits THIS role — weigh skills, domain, "
        "seniority/years-of-experience fit, and trajectory. Be discriminating: "
        "80-100 = strong match, 50-79 = plausible stretch, 0-49 = weak/irrelevant.\n"
        'Return JSON: {"relevance": <int 0-100>, "reason": "<<=12 words why>"}\n\n'
        f"ROLE: {role.get('company')} — {role.get('role_title')} ({role.get('location')})\n"
        f"{role.get('overview') or ''}\n{desc}\n\n"
        f"RESUME:\n{resume[:5000]}"
    )
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


def _score_one(args) -> bool:
    role, resume = args
    try:
        d = enrich.call_model(build_messages(role, resume), max_tokens=150)
        if not d or d.get("relevance") is None:
            return False
        rel = max(0, min(100, int(d["relevance"])))
        db.save_score(role["key"], rel, (d.get("reason") or "")[:140])
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("score %s failed: %s", role.get("key"), exc)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Score role fit vs resume")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--all", action="store_true", help="clear + rescore everything")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv(config.ROOT / ".env")

    resume = db.get_resume()
    if not resume or not resume.get("resume_text"):
        log.error("No resume uploaded; nothing to score against.")
        sys.exit(1)
    if args.all:
        log.info("cleared %d scores", db.mark_all_unscored())

    todo = db.get_unscored(limit=args.limit or None)
    log.info("%d roles to score", len(todo))
    if not todo:
        return
    resume_text = resume["resume_text"]
    done = 0
    with ThreadPoolExecutor(max_workers=config.ENRICH_CONCURRENCY) as ex:
        for ok in ex.map(_score_one, ((r, resume_text) for r in todo)):
            done += bool(ok)
    log.info("scored %d/%d roles", done, len(todo))


if __name__ == "__main__":
    main()
