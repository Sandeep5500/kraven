#!/usr/bin/env python3
"""FastAPI backend for the AI-jobs UI.

Serves the SQLite store as a filterable/sortable JSON API plus the static
single-page UI. Optional HTTP Basic auth when UI_PASSWORD is set.

Run:
  ./.venv/bin/uvicorn api:app --reload --port 8000
"""
from __future__ import annotations

import os
import secrets

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

import config
import db

load_dotenv(config.ROOT / ".env")

app = FastAPI(title="Kraven — AI Jobs")
_security = HTTPBasic(auto_error=False)


def _auth(creds: HTTPBasicCredentials | None = Depends(_security)):
    """Require HTTP Basic only if UI_PASSWORD is set; else open."""
    pw = os.environ.get("UI_PASSWORD")
    if not pw:
        return True
    user = os.environ.get("UI_USERNAME", "team")
    ok = creds and secrets.compare_digest(creds.username, user) and \
        secrets.compare_digest(creds.password, pw)
    if not ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="auth required",
                            headers={"WWW-Authenticate": "Basic"})
    return True


@app.get("/api/stats")
def api_stats(_=Depends(_auth)):
    return db.stats()


@app.get("/api/facets")
def api_facets(_=Depends(_auth)):
    return db.facets()


@app.get("/api/roles")
def api_roles(
    _=Depends(_auth),
    status: str = "active",
    company: str | None = None,
    category: str | None = None,
    source: str | None = None,
    seniority: str | None = None,
    min_impact: int | None = None,
    has_comp: bool = False,
    search: str | None = None,
    sort: str = "first_seen",
    order: str = "desc",
    limit: int = Query(200, le=1000),
    offset: int = 0,
):
    rows = db.query_roles(
        status=status, company=company, category=category, source=source,
        seniority=seniority, min_impact=min_impact, has_comp=has_comp,
        search=search, sort=sort, order=order, limit=limit, offset=offset,
    )
    return {"count": len(rows), "roles": rows}


@app.get("/")
def index(_=Depends(_auth)):
    return FileResponse(config.ROOT / "static" / "index.html")
