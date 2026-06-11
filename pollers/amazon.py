"""Amazon (amazon.jobs) poller.

Public JSON API: GET https://www.amazon.jobs/en/search.json
  params: base_query, result_limit, offset, sort=recent
  -> {"hits": <total>, "jobs": [ {title, job_path, normalized_location,
       posted_date, id_icims, team, is_intern, is_manager} ]}

amazon.jobs is one big board, so we query role-relevant search terms and union
the results by id_icims rather than paging the whole board.

Token is ignored (single board); kept for interface symmetry.
"""
from __future__ import annotations

import config
import http_client
from normalize import normalize_amazon

PLATFORM = "amazon"
_BASE = "https://www.amazon.jobs/en/search.json"


def probe_url(slug: str) -> str:  # not auto-resolved
    return ""


def is_valid_payload(data) -> bool:
    return isinstance(data, dict) and isinstance(data.get("jobs"), list)


def _search(term: str) -> list[dict]:
    out: list[dict] = []
    for page in range(config.BIGTECH_MAX_PAGES):
        params = {"base_query": term, "result_limit": config.BIGTECH_PAGE,
                  "offset": page * config.BIGTECH_PAGE, "sort": "recent"}
        data = http_client.get_json(_BASE, params=params)
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        out.extend(jobs)
        total = data.get("hits", 0) if isinstance(data, dict) else 0
        if (page + 1) * config.BIGTECH_PAGE >= total or not jobs:
            break
    return out


def fetch(company: str, token: str = "") -> list[dict]:
    by_id: dict[str, dict] = {}
    for term in config.ROLE_SEARCH_TERMS:
        for j in _search(term):
            jid = str(j.get("id_icims") or j.get("id"))
            by_id[jid] = j
    return [normalize_amazon(company, j) for j in by_id.values()]
