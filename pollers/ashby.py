"""Ashby poller.

API: GET https://api.ashbyhq.com/posting-api/job-board/<token>
Shape: {"jobs": [ {id, title, location, jobUrl, employmentType, isListed} ]}
Only keep isListed == True.
"""
from __future__ import annotations

import http_client
from normalize import normalize_ashby

PLATFORM = "ashby"


def probe_url(slug: str) -> str:
    return f"https://api.ashbyhq.com/posting-api/job-board/{slug}"


def is_valid_payload(data) -> bool:
    return isinstance(data, dict) and isinstance(data.get("jobs"), list)


def fetch(company: str, token: str) -> list[dict]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    data = http_client.get_json(url)
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    return [
        normalize_ashby(company, j)
        for j in jobs
        if j.get("isListed", True)  # keep only listed roles
    ]
