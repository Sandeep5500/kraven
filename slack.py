"""Slack posting — two modes:

1. Threaded (preferred): when SLACK_BOT_TOKEN (xoxb-...) is set and
   config.SLACK_THREADED is True, roles are posted via the Web API
   (chat.postMessage) as replies under a per-company thread, with each company's
   thread living in a category channel (hybrid layout). Channel + thread ids are
   persisted in state/ so replies land correctly across runs.

2. Flat (fallback): posts batched Block Kit messages to the incoming webhook
   (SLACK_WEBHOOK_URL). Used when no bot token is configured.

In dry-run, every API/webhook call prints instead of sending.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict

import httpx

import config

log = logging.getLogger("ai-jobs-runner")


# --- token / mode helpers ----------------------------------------------------
def _bot_token() -> str | None:
    return os.environ.get("SLACK_BOT_TOKEN")


def _webhook_url() -> str | None:
    return os.environ.get("SLACK_WEBHOOK_URL")


def _home_channel() -> str:
    return os.environ.get("SLACK_HOME_CHANNEL") or config.SLACK_HOME_CHANNEL


def threaded_enabled() -> bool:
    return bool(config.SLACK_THREADED and _bot_token())


# --- shared block builders ---------------------------------------------------
def _role_blocks(record: dict) -> list[dict]:
    title = record["role_title"] or "(untitled role)"
    company = record["company"]
    bits = [b for b in (record.get("location"), record.get("category")) if b]
    meta_line = " · ".join(bits) if bits else "—"
    url = record.get("url") or ""
    apply_link = f"<{url}|Apply>" if url else "(no link)"
    text = f"*{title}* — {company}\n{meta_line}  |  {apply_link}"
    return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]


def _role_fallback(record: dict) -> str:
    return f"{record['role_title']} — {record['company']}"


# =============================================================================
# Web API (threaded mode)
# =============================================================================
class SlackError(Exception):
    pass


_API = "https://slack.com/api/"


def _api(method: str, payload: dict, *, dry_run: bool = False) -> dict:
    """Call a Slack Web API method. Handles 429 + 'ratelimited' with backoff."""
    if dry_run:
        print(f"[DRY RUN] {method} {json.dumps(payload)[:300]}")
        # Return plausible ids so downstream logic proceeds.
        return {"ok": True, "ts": "0000000000.000000",
                "channel": {"id": "C_DRYRUN"}}

    token = _bot_token()
    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json; charset=utf-8"}
    for attempt in range(config.SLACK_RETRIES + 1):
        resp = httpx.post(_API + method, headers=headers, json=payload,
                          timeout=config.HTTP_TIMEOUT)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", "2"))
            log.warning("Slack rate-limited on %s; sleeping %ds", method, wait)
            time.sleep(wait)
            continue
        data = resp.json()
        if data.get("ok"):
            return data
        err = data.get("error", "unknown")
        if err == "ratelimited":
            time.sleep(config.SLACK_BACKOFF * (attempt + 1))
            continue
        raise SlackError(f"{method}: {err}")
    raise SlackError(f"{method}: exhausted retries")


# --- channel resolution ------------------------------------------------------
def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:70] or "misc"


def _channel_name(category: str) -> str:
    slug = config.CATEGORY_SLUGS.get(category) or _slugify(category)
    return f"{config.CATEGORY_CHANNEL_PREFIX}{slug}"


def _channel_types() -> str:
    return "private_channel" if config.SLACK_PRIVATE_CHANNELS else "public_channel"


def _invite_users(channel_id: str, *, dry_run: bool = False) -> None:
    users = config.SLACK_INVITE_USERS
    if not users:
        return
    try:
        _api("conversations.invite",
             {"channel": channel_id, "users": ",".join(users)}, dry_run=dry_run)
    except SlackError as exc:
        # already_in_channel / cant_invite_self etc. are harmless.
        log.debug("conversations.invite(%s): %s", channel_id, exc)


def _find_channel_by_name(name: str, *, dry_run: bool = False) -> str | None:
    cursor = ""
    while True:
        payload = {"limit": 1000, "exclude_archived": True,
                   "types": _channel_types()}
        if cursor:
            payload["cursor"] = cursor
        data = _api("conversations.list", payload, dry_run=dry_run)
        for ch in data.get("channels", []):
            if ch.get("name") == name:
                return ch["id"]
        cursor = (data.get("response_metadata") or {}).get("next_cursor", "")
        if not cursor:
            return None


def _resolve_channel(category: str, cache: dict, *, dry_run: bool = False) -> str:
    if category in config.CATEGORY_CHANNELS:
        return config.CATEGORY_CHANNELS[category]
    if category in cache:
        return cache[category]

    name = _channel_name(category)
    cid: str | None = None
    if config.AUTO_CREATE_CHANNELS:
        try:
            data = _api("conversations.create",
                        {"name": name, "is_private": config.SLACK_PRIVATE_CHANNELS},
                        dry_run=dry_run)
            cid = data["channel"]["id"]
            log.info("created %s channel #%s (%s)",
                     "private" if config.SLACK_PRIVATE_CHANNELS else "public", name, cid)
            _invite_users(cid, dry_run=dry_run)
        except SlackError as exc:
            if "name_taken" in str(exc):
                cid = _find_channel_by_name(name, dry_run=dry_run)
            else:
                raise
    else:
        cid = _find_channel_by_name(name, dry_run=dry_run)

    if not cid:
        raise SlackError(f"could not resolve channel for category {category!r} "
                         f"(name #{name})")
    cache[category] = cid
    return cid


_joined: set[str] = set()


def _ensure_member(channel_id: str, *, dry_run: bool = False) -> None:
    """Best-effort join so the bot can post to channels it didn't create.

    Only applies to public channels — bots cannot self-join private channels
    (they must be invited, or be the creator, which they are for auto-created
    private channels).
    """
    if channel_id in _joined or dry_run or config.SLACK_PRIVATE_CHANNELS:
        return
    try:
        _api("conversations.join", {"channel": channel_id}, dry_run=dry_run)
    except SlackError as exc:
        log.debug("conversations.join(%s): %s", channel_id, exc)
    _joined.add(channel_id)


# --- thread roots ------------------------------------------------------------
def _ensure_thread(company: str, category: str, channel_id: str, threads: dict,
                   *, dry_run: bool = False) -> str:
    cur = threads.get(company)
    if cur and cur.get("channel") == channel_id and cur.get("ts"):
        return cur["ts"]
    text = (f":briefcase: *{company}* — new {category} roles appear as replies "
            f"in this thread.")
    data = _api("chat.postMessage",
                {"channel": channel_id, "text": text,
                 "blocks": [{"type": "section",
                             "text": {"type": "mrkdwn", "text": text}}]},
                dry_run=dry_run)
    ts = data["ts"]
    threads[company] = {"channel": channel_id, "ts": ts}
    return ts


def _load_state(path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(path, obj: dict) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True))


def post_new_roles_threaded(records: list[dict], company_category: dict[str, str],
                            *, dry_run: bool = False) -> int:
    """Post each company's new roles as replies under its per-company thread."""
    if not records:
        return 0
    channels = _load_state(config.CHANNELS_STORE)
    threads = _load_state(config.THREADS_STORE)

    by_company: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_company[r["company"]].append(r)

    posted = 0
    try:
        for company, recs in by_company.items():
            category = company_category.get(company) or "Uncategorized"
            try:
                channel_id = _resolve_channel(category, channels, dry_run=dry_run)
                _ensure_member(channel_id, dry_run=dry_run)
                ts = _ensure_thread(company, category, channel_id, threads,
                                    dry_run=dry_run)
                for rec in recs:
                    _api("chat.postMessage",
                         {"channel": channel_id, "thread_ts": ts,
                          "text": _role_fallback(rec), "blocks": _role_blocks(rec)},
                         dry_run=dry_run)
                    posted += 1
                    time.sleep(config.SLACK_MIN_INTERVAL)
            except SlackError as exc:
                log.warning("threaded post failed for %s: %s", company, exc)
    finally:
        if not dry_run:
            _save_state(config.CHANNELS_STORE, channels)
            _save_state(config.THREADS_STORE, threads)
    return posted


# =============================================================================
# Webhook (flat fallback mode)
# =============================================================================
def _post_webhook(payload: dict, *, dry_run: bool) -> bool:
    if dry_run:
        print(json.dumps(payload, indent=2))
        return True
    url = _webhook_url()
    if not url:
        log.error("SLACK_WEBHOOK_URL not set; cannot post to Slack")
        return False
    for attempt in range(config.SLACK_RETRIES + 1):
        try:
            resp = httpx.post(url, json=payload, timeout=config.HTTP_TIMEOUT)
            if resp.status_code == 200:
                return True
            log.warning("Slack returned %s: %s", resp.status_code, resp.text[:200])
        except httpx.HTTPError as exc:
            log.warning("Slack post error: %s", exc)
        if attempt < config.SLACK_RETRIES:
            time.sleep(config.SLACK_BACKOFF * (attempt + 1))
    log.error("Slack webhook post failed after %d attempts", config.SLACK_RETRIES + 1)
    return False


def _post_new_roles_webhook(records: list[dict], *, dry_run: bool = False) -> int:
    posted = 0
    batch_size = config.SLACK_BATCH_SIZE
    first = True
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        blocks: list[dict] = []
        if first:
            header = f"*:briefcase: {len(records)} new AI role(s)*"
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": header}})
            blocks.append({"type": "divider"})
        for rec in batch:
            blocks.extend(_role_blocks(rec))
            blocks.append({"type": "divider"})
        fallback = "\n".join(_role_fallback(r) for r in batch)
        if i > 0:
            time.sleep(config.SLACK_MIN_INTERVAL)
        if _post_webhook({"text": fallback, "blocks": blocks}, dry_run=dry_run):
            posted += len(batch)
        first = False
    return posted


# =============================================================================
# Public entry points
# =============================================================================
def post_new_roles(records: list[dict],
                   company_category: dict[str, str] | None = None,
                   *, dry_run: bool = False) -> int:
    """Dispatch to threaded or webhook mode based on configuration."""
    if not records:
        return 0
    if threaded_enabled():
        return post_new_roles_threaded(records, company_category or {}, dry_run=dry_run)
    return _post_new_roles_webhook(records, dry_run=dry_run)


def init_structure(company_category: dict[str, str], *, dry_run: bool = False) -> tuple[int, int]:
    """Pre-create every category channel and a thread root per company, so the
    full structure exists immediately (no role replies posted). Idempotent:
    reuses anything already in channels.json / threads.json. Returns
    (channels_total, threads_total)."""
    if not threaded_enabled():
        log.error("init_structure requires threaded mode (SLACK_BOT_TOKEN).")
        return (0, 0)
    channels = _load_state(config.CHANNELS_STORE)
    threads = _load_state(config.THREADS_STORE)
    try:
        # Group companies by category for stable channel creation order.
        by_cat: dict[str, list[str]] = defaultdict(list)
        for company, category in sorted(company_category.items()):
            by_cat[category or "Uncategorized"].append(company)
        for category, companies in sorted(by_cat.items()):
            try:
                channel_id = _resolve_channel(category, channels, dry_run=dry_run)
            except SlackError as exc:
                log.warning("init: channel for %s failed: %s", category, exc)
                continue
            for company in companies:
                if company in threads and threads[company].get("ts"):
                    continue  # already has a thread root
                try:
                    _ensure_thread(company, category, channel_id, threads, dry_run=dry_run)
                    time.sleep(config.SLACK_MIN_INTERVAL)
                except SlackError as exc:
                    log.warning("init: thread for %s failed: %s", company, exc)
    finally:
        if not dry_run:
            _save_state(config.CHANNELS_STORE, channels)
            _save_state(config.THREADS_STORE, threads)
    return (len(channels), len(threads))


def notify_new(*, dry_run: bool = False) -> int:
    """DB-driven digest: post not-yet-notified (high-impact, once enriched) roles
    to the home channel with links into the UI, then mark them notified.
    Returns count notified."""
    import db
    roles = db.get_unnotified(min_impact=config.SLACK_NOTIFY_MIN_IMPACT,
                              limit=config.SLACK_NOTIFY_MAX)
    if not roles:
        log.info("notify: nothing new to alert")
        return 0
    home = _home_channel()
    base = config.BASE_URL

    blocks = [{"type": "section", "text": {"type": "mrkdwn",
              "text": f":sparkles: *{len(roles)} new AI role(s)*"
                      + (f" — <{base}|browse all in Kraven>" if base else "")}},
              {"type": "divider"}]
    for r in roles:
        comp = ""
        if r.get("comp_max"):
            comp = f" · ${r['comp_max']//1000}k"
        imp = f" · impact {r['impact']}" if r.get("impact") else ""
        ov = f"\n{r['overview']}" if r.get("overview") else ""
        url = r.get("url") or base
        line = (f"*<{url}|{r['role_title']}>* — {r['company']}\n"
                f"{r.get('location','')}{comp}{imp}{ov}")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": line[:2900]}})

    if threaded_enabled():
        if not home:
            log.warning("notify: SLACK_HOME_CHANNEL not set; cannot post digest")
            return 0
        if not dry_run:
            try:
                _api("chat.postMessage", {"channel": home, "text": f"{len(roles)} new AI roles",
                                          "blocks": blocks[:50]})
            except SlackError as exc:
                log.warning("notify post failed: %s", exc)
                return 0
        else:
            print(json.dumps({"channel": home, "blocks": blocks[:50]}, indent=2)[:1500])
    else:
        _post_webhook({"text": f"{len(roles)} new AI roles", "blocks": blocks[:50]},
                      dry_run=dry_run)

    if not dry_run:
        db.mark_notified([r["key"] for r in roles])
    log.info("notify: alerted %d roles", len(roles))
    return len(roles)


def check_config() -> bool:
    """Verify Slack setup without posting. Returns True if good to go."""
    if not threaded_enabled():
        if _webhook_url():
            log.info("Slack: flat/webhook mode configured (SLACK_WEBHOOK_URL set).")
            return True
        log.error("Slack: no SLACK_BOT_TOKEN and no SLACK_WEBHOOK_URL configured.")
        return False

    token = _bot_token()
    headers = {"Authorization": f"Bearer {token}"}
    resp = httpx.post(_API + "auth.test", headers=headers, timeout=config.HTTP_TIMEOUT)
    data = resp.json()
    if not data.get("ok"):
        log.error("Slack auth.test failed: %s", data.get("error"))
        return False
    log.info("Slack: authenticated as bot '%s' in team '%s'", data.get("user"), data.get("team"))

    have = {s.strip() for s in resp.headers.get("x-oauth-scopes", "").split(",") if s.strip()}
    if config.SLACK_PRIVATE_CHANNELS:
        need = {"chat:write", "groups:write", "groups:read"}
    else:
        need = {"chat:write", "channels:manage", "channels:read", "channels:join"}
    missing = need - have
    if missing:
        log.error("Slack: missing required scopes: %s (add them and reinstall the app)",
                  sorted(missing))
        return False
    log.info("Slack: all required scopes present (%s channels).",
             "private" if config.SLACK_PRIVATE_CHANNELS else "public")

    home = _home_channel()
    if home:
        info = httpx.get(_API + "conversations.info", headers=headers,
                         params={"channel": home}, timeout=config.HTTP_TIMEOUT).json()
        if not info.get("ok"):
            log.error("Slack: cannot read home channel %s: %s", home, info.get("error"))
            return False
        ch = info.get("channel", {})
        if not ch.get("is_member"):
            log.warning("Slack: bot is NOT a member of home channel #%s — run "
                        "`/invite @%s` there, or the init message will fail.",
                        ch.get("name", home), data.get("user"))
        else:
            log.info("Slack: home channel #%s reachable and bot is a member.", ch.get("name"))
    else:
        log.info("Slack: no SLACK_HOME_CHANNEL set (init message will be skipped).")
    return True


def post_init_message(n_companies: int, m_roles: int, *, dry_run: bool = False) -> bool:
    text = (f":rocket: *AI jobs runner initialized* — tracking {n_companies} "
            f"companies, {m_roles} open matching roles. Will alert on *new* "
            f"postings from here.")
    if threaded_enabled():
        home = _home_channel()
        if not home:
            log.info("threaded mode, no SLACK_HOME_CHANNEL set; skipping init message")
            return True
        try:
            _api("chat.postMessage",
                 {"channel": home, "text": text,
                  "blocks": [{"type": "section",
                              "text": {"type": "mrkdwn", "text": text}}]},
                 dry_run=dry_run)
            return True
        except SlackError as exc:
            log.warning("init message failed: %s", exc)
            return False
    return _post_webhook(
        {"text": text, "blocks": [{"type": "section",
                                   "text": {"type": "mrkdwn", "text": text}}]},
        dry_run=dry_run)
