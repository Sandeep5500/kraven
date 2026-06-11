"""Normalize raw ATS job objects into one common record shape, and apply the
ML/SWE title filter.

Common record:
    {
        "company":         str,
        "role_title":      str,
        "location":        str,
        "url":             str,
        "category":        str,
        "posted_at":       str | None,   # ISO 8601 where available
        "source_platform": str,          # greenhouse | lever | ashby
        "job_id":          str,
    }

De-dupe key: f"{company}:{source_platform}:{job_id}"
"""
from __future__ import annotations

from datetime import datetime, timezone

import config


def dedupe_key(record: dict) -> str:
    return f"{record['company']}:{record['source_platform']}:{record['job_id']}"


# --- Per-platform normalizers ------------------------------------------------
def normalize_greenhouse(company: str, job: dict) -> dict:
    loc = (job.get("location") or {}).get("name") or ""
    departments = job.get("departments") or []
    category = departments[0]["name"] if departments and departments[0].get("name") else ""
    return {
        "company": company,
        "role_title": (job.get("title") or "").strip(),
        "location": loc.strip(),
        "url": job.get("absolute_url") or "",
        "category": category.strip(),
        "posted_at": job.get("updated_at"),
        "source_platform": "greenhouse",
        "job_id": str(job.get("id")),
    }


def normalize_lever(company: str, job: dict) -> dict:
    cats = job.get("categories") or {}
    posted_at = None
    created = job.get("createdAt")
    if isinstance(created, (int, float)):
        # Lever createdAt is epoch milliseconds.
        posted_at = datetime.fromtimestamp(created / 1000, tz=timezone.utc).isoformat()
    return {
        "company": company,
        "role_title": (job.get("text") or "").strip(),
        "location": (cats.get("location") or "").strip(),
        "url": job.get("hostedUrl") or job.get("applyUrl") or "",
        "category": (cats.get("team") or "").strip(),
        "posted_at": posted_at,
        "source_platform": "lever",
        "job_id": str(job.get("id")),
    }


def normalize_amazon(company: str, job: dict) -> dict:
    path = job.get("job_path") or ""
    url = f"https://www.amazon.jobs{path}" if path else ""
    team = job.get("team")
    if isinstance(team, dict):
        team = team.get("label") or team.get("name")
    category = team or job.get("job_category") or job.get("business_category") or ""
    return {
        "company": company,
        "role_title": (job.get("title") or "").strip(),
        "location": (job.get("normalized_location") or job.get("location") or "").strip(),
        "url": url,
        "category": str(category).strip(),
        "posted_at": job.get("posted_date"),
        "source_platform": "amazon",
        "job_id": str(job.get("id_icims") or job.get("id")),
        "country": (job.get("country_code") or "").strip(),
    }


def normalize_workable(company: str, job: dict) -> dict:
    loc = ", ".join(b for b in (job.get("city"), job.get("state"), job.get("country")) if b)
    if job.get("telecommuting"):
        loc = (loc + " (remote)").strip()
    return {
        "company": company,
        "role_title": (job.get("title") or "").strip(),
        "location": loc.strip(),
        "url": job.get("url") or job.get("application_url") or "",
        "category": (job.get("department") or "").strip(),
        "posted_at": job.get("published_on") or job.get("created_at"),
        "source_platform": "workable",
        "job_id": str(job.get("shortcode") or job.get("code")),
        "country": (job.get("country") or "").strip(),
    }


def normalize_workday(company: str, job: dict, *, tenant: str, dc: str, site: str) -> dict:
    # externalPath is the stable unique identifier, e.g. "/job/US-CA/Foo_JR123".
    path = job.get("externalPath") or ""
    url = f"https://{tenant}.{dc}.myworkdayjobs.com/en-US/{site}{path}" if path else ""
    # Workday's postedOn is relative text ("Posted Today") -> not a real date; we
    # leave posted_at None and rely on the stable job_id for de-dupe.
    return {
        "company": company,
        "role_title": (job.get("title") or "").strip(),
        "location": (job.get("locationsText") or "").strip(),
        "url": url,
        "category": "",
        "posted_at": None,
        "source_platform": "workday",
        "job_id": path or str(job.get("bulletFields")),
    }


def normalize_smartrecruiters(company: str, job: dict) -> dict:
    loc = job.get("location") or {}
    location = loc.get("fullLocation") or ", ".join(
        b for b in (loc.get("city"), loc.get("region"), loc.get("country")) if b
    )
    if loc.get("remote"):
        location = (location + " (remote)").strip()
    dept = (job.get("department") or {}).get("label") or ""
    identifier = (job.get("company") or {}).get("identifier") or ""
    job_id = str(job.get("id"))
    url = f"https://jobs.smartrecruiters.com/{identifier}/{job_id}" if identifier else ""
    return {
        "company": company,
        "role_title": (job.get("name") or "").strip(),
        "location": location.strip(),
        "url": url,
        "category": dept.strip(),
        "posted_at": job.get("releasedDate"),
        "source_platform": "smartrecruiters",
        "job_id": job_id,
    }


def normalize_ashby(company: str, job: dict) -> dict:
    return {
        "company": company,
        "role_title": (job.get("title") or "").strip(),
        "location": (job.get("location") or "").strip(),
        "url": job.get("jobUrl") or "",
        "category": (job.get("department") or job.get("team") or "").strip(),
        "posted_at": job.get("publishedAt") or job.get("updatedAt"),
        "source_platform": "ashby",
        "job_id": str(job.get("id")),
    }


# --- Title filter ------------------------------------------------------------
import re  # noqa: E402


def _normalize_title(title: str) -> str:
    """Lowercase and replace runs of non-alphanumerics with single spaces, so
    word-boundary matching is robust to punctuation/hyphens (e.g. "ML/AI",
    "machine-learning")."""
    return " " + re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip() + " "


def _has_term(terms: list[str], normalized: str) -> bool:
    """Whole-word match: avoids 'swe' matching 'Bundeswehr', 'intern' matching
    'internal', etc. Multi-word terms match as adjacent words."""
    for term in terms:
        pat = r"\b" + re.escape(term.strip()) + r"\b"
        if re.search(pat, normalized):
            return True
    return False


def title_matches(title: str) -> bool:
    """True if the title passes the ML/SWE include/exclude filter."""
    if not (title or "").strip():
        return False
    t = _normalize_title(title)

    if not config.INCLUDE_INTERNS and _has_term(config.INTERN_TERMS, t):
        return False

    if _has_term(config.EXCLUDE_TITLE_TERMS, t):
        return False

    if config.EXCLUDE_SENIOR and _is_senior(t):
        return False

    if config.NEW_GRAD_ONLY and not _has_term(config.NEW_GRAD_TERMS, t):
        return False

    # Core AI/ML/research role term...
    if _has_term(config.INCLUDE_TITLE_TERMS, t):
        return True
    # ...or a generic SWE title qualified by an AI/ML signal.
    if _has_term(config.SWE_TERMS, t) and _has_term(config.AIML_QUALIFIERS, t):
        return True
    return False


def _is_senior(normalized: str) -> bool:
    """True if the title looks senior+. 'staff' is ignored when it's part of a
    kept phrase like 'Member of Technical Staff'."""
    keep = any(p in normalized for p in config.SENIORITY_KEEP_PHRASES)
    for term in config.SENIORITY_EXCLUDE_TERMS:
        if term == "staff" and keep:
            continue
        if re.search(r"\b" + re.escape(term) + r"\b", normalized):
            return True
    return False


_ABBR_RE = re.compile(r",\s*(" + "|".join(config.US_STATE_ABBRS) + r")\b")


def is_us_location(location: str, country: str = "") -> bool:
    """Heuristic: is this role in the US?

    Uses the exact country value when a feed provides one (Amazon country_code,
    Workable country); otherwise inspects the location string. Ambiguous values
    ("Remote", "2 Locations", "") are governed by US_ALLOW_REMOTE / _AMBIGUOUS.
    """
    c = (country or "").strip().lower().replace(".", "")
    if c:
        return c in {v.replace(".", "") for v in config.US_COUNTRY_VALUES}

    loc = location or ""
    if not loc.strip():
        return config.US_ALLOW_AMBIGUOUS

    # State abbreviation after a comma (e.g. "Santa Clara, CA") — original case.
    if _ABBR_RE.search(loc):
        return True

    t = " " + re.sub(r"[^a-z0-9]+", " ", loc.lower()).strip() + " "
    if any(term in t for term in config.US_LOCATION_TERMS):
        return True
    if re.search(r"\bus\b", t):
        return True
    if any(f" {s} " in t for s in config.US_STATES_FULL):
        return True

    # Explicit non-US signals.
    if any(m in t for m in config.NON_US_MARKERS):
        return False
    if re.search(r"\buk\b", t):
        return False

    # Ambiguous (no US, no known non-US): "Remote", "Hybrid", etc.
    if "remote" in t:
        return config.US_ALLOW_REMOTE
    return config.US_ALLOW_AMBIGUOUS
