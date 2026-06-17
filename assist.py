"""Apply-kit generation: given the user's resume + a role, draft outreach/email/
referral messages and application-essay first drafts via the OpenAI-compatible
model (same endpoint as enrichment).
"""
from __future__ import annotations

import enrich  # reuses call_model (OpenAI-compatible client)

_SYSTEM = (
    "You are an expert tech-career writer helping a candidate apply to AI/ML roles. "
    "Write concise, specific, non-generic copy grounded in the candidate's actual "
    "resume and the specific role. No clichés, no made-up facts. "
    "Reply with ONLY a single JSON object, no markdown fences."
)

_SCHEMA = """Return JSON with exactly these keys (all strings unless noted):
{
  "linkedin_outreach": "<<=400 chars DM to someone at the company (hiring manager/teammate). Warm, specific, 1 ask for a chat/referral>",
  "recruiter_email": "<short email to a recruiter: subject line on first line as 'Subject: ...', then 2 short paragraphs>",
  "referral_message": "<message to a friend/contact who could refer the candidate: friendly, makes it easy for them, 1 short paragraph>",
  "essays": [
     {"q": "Why this company/role?", "a": "<3-4 sentence draft grounded in resume + role>"},
     {"q": "Why are you a strong fit?", "a": "<3-4 sentences citing concrete resume experience matched to the role>"},
     {"q": "A relevant project or accomplishment", "a": "<3-4 sentences on the most relevant resume project>"}
  ]
}"""


def generate(role: dict, resume_text: str) -> dict | None:
    desc = enrich._strip_html(role.get("description") or "")[:4000]
    resume = (resume_text or "")[:6000]
    user = (
        f"{_SCHEMA}\n\n"
        f"=== ROLE ===\n"
        f"Company: {role.get('company')}\n"
        f"Title: {role.get('role_title')}\n"
        f"Location: {role.get('location')}\n"
        f"Overview: {role.get('overview') or ''}\n"
        f"Job description:\n{desc or '(none)'}\n\n"
        f"=== CANDIDATE RESUME ===\n{resume or '(no resume provided)'}"
    )
    messages = [{"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user}]
    data = enrich.call_model(messages, max_tokens=1500, temperature=0.4)
    if not data:
        return None
    # light normalization
    if not isinstance(data.get("essays"), list):
        data["essays"] = []
    for k in ("linkedin_outreach", "recruiter_email", "referral_message"):
        data[k] = (data.get(k) or "").strip()
    return data
