# Scheduling

Pick one. Start with local cron; move to a VPS or Modal later if you want it
running while your laptop is closed.

## Option A — local cron (do this first)

The runner is idempotent and self-seeding, so it's safe to run on a timer.

```cron
# Edit with: crontab -e
# Poll 3x/day at 09:00, 13:00, 17:00 local time.
0 9,13,17 * * * cd /Users/skumar/repos/kraven && ./.venv/bin/python runner.py >> run.log 2>&1
```

Notes:
- Use the venv's Python (absolute path) so cron doesn't need an activated shell.
- `state/seen.json` persists between runs, so the first cron run seeds and the
  rest post only the delta.
- Logs append to `run.log` (gitignored).

## Option B — a ~$5/mo VPS

Same as cron, on a small Linux box so it runs 24/7:

```bash
git clone <your-repo> ai-jobs-runner && cd ai-jobs-runner
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
printf 'SLACK_WEBHOOK_URL=...\n' > .env       # paste your webhook
./.venv/bin/python runner.py                  # first run = seed + init message
crontab -e                                    # add the line from Option A
```

## Option C — Modal (serverless, optional)

`modal_app.py` wraps `runner.py` in a scheduled Modal function. Slack credentials
are a Modal secret (never in the repo). All state — `seen.json`, `channels.json`,
`threads.json` — lives on a Modal Volume so de-dupe **and the per-company Slack
threads** survive across invocations.

```bash
./.venv/bin/pip install modal
modal token new                                       # one-time auth

# Threaded mode (recommended):
modal secret create slack-creds \
    SLACK_BOT_TOKEN=xoxb-... SLACK_HOME_CHANNEL=C0123456789
# ...or flat/webhook mode:
modal secret create slack-creds SLACK_WEBHOOK_URL=https://hooks.slack.com/...

modal run modal_app.py::run_once                       # first run = seed (no spam)
modal deploy modal_app.py                              # deploy the scheduled fn
```

The schedule (`0 9,13,17 * * *`) is defined in `modal_app.py`. This is the
recommended "always-on" host: serverless, free tier, no server to maintain, and
the Volume keeps the thread state consistent between runs.
