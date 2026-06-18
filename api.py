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
from pydantic import BaseModel

import assist
import config
import db

load_dotenv(config.ROOT / ".env")

app = FastAPI(title="Kraven — AI Jobs")
_security = HTTPBasic(auto_error=False)


def _auth(creds: HTTPBasicCredentials | None = Depends(_security)) -> str:
    """HTTP Basic against the users table. Returns the current username."""
    if creds and db.verify_user(creds.username, creds.password):
        return creds.username
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="auth required",
                        headers={"WWW-Authenticate": "Basic"})


@app.get("/api/stats")
def api_stats(_=Depends(_auth)):
    return db.stats()


@app.get("/api/facets")
def api_facets(_=Depends(_auth)):
    return db.facets()


@app.get("/api/me")
def api_me(user: str = Depends(_auth)):
    r = db.get_resume(user)
    return {"username": user, "resume": (
        {"filename": r["filename"], "chars": len(r["resume_text"])} if r else None)}


@app.get("/api/roles")
def api_roles(
    user: str = Depends(_auth),
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
        username=user,
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
def resume_status(user: str = Depends(_auth)):
    r = db.get_resume(user)
    if not r:
        return {"uploaded": False}
    return {"uploaded": True, "filename": r["filename"],
            "updated_at": r["updated_at"], "chars": len(r["resume_text"])}


@app.post("/api/resume")
async def resume_upload(user: str = Depends(_auth), file: UploadFile = File(...)):
    raw = await file.read()
    text = _extract_text(file.filename or "resume", raw)
    if len(text) < 50:
        raise HTTPException(400, "Could not extract text from that file (try a text-based PDF or paste).")
    db.save_resume(user, text, file.filename or "resume", now=_now())
    cleared = db.mark_all_unscored(user)   # resume changed -> this user's scores are stale
    return {"ok": True, "filename": file.filename, "chars": len(text), "rescore_pending": cleared}


@app.post("/api/role/{key}/applykit")
def applykit(key: str, user: str = Depends(_auth), force: bool = False):
    if not force:
        cached = db.get_applykit(user, key)
        if cached:
            return {"cached": True, "kit": cached}
    role = db.get_role(key)
    if not role:
        raise HTTPException(404, "role not found")
    resume = db.get_resume(user)
    if not resume:
        raise HTTPException(400, "Upload your resume first (on the home page).")
    kit = assist.generate(role, resume["resume_text"])
    if not kit:
        raise HTTPException(502, "Generation failed; try again.")
    db.save_applykit(user, key, kit, now=_now())
    return {"cached": False, "kit": kit, "role": {"company": role["company"],
            "role_title": role["role_title"], "url": role["url"]}}


@app.get("/api/role/{key}")
def role_detail(key: str, _=Depends(_auth)):
    role = db.get_role(key)
    if not role:
        raise HTTPException(404, "role not found")
    return role


class SignupReq(BaseModel):
    username: str
    password: str
    code: str = ""


@app.post("/api/signup")
def signup(req: SignupReq):
    code = os.environ.get("SIGNUP_CODE")
    if not code:
        raise HTTPException(403, "Self-signup is disabled.")
    if req.code.strip() != code:
        raise HTTPException(403, "Invalid invite code.")
    u = req.username.strip().lower()
    if not u.isidentifier():
        raise HTTPException(400, "Username must be letters/numbers/underscore.")
    if len(req.password) < 4:
        raise HTTPException(400, "Password too short.")
    if db.user_exists(u):
        raise HTTPException(400, "That username is taken.")
    db.create_user(u, req.password, now=_now())
    return {"ok": True, "username": u}


@app.get("/signup")
def signup_page():
    return FileResponse(config.ROOT / "static" / "signup.html")


@app.get("/")
def index(_=Depends(_auth)):
    return FileResponse(config.ROOT / "static" / "index.html")


@app.get("/apply")
def apply_page(_=Depends(_auth)):
    return FileResponse(config.ROOT / "static" / "apply.html")
