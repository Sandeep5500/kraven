# Deploy (Modal)

Deploys three things sharing one Volume (`kraven-state`, holds the SQLite DB +
Slack state):
- **web** — the FastAPI UI/API (always-on)
- **scheduled_run** — cron: poll → upsert DB → enrich → Slack notify
- manual triggers: `run_once`, `enrich_once`

Everything is built; the steps below need your Modal auth (browser) and the
model endpoint (step 7, tomorrow).

## 1. Auth (you — opens a browser)
```bash
./.venv/bin/pip install -r requirements.txt
./.venv/bin/modal token new
```

## 2. Secrets
```bash
# Slack (your new-workspace bot)
./.venv/bin/modal secret create kraven-slack \
    SLACK_BOT_TOKEN=xoxb-...  SLACK_HOME_CHANNEL=C0B9U83GK19

# App: UI auth + base URL (placeholder for now; fixed in step 5)
./.venv/bin/modal secret create kraven-app \
    KRAVEN_BASE_URL=https://placeholder  UI_USERNAME=team  UI_PASSWORD=<pick-one>

# Model: placeholders today; real values tomorrow (step 7). enrich no-ops until then.
./.venv/bin/modal secret create kraven-model \
    OPENAI_BASE_URL=https://placeholder/v1  OPENAI_API_KEY=x  ENRICH_MODEL=default
```

## 3. Upload existing state to the Volume (so it continues seamlessly)
```bash
./.venv/bin/modal volume create kraven-state   # ok if it says already exists
./.venv/bin/modal volume put kraven-state state/roles.db     /roles.db
./.venv/bin/modal volume put kraven-state state/seen.json    /seen.json
./.venv/bin/modal volume put kraven-state state/channels.json /channels.json
./.venv/bin/modal volume put kraven-state state/threads.json /threads.json
```

## 4. Deploy
```bash
./.venv/bin/modal deploy modal_app.py
```
Note the printed **web URL** (e.g. `https://<you>--kraven-web.modal.run`).

## 5. Point notification links at the real URL
```bash
./.venv/bin/modal secret delete kraven-app
./.venv/bin/modal secret create kraven-app \
    KRAVEN_BASE_URL=https://<you>--kraven-web.modal.run  UI_USERNAME=team  UI_PASSWORD=<same>
./.venv/bin/modal deploy modal_app.py        # redeploy to pick it up
```

## 6. Smoke test
```bash
./.venv/bin/modal run modal_app.py::run_once   # one full cycle now
# open the web URL (login: team / your UI_PASSWORD)
```

## 7. Tomorrow — turn on enrichment
```bash
./.venv/bin/modal secret delete kraven-model
./.venv/bin/modal secret create kraven-model \
    OPENAI_BASE_URL=https://<your-modal-model>/v1  OPENAI_API_KEY=<token>  ENRICH_MODEL=<name>
./.venv/bin/modal deploy modal_app.py
./.venv/bin/modal run modal_app.py::enrich_once   # backfill overviews/comp/impact
```

## 8. Retire the local cron (Modal now owns scheduling)
```bash
crontab -r
```

## Notes
- Schedule is `0 13,17,21 * * *` **UTC** (≈ 9/13/17 US-Eastern) — edit the
  `modal.Cron(...)` in `modal_app.py` to taste.
- `SLACK_NOTIFY_MODE="notify"` (config.py): Slack gets a digest of new
  impact≥`SLACK_NOTIFY_MIN_IMPACT` roles with links to the UI. Set the threshold
  to 0 to alert on everything regardless of enrichment.
- SQLite on a Modal Volume: the scheduled job is the only writer; the web app
  calls `volume.reload()` per request to see fresh data. Fine at this scale.
