"""Thin httpx wrapper with sane timeouts, a real User-Agent, and retry/backoff
for transient 5xx / 429 responses. Shared by every poller and the resolver.
"""
from __future__ import annotations

import logging
import time

import httpx

import config

log = logging.getLogger("ai-jobs-runner")

_HEADERS = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}


def get_json(url: str, *, params: dict | None = None, timeout: float | None = None):
    """GET `url` and return parsed JSON.

    Retries on 429 and 5xx with linear backoff. Raises httpx.HTTPStatusError
    for non-retryable 4xx (e.g. 404 = no such board) and re-raises the last
    error if all attempts fail. Returns the parsed JSON body on success.
    """
    timeout = timeout if timeout is not None else config.HTTP_TIMEOUT
    last_exc: Exception | None = None

    for attempt in range(config.HTTP_RETRIES + 1):
        try:
            resp = httpx.get(url, params=params, headers=_HEADERS, timeout=timeout,
                             follow_redirects=True)
            if resp.status_code in (429,) or resp.status_code >= 500:
                last_exc = httpx.HTTPStatusError(
                    f"{resp.status_code} from {url}", request=resp.request, response=resp
                )
                wait = config.HTTP_BACKOFF * (attempt + 1)
                log.debug("retryable %s from %s; backing off %.1fs", resp.status_code, url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            wait = config.HTTP_BACKOFF * (attempt + 1)
            log.debug("transport error on %s (%s); backing off %.1fs", url, exc, wait)
            time.sleep(wait)

    assert last_exc is not None
    raise last_exc


def post_json(url: str, *, json_body: dict, timeout: float | None = None):
    """POST JSON and return parsed JSON. Same retry/backoff policy as get_json."""
    timeout = timeout if timeout is not None else config.HTTP_TIMEOUT
    headers = {**_HEADERS, "Content-Type": "application/json"}
    last_exc: Exception | None = None

    for attempt in range(config.HTTP_RETRIES + 1):
        try:
            resp = httpx.post(url, json=json_body, headers=headers, timeout=timeout,
                              follow_redirects=True)
            if resp.status_code in (429,) or resp.status_code >= 500:
                last_exc = httpx.HTTPStatusError(
                    f"{resp.status_code} from {url}", request=resp.request, response=resp
                )
                time.sleep(config.HTTP_BACKOFF * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            time.sleep(config.HTTP_BACKOFF * (attempt + 1))

    assert last_exc is not None
    raise last_exc
