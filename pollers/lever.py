"""Lever poller.

API: GET https://api.lever.co/v0/postings/<token>?mode=json
Shape: [ {id, text, hostedUrl, applyUrl, categories:{team,location,commitment},
          createdAt(epoch ms)} ]   (top-level list)

Politeness: Lever's robots.txt asks Crawl-delay: 1 -> sleep >=1s between calls.
"""
from __future__ import annotations

import time

import config
import http_client
from normalize import normalize_lever

PLATFORM = "lever"


def probe_url(slug: str) -> str:
    return f"https://api.lever.co/v0/postings/{slug}?mode=json"


def is_valid_payload(data) -> bool:
    # A real Lever board returns a top-level list (possibly empty).
    return isinstance(data, list)


def fetch(company: str, token: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    data = http_client.get_json(url)
    time.sleep(config.LEVER_CRAWL_DELAY)  # honor Crawl-delay: 1
    jobs = data if isinstance(data, list) else []
    return [normalize_lever(company, j) for j in jobs]
