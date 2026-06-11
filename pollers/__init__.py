"""ATS pollers. Each exposes `fetch(company, token) -> list[dict]` of normalized
common records, and `BASE_URL` / `probe_url(slug)` for Phase 1 resolution.
"""
