# AI Jobs Discovery Runner

A small scheduled service that polls public **ATS job APIs** for a watchlist of
~154 AI companies, finds newly posted **ML/SWE** roles, de-duplicates them, and
posts each new role to a Slack channel via an incoming webhook.

Discovery only — no application tracking. ATS public APIs only (no LinkedIn /
Indeed scraping). Idempotent, resilient to a single company's API failing, and
**never spams the channel on first run**.

## How it works

```
watchlist_resolved.csv → poll each ATS → normalize → filter ML/SWE titles
   → de-dupe vs state/seen.json → post new roles to Slack
```

- **Phase 1 (one-time):** `resolve_ats.py` probes Greenhouse / Ashby / Lever to
  figure out each company's ATS platform + board token, writing
  `watchlist_resolved.csv`. Big-tech rows on Workday/custom sites resolve to
  `unresolved` by design (left for v2).
- **First run:** seeds `state/seen.json` with all currently-open matching roles,
  posts a single "initialized" message, and stops. No flood.
- **Every run after:** posts only the *new* roles (the delta) and updates the store.

## Supported ATS platforms

| Platform        | Endpoint                                                        | Notes |
|-----------------|----------------------------------------------------------------|-------|
| Greenhouse      | `boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true` | `title` |
| Ashby           | `api.ashbyhq.com/posting-api/job-board/<token>`                | `title`, keeps `isListed` |
| Lever           | `api.lever.co/v0/postings/<token>?mode=json`                   | `text`; honors Crawl-delay: 1 |
| SmartRecruiters | `api.smartrecruiters.com/v1/companies/<token>/postings`        | paginated; requires `totalFound > 0` |
| Workable        | `apply.workable.com/api/v1/widget/accounts/<token>`            | `title` |
| Workday         | `<tenant>.<dc>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs` (POST) | token = `tenant\|dc\|site`; queries role-relevant `searchText` terms and unions |
| Amazon          | `www.amazon.jobs/en/search.json`                               | one big board; unions role-relevant `base_query` terms |

All normalize into one record:
`{company, role_title, location, url, category, posted_at, source_platform, job_id}`
De-dupe key: `company:source_platform:job_id`.

### Resolving tokens

`resolve_ats.py` derives candidate slugs from each company name and probes
Greenhouse/Ashby/Lever/Workable/SmartRecruiters. Tokens that can't be derived
from the name (e.g. Glean → `gleanwork`, Sourcegraph → `sourcegraph91`) live in
`config.ATS_OVERRIDES`, which the resolver verifies live before writing.
`detect_ats.py` is a helper that finds a board token by reading a company's
careers-page HTML for ATS signatures. Both are one-time/occasional tools; the
runtime reads the resulting `watchlist_resolved.csv`.

## Setup

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Secrets live only in `.env` (gitignored). Pick one Slack mode:

**Threaded mode (recommended)** — one thread per company, grouped into category
channels; new roles post as replies. Needs a Slack **bot token**:

1. Create a Slack app → **OAuth & Permissions** → add Bot Token Scopes:
   - Private channels (default, `SLACK_PRIVATE_CHANNELS=True`): `chat:write`,
     `groups:write`, `groups:read`
   - Public channels instead: `chat:write`, `channels:manage`, `channels:read`,
     `channels:join`
2. Install (or **reinstall** after changing scopes) to the workspace, copy the
   **Bot User OAuth Token** (`xoxb-…`).
3. (Optional) Pick a "home" channel id for the one-time init message; invite the
   bot to it (`/invite @your-bot`). For private category channels, set
   `SLACK_INVITE_USERS` so the bot adds your team (private channels are invisible
   until invited).

```bash
cat > .env <<'EOF'
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_HOME_CHANNEL=C0123456789   # optional: where the init message goes
EOF
```

The bot auto-creates the category channels (`ai-jobs-frontier`, `ai-jobs-infra`,
…) on first use and remembers their ids in `state/channels.json`; per-company
thread roots are tracked in `state/threads.json`.

**Flat mode (fallback)** — batched messages to one channel via incoming webhook:

```bash
echo 'SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...' > .env
```

Mode is chosen automatically: bot token present + `SLACK_THREADED=True` → threaded;
otherwise webhook. See `config.py` (`CATEGORY_SLUGS`, `CATEGORY_CHANNELS`,
`AUTO_CREATE_CHANNELS`) to tune the layout.

## Usage

```bash
# Phase 1: resolve ATS platforms + tokens (writes watchlist_resolved.csv)
./.venv/bin/python resolve_ats.py            # add --limit N for a smoke test

# Dry run: full pipeline, prints instead of posting (safe anytime)
./.venv/bin/python runner.py --dry-run

# Real run: first run seeds + posts one init message; later runs post the delta
./.venv/bin/python runner.py

# Rebaseline the store silently after editing the watchlist or title filter
./.venv/bin/python runner.py --reseed
```

## Configuration

Everything tunable is in [config.py](config.py):

- `INCLUDE_TITLE_TERMS` / `EXCLUDE_TITLE_TERMS` — the ML/SWE title filter.
- `INCLUDE_INTERNS` — set `True` to also surface intern / new-grad / return-offer roles.
- HTTP timeouts, retries, politeness sleeps (incl. Lever's Crawl-delay).
- Slack batch size + throttle.

## Scheduling

See [SCHEDULING.md](SCHEDULING.md) — local cron (start here), a ~$5/mo VPS, or Modal.

## Files

```
config.py              # filters, paths, HTTP/Slack settings, ATS_OVERRIDES
resolve_ats.py         # Phase 1: probe ATS APIs -> watchlist_resolved.csv
detect_ats.py          # helper: detect ATS token from a careers-page HTML
http_client.py         # httpx wrapper: timeout, UA, retry/backoff (GET + POST)
pollers/               # greenhouse, ashby, lever, smartrecruiters, workable, workday
normalize.py           # raw ATS job -> common record + title filter
dedupe.py              # state/seen.json load/save
slack.py               # Block Kit posting, throttle, retry
runner.py              # main pipeline (--dry-run, --reseed)
modal_app.py           # optional Modal scheduled deployment
state/seen.json        # persisted de-dupe store (gitignored)
```

## Guardrails

- ATS public APIs only — no LinkedIn/Indeed scraping.
- The webhook is never hardcoded or committed; it lives only in `.env`.
- The seeding run never posts individual roles.
- Polite to every endpoint: timeouts, sleeps, backoff, real User-Agent, Crawl-delay.

## Coverage

129/154 companies resolve to a pollable ATS API. The remaining ~25 fall into:

- **Bot-blocked / auth-gated big-tech custom sites:** Apple, Microsoft, Google,
  Meta, Tesla, Uber, IBM (+ AI sub-labels). Their careers APIs return 403/436 to
  automated clients or sit behind rotating CSRF tokens. Polling them reliably
  would require bot-detection evasion, which this project deliberately avoids.
- **Acquired — folded into a tracked parent:** Windsurf→Cognition,
  Weights & Biases→CoreWeave, Replicate→Cloudflare. Excluded to avoid duplicate
  postings.
- **Public ATS feed disabled:** EvenUp, PlayAI, MultiOn (Ashby pages exist but
  the JSON feed 404s).
- **Other:** AI21 Labs (Comeet), Sakana AI, Predibase, Groq, Tabnine, Midjourney,
  Qualcomm (Workday tenant returns 0 via API).

## v2 / stretch (not built)

Comeet poller (AI21) · per-category Slack routing · Google Sheet sync ·
authenticated/headless handlers for the bot-protected big-tech sites.
