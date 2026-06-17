#!/usr/bin/env python3
"""LLM enrichment of roles via an OpenAI-compatible chat endpoint.

Reads unenriched active roles from the SQLite store, asks the model to extract a
short overview + structured fields (comp, YOE, seniority, remote, impact, skills,
tags) from the title + job description, and writes them back.

Endpoint config (env): OPENAI_BASE_URL, OPENAI_API_KEY, ENRICH_MODEL.
Works against any OpenAI-compatible /chat/completions server (e.g. a Modal-hosted
~100B model).

Usage:
  python enrich.py                 # enrich all unenriched active roles
  python enrich.py --limit 20
  python enrich.py --dry-run       # print the prompt for one role, call nothing
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx
from dotenv import load_dotenv

import config
import db

log = logging.getLogger("ai-jobs-runner")

SYSTEM_PROMPT = (
    "You extract structured data from tech job postings. "
    "Reply with ONLY a single JSON object, no prose, no markdown fences."
)

SCHEMA_HINT = """Return JSON with exactly these keys:
{
  "overview": "<=2 sentences, what the role does and why it's notable>",
  "comp_min": <int USD base salary lower bound, or null>,
  "comp_max": <int USD base salary upper bound, or null>,
  "comp_currency": "<e.g. USD, or null>",
  "comp_raw": "<verbatim comp text if present, else null>",
  "yoe_min": <minimum years of experience as int, or null>,
  "seniority": "<one of: intern, new-grad, mid, senior, staff, principal, unknown>",
  "remote": "<one of: remote, hybrid, onsite, unknown>",
  "phd_required": <true ONLY if a PhD is a HARD requirement; false if a Master's or
                   Bachelor's suffices, or if a PhD is merely "preferred" / "nice to
                   have" / "or equivalent experience">,
  "impact": <integer 1-5 = how high-impact/notable this role is for an AI/ML career>,
  "skills": ["<key required skill>", ...up to 8],
  "tags": ["<short topical tag e.g. LLM, RL, infra, vision>", ...up to 6]
}"""


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def build_messages(role: dict) -> list[dict]:
    desc = _strip_html(role.get("description") or "")[: config.ENRICH_MAX_DESC_CHARS]
    user = (
        f"{SCHEMA_HINT}\n\n"
        f"Company: {role.get('company')}\n"
        f"Title: {role.get('role_title')}\n"
        f"Location: {role.get('location')}\n"
        f"Category: {role.get('category')}\n\n"
        f"Job description:\n{desc or '(no description available; infer from title)'}"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user}]


def _parse_json(content: str) -> dict | None:
    """Extract the first JSON object from a model reply, tolerating fences/prose."""
    if not content:
        return None
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _coerce(data: dict) -> dict:
    def as_int(v):
        try:
            return int(v) if v is not None and str(v) != "" else None
        except (ValueError, TypeError):
            return None
    out = {
        "overview": (data.get("overview") or "")[:600] or None,
        "comp_min": as_int(data.get("comp_min")),
        "comp_max": as_int(data.get("comp_max")),
        "comp_currency": data.get("comp_currency") or None,
        "comp_raw": data.get("comp_raw") or None,
        "yoe_min": as_int(data.get("yoe_min")),
        "seniority": data.get("seniority") or "unknown",
        "remote": data.get("remote") or "unknown",
        "phd_required": 1 if data.get("phd_required") in (True, "true", "True", 1, "yes") else 0,
        "impact": as_int(data.get("impact")),
        "skills": data.get("skills") if isinstance(data.get("skills"), list) else [],
        "tags": data.get("tags") if isinstance(data.get("tags"), list) else [],
    }
    if out["impact"] is not None:
        out["impact"] = max(1, min(5, out["impact"]))
    return out


def call_model(messages: list[dict]) -> dict | None:
    base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    key = os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("ENRICH_MODEL", config.ENRICH_MODEL_DEFAULT)
    if not base:
        raise RuntimeError("OPENAI_BASE_URL not set (point it at your Modal endpoint)")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": config.ENRICH_MAX_TOKENS,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    resp = httpx.post(f"{base}/chat/completions", json=payload, headers=headers,
                      timeout=config.ENRICH_TIMEOUT)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return _parse_json(content)


def enrich_one(role: dict) -> bool:
    try:
        data = call_model(build_messages(role))
        if not data:
            log.warning("enrich %s: unparseable response", role["key"])
            return False
        db.save_enrichment(role["key"], _coerce(data))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("enrich %s failed: %s", role.get("key"), exc)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM-enrich roles in the store")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the prompt for one role and exit (no API call)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv(config.ROOT / ".env")

    todo = db.get_unenriched(limit=args.limit or None)
    log.info("%d roles to enrich", len(todo))
    if not todo:
        return

    if args.dry_run:
        print(json.dumps(build_messages(todo[0]), indent=2)[:2000])
        return

    done = 0
    with ThreadPoolExecutor(max_workers=config.ENRICH_CONCURRENCY) as ex:
        for ok in ex.map(enrich_one, todo):
            done += bool(ok)
    log.info("enriched %d/%d roles", done, len(todo))


if __name__ == "__main__":
    main()
