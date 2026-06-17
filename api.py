#!/usr/bin/env python3
"""FastAPI backend for the AI-jobs UI.

Serves the SQLite store as a filterable/sortable JSON API plus the static
single-page UI. Optional HTTP Basic auth when UI_PASSWORD is set.

Run:
  ./.venv/bin/uvicorn api:app --reload --port 8000
"""
from __future__ import annotations

import io
import os
import secrets
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

import assist
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
    min_relevance: int | None = None,
    max_yoe: int | None = None,
    yoe_known: bool = False,
    hide_phd: bool = False,
    has_comp: bool = False,
    search: str | None = None,
    sort: str = "first_seen",
    order: str = "desc",
    limit: int = Query(200, le=1000),
    offset: int = 0,
):
    rows = db.query_roles(
        status=status, company=company, category=category, source=source,
        seniority=seniority, min_impact=min_impact, min_relevance=min_relevance,
        max_yoe=max_yoe, yoe_known=yoe_known, hide_phd=hide_phd, has_comp=has_comp,
        search=search, sort=sort, order=order, limit=limit, offset=offset,
    )
    return {"count": len(rows), "roles": rows}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _extract_text(filename: str, raw: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(raw))
        return "\n".join((p.extract_text() or "") for p in reader.pages).strip()
    return raw.decode("utf-8", errors="ignore").strip()


@app.get("/api/resume")
def resume_status(_=Depends(_auth)):
    r = db.get_resume()
    if not r or not r.get("resume_text"):
        return {"uploaded": False}
    return {"uploaded": True, "filename": r["filename"],
            "updated_at": r["updated_at"], "chars": len(r["resume_text"])}


@app.post("/api/resume")
async def resume_upload(_=Depends(_auth), file: UploadFile = File(...)):
    raw = await file.read()
    text = _extract_text(file.filename or "resume", raw)
    if len(text) < 50:
        raise HTTPException(400, "Could not extract text from that file (try a text-based PDF or paste).")
    db.save_resume(text, file.filename or "resume", now=_now())
    cleared = db.mark_all_unscored()   # resume changed -> fit scores are stale
    return {"ok": True, "filename": file.filename, "chars": len(text), "rescore_pending": cleared}


@app.post("/api/role/{key}/applykit")
def applykit(key: str, _=Depends(_auth), force: bool = False):
    if not force:
        cached = db.get_applykit(key)
        if cached:
            return {"cached": True, "kit": cached}
    role = db.get_role(key)
    if not role:
        raise HTTPException(404, "role not found")
    resume = db.get_resume()
    if not resume or not resume.get("resume_text"):
        raise HTTPException(400, "Upload your resume first (on the home page).")
    kit = assist.generate(role, resume["resume_text"])
    if not kit:
        raise HTTPException(502, "Generation failed; try again.")
    db.save_applykit(key, kit, now=_now())
    return {"cached": False, "kit": kit, "role": {"company": role["company"],
            "role_title": role["role_title"], "url": role["url"]}}


@app.get("/api/role/{key}")
def role_detail(key: str, _=Depends(_auth)):
    role = db.get_role(key)
    if not role:
        raise HTTPException(404, "role not found")
    return role


@app.get("/")
def index(_=Depends(_auth)):
    return FileResponse(config.ROOT / "static" / "index.html")


@app.get("/apply")
def apply_page(_=Depends(_auth)):
    return FileResponse(config.ROOT / "static" / "apply.html")
