"""Workable poller.

API: GET https://apply.workable.com/api/v1/widget/accounts/<token>
Shape: {"name","description","jobs":[ {title, shortcode, city, state, country,
        department, employment_type, created_at, published_on, url,
        application_url, telecommuting} ]}

Returns HTTP 404 for unknown accounts, so a 200 with a "jobs" list is a reliable
signal.
"""
from __future__ import annotations

import http_client
from normalize import normalize_workable

PLATFORM = "workable"


def probe_url(slug: str) -> str:
    return f"https://apply.workable.com/api/v1/widget/accounts/{slug}"


def is_valid_payload(data) -> bool:
    return isinstance(data, dict) and isinstance(data.get("jobs"), list)


def fetch(company: str, token: str) -> list[dict]:
    url = f"https://apply.workable.com/api/v1/widget/accounts/{token}"
    data = http_client.get_json(url)
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    return [normalize_workable(company, j) for j in jobs]
