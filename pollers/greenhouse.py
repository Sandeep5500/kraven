"""Greenhouse poller.

API: GET https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true
Shape: {"jobs": [ {id, title, absolute_url, updated_at, location:{name}, ...} ]}
"""
from __future__ import annotations

import http_client
from normalize import normalize_greenhouse

PLATFORM = "greenhouse"


def probe_url(slug: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def is_valid_payload(data) -> bool:
    return isinstance(data, dict) and isinstance(data.get("jobs"), list)


def fetch(company: str, token: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    data = http_client.get_json(url)
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    return [normalize_greenhouse(company, j) for j in jobs]
