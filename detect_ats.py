#!/usr/bin/env python3
"""Detect ATS platform + token by reading a company's careers-page HTML.

Many companies embed their ATS board (Greenhouse/Ashby/Lever/Workable/Workday/
SmartRecruiters) via a script, iframe, or "view all jobs" link whose URL contains
the board token. We fetch candidate careers URLs, regex those signatures out, and
verify the guess against the live ATS API before trusting it.
"""
from __future__ import annotations

import re

import httpx

import config
from pollers import ashby, greenhouse, lever, smartrecruiters

H = {"User-Agent": config.USER_AGENT,
     "Accept": "text/html,application/xhtml+xml,application/json"}

# Signature -> (platform, regex capturing the token). First match wins.
SIGNATURES: list[tuple[str, re.Pattern]] = [
    ("greenhouse", re.compile(r"boards\.greenhouse\.io/embed/job_board\?for=([a-zA-Z0-9]+)")),
    ("greenhouse", re.compile(r"job-boards\.greenhouse\.io/([a-zA-Z0-9]+)")),
    ("greenhouse", re.compile(r"boards(?:-api)?\.greenhouse\.io/(?:v1/boards/)?([a-zA-Z0-9]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9\-]+)")),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([a-zA-Z0-9\-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([a-zA-Z0-9\-]+)")),
    ("lever", re.compile(r"api\.lever\.co/v0/postings/([a-zA-Z0-9\-]+)")),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([a-zA-Z0-9]+)")),
    ("smartrecruiters", re.compile(r"careers\.smartrecruiters\.com/([a-zA-Z0-9]+)")),
]

_VERIFY = {"greenhouse": greenhouse, "ashby": ashby, "lever": lever,
           "smartrecruiters": smartrecruiters}

# Workday is detected separately (token alone isn't enough; needs tenant+site+dc).
WORKDAY_RE = re.compile(r"https?://([a-z0-9]+)\.(wd\d+)\.myworkdayjobs\.com/([^/\"'<> ]+)")

_CANDIDATE_PATHS = ["/careers", "/careers/", "/jobs", "/jobs/", "/company/careers",
                    "/about/careers", "/careers/jobs", "/career", ""]


def _verify(platform: str, token: str) -> int | None:
    """Return job count if the (platform, token) is a live board, else None."""
    mod = _VERIFY.get(platform)
    if not mod:
        return None
    try:
        data = http_client_get(mod.probe_url(token))
        if mod.is_valid_payload(data):
            if platform == "smartrecruiters":
                return data.get("totalFound", 0)
            return len(data.get("jobs", []) if isinstance(data, dict) else data)
    except Exception:
        return None
    return None


def http_client_get(url: str):
    r = httpx.get(url, headers=H, timeout=12, follow_redirects=True)
    r.raise_for_status()
    return r.json()


def detect_from_html(html: str) -> tuple[str, str] | None:
    # Workday first (most specific).
    m = WORKDAY_RE.search(html)
    if m:
        tenant, dc, rest = m.group(1), m.group(2), m.group(3)
        return ("workday", f"{tenant}|{dc}|{rest}")
    for platform, rx in SIGNATURES:
        for m in rx.finditer(html):
            tok = m.group(1)
            if tok.lower() in ("embed", "v1", "boards", "job"):
                continue
            cnt = _verify(platform, tok)
            if cnt is not None:
                return (platform, tok)
    return None


def detect_company(domain: str) -> tuple[str, str, str] | None:
    """Try candidate careers URLs on `domain`; return (platform, token, source_url)."""
    base = domain if domain.startswith("http") else f"https://{domain}"
    for path in _CANDIDATE_PATHS:
        url = base.rstrip("/") + path
        try:
            r = httpx.get(url, headers=H, timeout=12, follow_redirects=True)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        hit = detect_from_html(r.text)
        if hit:
            return (hit[0], hit[1], url)
    return None


# Domain guesses for the unresolved startup tail.
DOMAIN_GUESSES: dict[str, list[str]] = {
    "AI21 Labs": ["ai21.com"],
    "Sakana AI": ["sakana.ai"],
    "Magic.dev": ["magic.dev"],
    "Replicate": ["replicate.com"],
    "Fal.ai": ["fal.ai"],
    "Predibase": ["predibase.com"],
    "Groq": ["groq.com"],
    "SambaNova": ["sambanova.ai"],
    "Windsurf": ["windsurf.com", "codeium.com"],
    "Augment Code": ["augmentcode.com"],
    "Sourcegraph": ["sourcegraph.com"],
    "Tabnine": ["tabnine.com"],
    "All Hands AI": ["all-hands.dev"],
    "Hebbia": ["hebbia.ai", "hebbia.com"],
    "Sana": ["sanalabs.com"],
    "MultiOn": ["multion.ai"],
    "11x": ["11x.ai"],
    "PlayAI": ["play.ai", "playht.com"],
    "Midjourney": ["midjourney.com"],
    "Captions": ["captions.ai"],
    "Hippocratic AI": ["hippocraticai.com"],
    "Recursion": ["recursion.com"],
    "Cradle": ["cradle.bio"],
    "EvenUp": ["evenuplaw.com"],
    "Clay": ["clay.com"],
    "Hugging Face": ["huggingface.co"],
    "Weights & Biases": ["wandb.ai", "wandb.com"],
    "Skild AI": ["skild.ai"],
    "Boston Dynamics": ["bostondynamics.com"],
    "NVIDIA": ["nvidia.com"],
    "Adobe": ["adobe.com"],
    "Salesforce": ["salesforce.com"],
    "Qualcomm": ["qualcomm.com"],
    "IBM Research": ["research.ibm.com", "ibm.com"],
    "Tesla": ["tesla.com"],
    "Uber": ["uber.com"],
}


def main():
    import sys
    only = set(sys.argv[1:])
    items = {k: v for k, v in DOMAIN_GUESSES.items() if not only or k in only}
    hits, misses = {}, []
    for company, domains in items.items():
        found = None
        for d in domains:
            found = detect_company(d)
            if found:
                break
        if found:
            hits[company] = found
            print(f"{company:22s} -> {found[0]}:{found[1]}   (via {found[2]})", flush=True)
        else:
            misses.append(company)
            print(f"{company:22s} -> no ATS detected", flush=True)
    print("\n=== DETECTED ===")
    for c, (p, t, u) in hits.items():
        print(f"  {c:22s} {p}:{t}")
    print(f"\n=== STILL UNRESOLVED ({len(misses)}) ===\n  " + ", ".join(misses))


if __name__ == "__main__":
    main()
