"""Seen-store: a JSON-backed set of de-dupe keys persisted across runs.

Stored as {"keys": [...], "updated_at": "..."} so the file is human-inspectable.
"""
from __future__ import annotations

import json
import logging

import config

log = logging.getLogger("ai-jobs-runner")


def store_exists() -> bool:
    return config.SEEN_STORE.exists()


def load_seen() -> set[str]:
    if not config.SEEN_STORE.exists():
        return set()
    try:
        data = json.loads(config.SEEN_STORE.read_text())
        return set(data.get("keys", []))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("could not read seen-store (%s); treating as empty", exc)
        return set()


def save_seen(keys: set[str], *, updated_at: str | None = None) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"keys": sorted(keys)}
    if updated_at:
        payload["updated_at"] = updated_at
    config.SEEN_STORE.write_text(json.dumps(payload, indent=2))
