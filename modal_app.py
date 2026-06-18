"""Modal deployment for Kraven.

Three pieces, all sharing one Volume that holds the SQLite store + Slack state:
  - scheduled_run : cron -> poll ATS -> upsert DB -> enrich (LLM) -> Slack notify
  - web           : the FastAPI UI/API (api:app), reads the Volume
  - enrich_once / run_once : manual triggers

State lives on the "kraven-state" Volume at /data; config reads KRAVEN_STATE_DIR.

Secrets (create before deploy):
  modal secret create kraven-slack  SLACK_BOT_TOKEN=xoxb-... SLACK_HOME_CHANNEL=C...
  modal secret create kraven-app    KRAVEN_BASE_URL=https://<web-url>  UI_PASSWORD=... UI_USERNAME=team
  modal secret create kraven-model  OPENAI_BASE_URL=https://<modal-model>/v1 OPENAI_API_KEY=... ENRICH_MODEL=...
  (kraven-model can hold placeholders until the model endpoint is live; enrich
   no-ops safely until it returns valid responses.)

Deploy:
  modal deploy modal_app.py
  modal run modal_app.py::run_once          # one full cycle now
See DEPLOY.md for the full ordered steps (incl. uploading existing state).
"""
from __future__ import annotations

import os
import subprocess

import modal

app = modal.App("kraven")

image = (
    modal.Image.debian_slim()
    .pip_install("httpx>=0.27", "python-dotenv>=1.0", "fastapi>=0.110",
                 "uvicorn[standard]>=0.29", "pypdf>=4.0", "python-multipart>=0.0.9")
    .add_local_dir(".", remote_path="/root/app",
                   ignore=[".venv", "state", "*.log", ".git", "__pycache__"])
)

volume = modal.Volume.from_name("kraven-state", create_if_missing=True)
STATE = "/data"

slack_secret = modal.Secret.from_name("kraven-slack")
app_secret = modal.Secret.from_name("kraven-app")      # KRAVEN_BASE_URL, UI_PASSWORD
model_secret = modal.Secret.from_name("kraven-model")  # OPENAI_BASE_URL, ENRICH_MODEL

_ENV = {"KRAVEN_STATE_DIR": STATE}


def _cycle(*, notify: bool = True) -> None:
    """Run one poll -> enrich -> notify cycle inside the container."""
    os.chdir("/root/app")
    os.environ.update(_ENV)
    subprocess.run(["python", "runner.py"], check=True)            # poll + upsert DB
    try:
        subprocess.run(["python", "enrich.py"], check=True)        # LLM enrich
    except subprocess.CalledProcessError as exc:
        print(f"enrich step skipped/failed (endpoint not ready?): {exc}")
    subprocess.run(["python", "score.py"], check=False)            # fit vs resume
    if notify:
        subprocess.run(["python", "runner.py", "--notify"], check=False)
    volume.commit()


@app.function(image=image, schedule=modal.Cron("0 13,17,21 * * *"),
              volumes={STATE: volume},
              secrets=[slack_secret, app_secret, model_secret], timeout=1800)
def scheduled_run():
    _cycle()


@app.function(image=image, volumes={STATE: volume},
              secrets=[slack_secret, app_secret, model_secret], timeout=1800)
def run_once():
    _cycle()


@app.function(image=image, volumes={STATE: volume},
              secrets=[slack_secret, app_secret, model_secret], timeout=1800)
def enrich_once():
    os.chdir("/root/app")
    os.environ.update(_ENV)
    subprocess.run(["python", "enrich.py"], check=False)
    volume.commit()


def _cli(*args):
    import os as _os
    import subprocess as _sp
    import sys as _sys
    _os.chdir("/root/app"); _sys.path.insert(0, "/root/app")
    _os.environ.update(_ENV)
    _sp.run(["python", *args], check=False)
    volume.commit()


@app.function(image=image, volumes={STATE: volume})
def add_user(username: str, password: str):
    """modal run modal_app.py::add_user --username x --password y"""
    _cli("users.py", "add", username, password)


@app.function(image=image, volumes={STATE: volume})
def list_users():
    _cli("users.py", "list")


@app.function(image=image, volumes={STATE: volume})
def migrate_user(username: str):
    """Move the legacy singleton resume + scores into this profile (one-time)."""
    _cli("users.py", "migrate", username)


@app.function(image=image, volumes={STATE: volume},
              # app_secret = UI/base-url; model_secret = OPENAI_* for live apply-kit
              # generation (the /applykit endpoint calls the LLM synchronously).
              secrets=[app_secret, model_secret],
              # scale-to-zero: only billed when someone opens the UI (a few-second
              # cold start on the first hit after idle). Set min_containers=1 to
              # keep it always-warm if the cold start annoys the team.
              min_containers=0, scaledown_window=300)
@modal.asgi_app()
def web():
    import sys
    sys.path.insert(0, "/root/app")   # repo code lives here (add_local_dir)
    os.environ.update(_ENV)           # set KRAVEN_STATE_DIR before importing config/api
    from api import app as fastapi_app

    @fastapi_app.middleware("http")
    async def _reload_volume(request, call_next):
        # See the runner's latest commits to the shared Volume.
        try:
            volume.reload()
        except Exception:  # noqa: BLE001
            pass
        return await call_next(request)

    return fastapi_app
