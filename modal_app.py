"""Optional Modal deployment of the runner (see SCHEDULING.md, Option C).

All state (seen.json, channels.json, threads.json) lives on a Modal Volume so
de-dupe and the per-company Slack threads persist across invocations. Slack
credentials are injected from a Modal secret, never committed.

Threaded mode (recommended):
    modal secret create slack-creds \\
        SLACK_BOT_TOKEN=xoxb-... SLACK_HOME_CHANNEL=C0123456789
Flat/webhook mode:
    modal secret create slack-creds SLACK_WEBHOOK_URL=https://hooks.slack.com/...

    modal deploy modal_app.py
    modal run modal_app.py::run_once   # manual trigger / first-run seed
"""
from __future__ import annotations

import modal

app = modal.App("ai-jobs-runner")

image = (
    modal.Image.debian_slim()
    .pip_install("httpx>=0.27", "python-dotenv>=1.0")
    # Bundle the code + the resolved watchlist into the image.
    .add_local_dir(".", remote_path="/root/app", ignore=[".venv", "state", "*.log", ".git"])
)

# Persisted de-dupe store.
volume = modal.Volume.from_name("ai-jobs-state", create_if_missing=True)


@app.function(
    image=image,
    schedule=modal.Cron("0 9,13,17 * * *"),
    secrets=[modal.Secret.from_name("slack-creds")],
    volumes={"/root/app/state": volume},
    timeout=600,
)
def scheduled_run():
    _run()


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("slack-creds")],
    volumes={"/root/app/state": volume},
    timeout=600,
)
def run_once():
    _run()


def _run():
    import os
    import subprocess

    os.chdir("/root/app")
    subprocess.run(["python", "runner.py"], check=True)
    volume.commit()  # persist the updated seen-store
