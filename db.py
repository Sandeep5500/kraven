"""SQLite store for roles + LLM enrichment.

One row per de-dupe key (company:source_platform:job_id). The runner upserts the
current matched roles each run; roles no longer seen are marked closed (so the UI
can archive them). Enrichment fields are filled in later by enrich.py.

This is the source of truth for the web UI; it does not replace seen.json/Slack
yet (kept additive during the migration).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

import config

# Columns that come straight from a normalized record. `category` is the ATS
# department/team (per role); `company_category` is the watchlist bucket
# (Frontier, Infra, ...) used for the UI's category filter.
_RECORD_COLS = ["company", "company_category", "role_title", "location", "url",
                "category", "posted_at", "source_platform", "job_id", "country",
                "description"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    key             TEXT PRIMARY KEY,
    company         TEXT,
    company_category TEXT,
    role_title      TEXT,
    location        TEXT,
    url             TEXT,
    category        TEXT,
    posted_at       TEXT,
    source_platform TEXT,
    job_id          TEXT,
    country         TEXT,
    description     TEXT,
    -- lifecycle
    first_seen      TEXT,
    last_seen       TEXT,
    status          TEXT DEFAULT 'active',   -- active | closed
    notified        INTEGER DEFAULT 0,       -- posted to Slack yet
    -- enrichment (filled by enrich.py)
    enriched        INTEGER DEFAULT 0,
    overview        TEXT,
    comp_min        INTEGER,
    comp_max        INTEGER,
    comp_currency   TEXT,
    comp_raw        TEXT,
    yoe_min         INTEGER,
    seniority       TEXT,
    remote          TEXT,
    phd_required    INTEGER,                 -- 1 if a PhD is a hard requirement
    impact          INTEGER,                 -- 1-5 notability score
    relevance       INTEGER,                 -- 0-100 fit vs the user's resume
    relevance_reason TEXT,
    skills          TEXT,                    -- JSON array
    tags            TEXT                     -- JSON array
);
CREATE INDEX IF NOT EXISTS idx_roles_status   ON roles(status);
CREATE INDEX IF NOT EXISTS idx_roles_company  ON roles(company);
CREATE INDEX IF NOT EXISTS idx_roles_category ON roles(category);
CREATE INDEX IF NOT EXISTS idx_roles_enriched ON roles(enriched);

CREATE TABLE IF NOT EXISTS profile (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    resume_text TEXT,
    filename    TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS applykit (
    key        TEXT PRIMARY KEY,   -- role key
    data       TEXT,               -- JSON {linkedin, email, referral, answers}
    created_at TEXT
);
"""


@contextmanager
def connect():
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Columns added after the initial schema, for self-migration of existing DBs.
_MIGRATIONS = {"company_category": "TEXT", "phd_required": "INTEGER",
               "relevance": "INTEGER", "relevance_reason": "TEXT"}


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)
        existing = {r[1] for r in conn.execute("PRAGMA table_info(roles)").fetchall()}
        for col, typ in _MIGRATIONS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE roles ADD COLUMN {col} {typ}")


def upsert_roles(records: list[dict], *, now: str) -> list[str]:
    """Insert/refresh the given roles. Returns the keys that are NEW (first seen
    this run). Existing rows have last_seen + status='active' refreshed and any
    changed core fields updated; enrichment is preserved.
    """
    from normalize import dedupe_key

    init_db()
    new_keys: list[str] = []
    with connect() as conn:
        existing = {r[0] for r in conn.execute("SELECT key FROM roles").fetchall()}
        for rec in records:
            key = dedupe_key(rec)
            vals = {c: rec.get(c) for c in _RECORD_COLS}
            if key in existing:
                conn.execute(
                    f"""UPDATE roles SET {', '.join(f'{c}=:{c}' for c in _RECORD_COLS)},
                        last_seen=:now, status='active' WHERE key=:key""",
                    {**vals, "now": now, "key": key},
                )
            else:
                new_keys.append(key)
                conn.execute(
                    f"""INSERT INTO roles (key, {', '.join(_RECORD_COLS)},
                                           first_seen, last_seen, status)
                        VALUES (:key, {', '.join(f':{c}' for c in _RECORD_COLS)},
                                :now, :now, 'active')""",
                    {**vals, "key": key, "now": now},
                )
    return new_keys


def mark_closed(active_keys: set[str], *, now: str) -> int:
    """Mark any currently-active role not in active_keys as closed. Returns count."""
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT key FROM roles WHERE status='active'").fetchall()
        to_close = [(r[0],) for r in rows if r[0] not in active_keys]
        conn.executemany("UPDATE roles SET status='closed', last_seen=? WHERE key=?",
                         [(now, k[0]) for k in to_close])
    return len(to_close)


# --- enrichment helpers ------------------------------------------------------
def get_unenriched(limit: int | None = None) -> list[dict]:
    init_db()
    q = "SELECT * FROM roles WHERE enriched=0 AND status='active' ORDER BY first_seen DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    with connect() as conn:
        return [dict(r) for r in conn.execute(q).fetchall()]


def save_enrichment(key: str, data: dict) -> None:
    fields = {
        "overview": data.get("overview"),
        "comp_min": data.get("comp_min"),
        "comp_max": data.get("comp_max"),
        "comp_currency": data.get("comp_currency"),
        "comp_raw": data.get("comp_raw"),
        "yoe_min": data.get("yoe_min"),
        "seniority": data.get("seniority"),
        "remote": data.get("remote"),
        "phd_required": data.get("phd_required"),
        "impact": data.get("impact"),
        "skills": json.dumps(data.get("skills") or []),
        "tags": json.dumps(data.get("tags") or []),
    }
    with connect() as conn:
        conn.execute(
            f"UPDATE roles SET {', '.join(f'{k}=:{k}' for k in fields)}, enriched=1 "
            f"WHERE key=:key",
            {**fields, "key": key},
        )


# --- relevance scoring (resume-dependent) ------------------------------------
def get_unscored(limit: int | None = None) -> list[dict]:
    init_db()
    q = "SELECT * FROM roles WHERE relevance IS NULL AND status='active' ORDER BY first_seen DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    with connect() as conn:
        return [dict(r) for r in conn.execute(q).fetchall()]


def save_score(key: str, relevance: int | None, reason: str | None) -> None:
    with connect() as conn:
        conn.execute("UPDATE roles SET relevance=?, relevance_reason=? WHERE key=?",
                     (relevance, reason, key))


def mark_all_unscored() -> int:
    """Clear relevance for all roles (e.g. after the resume changes)."""
    init_db()
    with connect() as conn:
        cur = conn.execute("UPDATE roles SET relevance=NULL, relevance_reason=NULL")
        return cur.rowcount


# --- notifications -----------------------------------------------------------
def get_unnotified(min_impact: int = 0, limit: int | None = None) -> list[dict]:
    """Active, not-yet-notified roles. If min_impact>0, only enriched roles whose
    impact meets the threshold (so we wait for enrichment before alerting)."""
    init_db()
    q = "SELECT * FROM roles WHERE notified=0 AND status='active'"
    params: list = []
    if min_impact and min_impact > 0:
        q += " AND enriched=1 AND impact >= ?"
        params.append(int(min_impact))
    q += " ORDER BY impact DESC NULLS LAST, first_seen DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    for r in rows:
        r["skills"] = json.loads(r.get("skills") or "[]")
        r["tags"] = json.loads(r.get("tags") or "[]")
    return rows


def mark_notified(keys: list[str]) -> None:
    if not keys:
        return
    with connect() as conn:
        conn.executemany("UPDATE roles SET notified=1 WHERE key=?", [(k,) for k in keys])


# --- query helpers (used by the API) -----------------------------------------
def query_roles(*, status="active", company=None, category=None, source=None,
                seniority=None, min_impact=None, min_relevance=None, max_yoe=None,
                yoe_known=False, hide_phd=False, has_comp=False, search=None,
                sort="first_seen", order="desc", limit=200, offset=0) -> list[dict]:
    init_db()
    where = ["status = ?"]
    params: list = [status]
    if company:
        where.append("company = ?"); params.append(company)
    if category:
        where.append("company_category = ?"); params.append(category)
    if source:
        where.append("source_platform = ?"); params.append(source)
    if seniority:
        where.append("seniority = ?"); params.append(seniority)
    if min_impact:
        where.append("impact >= ?"); params.append(int(min_impact))
    if min_relevance:
        where.append("relevance >= ?"); params.append(int(min_relevance))
    if max_yoe is not None:
        # Include roles at/under the cap; unknown YOE included unless yoe_known.
        if yoe_known:
            where.append("yoe_min <= ?"); params.append(int(max_yoe))
        else:
            where.append("(yoe_min IS NULL OR yoe_min <= ?)"); params.append(int(max_yoe))
    if hide_phd:
        # Hide only HARD PhD requirements (LLM flag); "PhD or equivalent" stays.
        where.append("(phd_required IS NULL OR phd_required = 0)")
    if has_comp:
        where.append("comp_max IS NOT NULL")
    if search:
        where.append("(role_title LIKE ? OR company LIKE ? OR overview LIKE ?)")
        params += [f"%{search}%"] * 3
    allowed_sort = {"first_seen", "comp_max", "impact", "company", "role_title",
                    "yoe_min", "relevance"}
    sort = sort if sort in allowed_sort else "first_seen"
    order = "DESC" if str(order).lower() != "asc" else "ASC"
    q = (f"SELECT * FROM roles WHERE {' AND '.join(where)} "
         f"ORDER BY {sort} {order} NULLS LAST LIMIT ? OFFSET ?")
    params += [int(limit), int(offset)]
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    for r in rows:
        desc = (r.pop("description", "") or "").lower()   # drop heavy JD from list payload
        r["phd_mentioned"] = 1 if any(k in desc for k in
                                      ("phd", "ph.d", "doctoral", "doctorate")) else 0
        r["skills"] = json.loads(r.get("skills") or "[]")
        r["tags"] = json.loads(r.get("tags") or "[]")
    return rows


def facets() -> dict:
    init_db()
    with connect() as conn:
        def distinct(col):
            return [r[0] for r in conn.execute(
                f"SELECT DISTINCT {col} FROM roles WHERE status='active' AND {col} != '' "
                f"ORDER BY {col}").fetchall() if r[0]]
        counts = dict(conn.execute(
            "SELECT status, COUNT(*) FROM roles GROUP BY status").fetchall())
        return {
            "companies": distinct("company"),
            "categories": distinct("company_category"),
            "departments": distinct("category"),
            "sources": distinct("source_platform"),
            "counts": counts,
        }


# --- profile (resume) + apply-kit -------------------------------------------
def save_resume(text: str, filename: str, *, now: str) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO profile (id, resume_text, filename, updated_at) VALUES (1,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET resume_text=excluded.resume_text, "
            "filename=excluded.filename, updated_at=excluded.updated_at",
            (text, filename, now))


def get_resume() -> dict | None:
    init_db()
    with connect() as conn:
        r = conn.execute("SELECT resume_text, filename, updated_at FROM profile WHERE id=1").fetchone()
        return dict(r) if r else None


def get_role(key: str) -> dict | None:
    init_db()
    with connect() as conn:
        r = conn.execute("SELECT * FROM roles WHERE key=?", (key,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["skills"] = json.loads(d.get("skills") or "[]")
        d["tags"] = json.loads(d.get("tags") or "[]")
        return d


def get_applykit(key: str) -> dict | None:
    init_db()
    with connect() as conn:
        r = conn.execute("SELECT data FROM applykit WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else None


def save_applykit(key: str, data: dict, *, now: str) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO applykit (key, data, created_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET data=excluded.data, created_at=excluded.created_at",
            (key, json.dumps(data), now))


def stats() -> dict:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) total, "
            "SUM(status='active') active, "
            "SUM(enriched=1) enriched FROM roles").fetchone()
        return {"total": row[0], "active": row[1] or 0, "enriched": row[2] or 0}
