"""SmartRecruiters poller.

API: GET https://api.smartrecruiters.com/v1/companies/<token>/postings
Shape: {"offset","limit","totalFound","content":[ {id, name, location, department,
        releasedDate, company:{identifier,name}} ]}

Note: this endpoint returns HTTP 200 even for unknown companies (with
totalFound == 0), so validity requires totalFound > 0. Paginated via offset.
"""
from __future__ import annotations

import http_client
from normalize import normalize_smartrecruiters

PLATFORM = "smartrecruiters"
_PAGE = 100


def probe_url(slug: str) -> str:
    return f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1"


def is_valid_payload(data) -> bool:
    # 200 is returned for unknown companies too, so require actual postings.
    return isinstance(data, dict) and data.get("totalFound", 0) > 0


def fetch(company: str, token: str) -> list[dict]:
    base = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    out: list[dict] = []
    offset = 0
    while True:
        data = http_client.get_json(f"{base}?limit={_PAGE}&offset={offset}")
        content = data.get("content", []) if isinstance(data, dict) else []
        out.extend(normalize_smartrecruiters(company, j) for j in content)
        total = data.get("totalFound", 0) if isinstance(data, dict) else 0
        offset += _PAGE
        if offset >= total or not content:
            break
    return out
