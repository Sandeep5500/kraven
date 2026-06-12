"""Central configuration: paths, HTTP settings, title filters.

Everything tunable lives here so the runner/pollers stay generic.
"""
from __future__ import annotations

from pathlib import Path

# --- Paths -------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
WATCHLIST_RAW = ROOT / "ai_companies_watchlist.csv"
WATCHLIST_RESOLVED = ROOT / "watchlist_resolved.csv"
STATE_DIR = ROOT / "state"
SEEN_STORE = STATE_DIR / "seen.json"
LOG_FILE = ROOT / "run.log"

# --- HTTP --------------------------------------------------------------------
USER_AGENT = (
    "ai-jobs-runner/1.0 (+https://github.com/; personal job-discovery bot; "
    "contact: sandeepalt308@gmail.com)"
)
HTTP_TIMEOUT = 10.0          # seconds
HTTP_RETRIES = 2             # extra attempts on 5xx / 429
HTTP_BACKOFF = 1.5           # seconds, multiplied per retry attempt

# Politeness between calls (seconds). Lever's robots.txt asks Crawl-delay: 1.
SLEEP_BETWEEN_CALLS = 0.4
LEVER_CRAWL_DELAY = 1.0

# --- Big-board search (Workday + big-tech custom APIs) -----------------------
# Big boards (Workday, amazon.jobs, etc.) have thousands of roles; rather than
# page all of them every run we query these role-relevant search terms
# server-side and union the results. The normal title filter still applies.
ROLE_SEARCH_TERMS = [
    "machine learning",
    "software engineer",
    "applied scientist",
    "research scientist",
    "AI engineer",
    "deep learning",
]
WORKDAY_SEARCH_TERMS = ROLE_SEARCH_TERMS  # back-compat alias
WORKDAY_PAGE = 20            # Workday's max page size
WORKDAY_MAX_PAGES = 15      # safety cap per search term (logged if hit)

# Big-tech custom-API paging
BIGTECH_PAGE = 100          # page size where the API allows it
BIGTECH_MAX_PAGES = 5       # safety cap per search term

# --- Manual ATS overrides ----------------------------------------------------
# Companies whose board token can't be derived from the name (verified by hand).
# Keyed by the exact "Company" value in the CSV -> (platform, token).
# The resolver still verifies these are live before writing them.
ATS_OVERRIDES: dict[str, tuple[str, str]] = {
    "Glean": ("greenhouse", "gleanwork"),
    "Patronus AI": ("greenhouse", "patronusaiinc"),
    "ServiceNow": ("smartrecruiters", "servicenow"),
    "ByteDance / TikTok": ("smartrecruiters", "bytedance"),
    # Resolved by reading careers-page HTML (detect_ats.py), verified live.
    "Fal.ai": ("greenhouse", "fal"),
    "Sourcegraph": ("greenhouse", "sourcegraph91"),
    "Recursion": ("greenhouse", "recursionpharmaceuticals"),
    "All Hands AI": ("ashby", "OpenHands"),
    "Hebbia": ("ashby", "hebbia-ai"),
    "Sana": ("ashby", "sana-roles"),
    "Captions": ("ashby", "mirage"),
    "Cradle": ("ashby", "cradlebio"),
    "Clay": ("ashby", "claylabs"),
    "SambaNova": ("greenhouse", "sambanovasystems"),
    "Augment Code": ("greenhouse", "augmentcomputing"),
    "Magic.dev": ("greenhouse", "magic"),
    "Skild AI": ("greenhouse", "skildai-careers"),
    "Hippocratic AI": ("ashby", "Hippocratic AI"),
    "Hugging Face": ("workable", "huggingface"),
    # NOTE: Windsurf was acquired by Cognition; its careers page now serves
    # Cognition's Ashby board -> intentionally NOT added (would duplicate Cognition).
    # Workday boards: token is "tenant|datacenter|site".
    "NVIDIA": ("workday", "nvidia|wd5|NVIDIAExternalCareerSite"),
    "Adobe": ("workday", "adobe|wd5|external_experienced"),
    "Salesforce": ("workday", "salesforce|wd12|External_Career_Site"),
    "Boston Dynamics": ("workday", "bostondynamics|wd1|Boston_Dynamics"),
    # Big-tech custom public API (token unused; single board).
    "Amazon": ("amazon", "amazon"),
}

# --- Title filtering ---------------------------------------------------------
# A role is kept if (it has NO exclude term) AND
#   (it has a CORE AI/ML/research role term)  OR
#   (it has a generic SWE term AND an AI/ML qualifier).
# This keeps AI/ML engineering + research and AI-centric SWE
# ("Software Engineer, AI Platform"), while dropping generic SWE/data/infra and
# non-engineering roles that merely mention "AI" (architects, AEs, etc.).
INCLUDE_TITLE_TERMS = [          # CORE: the role itself is AI/ML/research
    "machine learning",
    "ml engineer", "mle",
    "ai engineer",
    "applied scientist",
    "research scientist",
    "research engineer",
    "deep learning",
    "computer vision",
    "nlp", "natural language",
    "llm", "llms",
    "generative ai", "genai",
    "reinforcement learning",
    "foundation model", "foundation models",
    "neural",
    "mlops", "ml ops",
    "perception",                # ML perception (robotics/AV)
    "member of technical staff",
]

# Generic SWE terms — included ONLY when paired with an AIML_QUALIFIER below.
SWE_TERMS = ["software engineer", "swe", "software developer"]

# AI/ML signal used to qualify a generic SWE title.
AIML_QUALIFIERS = [
    "ai", "ml", "machine learning", "deep learning", "llm", "nlp",
    "computer vision", "generative ai", "genai", "reinforcement learning",
    "foundation model", "neural", "mlops",
]

EXCLUDE_TITLE_TERMS = [
    "manager", "director", "vp", "vice president",
    "recruiter", "sales", "marketing",
    # Adjacent / non-AI roles to drop even if they mention ai/ml in passing.
    "data engineer", "data analyst", "analytics engineer",
    "business intelligence", "bi engineer", "data platform",
    # Non-engineering roles that often carry "AI" in the title.
    "architect", "account executive", "solutions engineer",
    "solutions consultant", "consultant", "evangelist", "developer advocate",
    "product manager", "program manager", "designer", "customer success",
    "go to market", "business development", "partnerships",
]

# Set INCLUDE_INTERNS = True to also surface intern / new-grad / return-offer roles.
INCLUDE_INTERNS = False
INTERN_TERMS = ["intern", "internship"]

# --- Seniority filter --------------------------------------------------------
# EXCLUDE_SENIOR drops senior/staff/principal/lead/etc. so the feed skews
# mid-level and below. ("manager", "director", "vp" are already excluded above.)
EXCLUDE_SENIOR = True
SENIORITY_EXCLUDE_TERMS = [
    "senior", "sr", "staff", "principal", "lead", "distinguished", "fellow",
    "head", "vice president", "svp", "evp",
]
# "staff" must NOT exclude "Member of Technical Staff" (an IC role we want).
SENIORITY_KEEP_PHRASES = ["technical staff"]

# NEW_GRAD_ONLY further restricts to entry-level / new-grad roles (requires one
# of these signals in the title). Off by default = "non-senior" (broader).
# Cap how many roles a single company can post per run (newest first), so no one
# thread floods. Roles beyond the cap are still marked seen (they won't post in a
# later run). Set to 0 to disable.
MAX_ROLES_PER_COMPANY_PER_RUN = 30

NEW_GRAD_ONLY = False
NEW_GRAD_TERMS = [
    "new grad", "new graduate", "recent graduate", "university grad",
    "early career", "early in career", "entry level", "entry-level",
    "associate", "campus", "rotational",
]

# --- Location filter (US-only) -----------------------------------------------
US_ONLY = True
# Bare "Remote" with no country named -> treat as US (most US-HQ'd AI cos).
US_ALLOW_REMOTE = True
# Genuinely ambiguous locations (e.g. Workday "2 Locations", empty) when US_ONLY:
# False = drop them (strict), True = keep them.
US_ALLOW_AMBIGUOUS = False

# Country values (from richer feeds like Amazon/Workable) that count as US.
US_COUNTRY_VALUES = {"us", "usa", "u.s.", "u.s.a.", "united states",
                     "united states of america"}

# Substrings that mark a US location (matched on punctuation-stripped lowercase).
US_LOCATION_TERMS = ["united states", "usa", "us remote", "remote us"]

# US state names + abbreviations (abbr matched only after a comma, e.g. ", CA").
US_STATES_FULL = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia",
]
US_STATE_ABBRS = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
]
# Major US cities/tech hubs that feeds often list WITHOUT a state (e.g. just
# "San Francisco"). Checked after explicit non-US markers so "Cambridge, UK" or
# "San Jose, Costa Rica" still resolve non-US.
US_CITIES = [
    "san francisco", "south san francisco", "new york", "new york city", "nyc",
    "seattle", "palo alto", "mountain view", "menlo park", "san jose",
    "santa clara", "sunnyvale", "cupertino", "redwood city", "san mateo",
    "bellevue", "redmond", "los angeles", "san diego", "santa monica",
    "culver city", "el segundo", "pasadena", "irvine", "long beach", "oakland",
    "berkeley", "fremont", "emeryville", "boston", "cambridge", "somerville",
    "austin", "dallas", "plano", "houston", "chicago", "denver", "boulder",
    "atlanta", "washington dc", "arlington", "reston", "pittsburgh",
    "philadelphia", "miami", "portland", "salt lake city", "phoenix", "tempe",
    "san antonio", "minneapolis", "detroit", "ann arbor", "nashville",
    "raleigh", "durham", "charlotte", "columbus", "kansas city", "las vegas",
    "brooklyn", "bentonville", "remote, united states", "remote us",
]
# Clear non-US markers (countries/cities). If present without any US signal -> drop.
NON_US_MARKERS = [
    "canada", "united kingdom", "australia", "germany", "france", "india",
    "ireland", "israel", "singapore", "japan", "netherlands", "spain", "italy",
    "poland", "brazil", "mexico", "china", "korea", "taiwan", "switzerland",
    "sweden", "norway", "denmark", "finland", "portugal", "romania", "czech",
    "austria", "belgium", "greece", "turkey", "uae", "dubai", "qatar",
    "philippines", "vietnam", "thailand", "malaysia", "indonesia", "argentina",
    "colombia", "chile", "egypt", "nigeria", "kenya", "south africa", "ukraine",
    "hungary", "bulgaria", "croatia", "serbia", "estonia", "lithuania", "latvia",
    "london", "toronto", "vancouver", "montreal", "ottawa", "berlin", "munich",
    "paris", "bangalore", "bengaluru", "hyderabad", "mumbai", "pune", "delhi",
    "gurgaon", "chennai", "dublin", "sydney", "melbourne", "tokyo", "amsterdam",
    "zurich", "tel aviv", "são paulo", "sao paulo", "warsaw", "barcelona",
    "madrid", "lisbon", "stockholm", "copenhagen", "helsinki", "oslo", "milan",
    "remote - emea", "remote - apac", "remote - uk", "remote-emea",
    "remote, canada", "remote - canada", "emea", "apac", "latam",
    "costa rica", "colombia", "peru", "uruguay", "new zealand",
]

# --- Slack -------------------------------------------------------------------
SLACK_BATCH_SIZE = 10        # roles per Block Kit message (flat/webhook mode)
SLACK_MIN_INTERVAL = 1.0     # seconds between Slack requests (>=1 req/sec rule)
SLACK_RETRIES = 3
SLACK_BACKOFF = 2.0

# Posting mode:
#   - If SLACK_BOT_TOKEN (xoxb-...) is set AND SLACK_THREADED is True, the runner
#     posts each company's roles as replies under a per-company thread, grouped
#     into category channels (hybrid layout). Requires a Slack app with scopes:
#     chat:write, channels:manage, channels:read (+ channels:join for existing
#     public channels the bot must post to).
#   - Otherwise it falls back to the flat incoming-webhook (SLACK_WEBHOOK_URL).
SLACK_THREADED = True

# Optional "home" channel id (Cxxxxx) for the init/summary message in threaded
# mode. If unset, the init message is skipped (only seeding happens).
SLACK_HOME_CHANNEL = ""      # or set via env SLACK_HOME_CHANNEL

# Auto-create category channels if missing. If False, every category must be
# mapped explicitly in CATEGORY_CHANNELS.
AUTO_CREATE_CHANNELS = True
CATEGORY_CHANNEL_PREFIX = "ai-jobs-"

# Create PRIVATE channels (recommended in a shared workspace). Requires the
# groups:write + groups:read bot scopes. Public channels instead need
# channels:manage + channels:read + channels:join.
SLACK_PRIVATE_CHANNELS = True

# Optional: bot invites these Slack user IDs to every channel it creates, so the
# team can see private channels. Get an id from a profile -> "Copy member ID"
# (starts with U...). Empty = invite people manually.
SLACK_INVITE_USERS: list[str] = ["U0BA2DNTE6M"]

# Explicit category -> channel id overrides (Cxxxxx). Anything not listed is
# resolved by name (CATEGORY_CHANNEL_PREFIX + slug) and created/looked-up live.
CATEGORY_CHANNELS: dict[str, str] = {}

# Short, stable channel slugs per watchlist Category. Falls back to a slug of the
# category name for anything not listed here.
CATEGORY_SLUGS: dict[str, str] = {
    "Frontier / foundation models": "frontier",
    "AI infrastructure & compute": "infra",
    "Coding & dev tools": "coding",
    "Search & enterprise knowledge": "search",
    "Agents & automation": "agents",
    "Voice & audio": "voice",
    "Vision / video & creative": "vision",
    "Healthcare & bio AI": "healthcare",
    "Legal / finance & vertical": "legal",
    "Data / eval & ML tooling": "data-ml",
    "Robotics & autonomy": "robotics",
    "Big tech & established AI": "bigtech",
}

# State files for threaded mode (gitignored under state/).
CHANNELS_STORE = STATE_DIR / "channels.json"   # {category: channel_id}
THREADS_STORE = STATE_DIR / "threads.json"     # {company: {channel, ts}}
