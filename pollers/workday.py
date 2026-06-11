"""Workday poller.

Workday boards live at <tenant>.<dc>.myworkdayjobs.com and expose a JSON API:
  POST https://<tenant>.<dc>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs
  body: {"limit","offset","searchText","appliedFacets":{}}
  -> {"total", "jobPostings":[{title, externalPath, locationsText, postedOn}]}

Token format in the watchlist: "<tenant>|<dc>|<site>"
  e.g. "nvidia|wd5|NVIDIAExternalCareerSite"

Because these boards are huge, we query a set of role-relevant searchText terms
(config.WORKDAY_SEARCH_TERMS) and union the results by externalPath, rather than
paging the entire board every run.
"""
from __future__ import annotations

import logging

import config
import http_client
from normalize import normalize_workday

log = logging.getLogger("ai-jobs-runner")

PLATFORM = "workday"


def parse_token(token: str) -> tuple[str, str, str] | None:
    parts = token.split("|")
    if len(parts) != 3 or not all(parts):
        return None
    return parts[0], parts[1], parts[2]


def _api_url(tenant: str, dc: str, site: str) -> str:
    return f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"


def probe_url(slug: str) -> str:  # not used for auto-resolution (needs full triple)
    return ""


def is_valid_payload(data) -> bool:
    return isinstance(data, dict) and "jobPostings" in data


def _search(url: str, term: str) -> list[dict]:
    """Page through one searchText term up to the safety cap."""
    out: list[dict] = []
    for page in range(config.WORKDAY_MAX_PAGES):
        body = {"limit": config.WORKDAY_PAGE, "offset": page * config.WORKDAY_PAGE,
                "searchText": term, "appliedFacets": {}}
        data = http_client.post_json(url, json_body=body)
        postings = data.get("jobPostings", []) if isinstance(data, dict) else []
        out.extend(postings)
        total = data.get("total", 0) if isinstance(data, dict) else 0
        if (page + 1) * config.WORKDAY_PAGE >= total or not postings:
            break
    else:
        log.info("workday: search '%s' hit page cap (%d) at %s",
                 term, config.WORKDAY_MAX_PAGES, url)
    return out


def fetch(company: str, token: str) -> list[dict]:
    parsed = parse_token(token)
    if not parsed:
        raise ValueError(f"bad workday token {token!r} (want tenant|dc|site)")
    tenant, dc, site = parsed
    url = _api_url(tenant, dc, site)

    by_path: dict[str, dict] = {}
    for term in config.WORKDAY_SEARCH_TERMS:
        for posting in _search(url, term):
            path = posting.get("externalPath")
            if path:
                by_path[path] = posting

    return [normalize_workday(company, j, tenant=tenant, dc=dc, site=site)
            for j in by_path.values()]
