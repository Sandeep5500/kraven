#!/usr/bin/env python3
"""Resolve fintech / quant-finance ML companies into the watchlist.

Adds a 'Fintech ML' category covering quant trading, banks' ML teams, and
fintech-product companies with strong ML hiring.

Usage:
  python fintech_companies.py            # resolve + append new companies
  python fintech_companies.py --dry-run  # print results without writing
"""
from __future__ import annotations

import argparse
import csv
import re
from concurrent.futures import ThreadPoolExecutor

import httpx

import config
from pollers import ashby, greenhouse, lever, workable, smartrecruiters
from resolve_ats import candidate_slugs

CATEGORY = "Fintech ML"
H = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}
PROBE = [ashby, greenhouse, lever, workable, smartrecruiters]

# Curated list: (display_name, description, extra_slugs_to_try)
# extra_slugs: try these in addition to auto-generated slugs (for firms whose
# ATS board uses a non-obvious token, e.g. "gs" for Goldman Sachs).
FINTECH_COMPANIES = [
    # ── Quant / prop trading ──────────────────────────────────────────────
    ("Two Sigma",             "Quant hedge fund, heavy ML/data science",    ["twosigma", "two-sigma"]),
    ("Citadel",               "Quant hedge fund + securities",               ["citadel"]),
    ("Jane Street",           "Quant trading, OCaml + ML",                   ["jane-street", "janestreet"]),
    ("D.E. Shaw",             "Quant hedge fund",                            ["de-shaw", "deshaw", "d-e-shaw"]),
    ("Hudson River Trading",  "Quant HFT, ML infra",                        ["hudson-river-trading", "hrt"]),
    ("Jump Trading",          "Quant prop trading",                          ["jump-trading", "jumptrading"]),
    ("Optiver",               "Market maker, ML + low-latency",              ["optiver"]),
    ("IMC Trading",           "Market maker, ML",                            ["imc-trading", "imc"]),
    ("Akuna Capital",         "Options market maker, ML",                    ["akuna-capital", "akuna"]),
    ("Tower Research Capital","HFT, ML",                                     ["tower-research-capital", "tower-research"]),
    ("Five Rings",            "Quant trading",                               ["five-rings", "fiverings"]),
    ("SIG",                   "Susquehanna, quant trading",                  ["sig", "susquehanna"]),
    ("Virtu Financial",       "HFT market maker",                            ["virtu", "virtu-financial"]),
    ("Point72",               "Quant hedge fund (Steve Cohen)",              ["point72"]),
    ("Millennium Management", "Quant multi-strategy",                        ["millennium", "millennium-management"]),
    ("Squarepoint Capital",   "Quant fund",                                  ["squarepoint", "squarepoint-capital"]),
    ("XTX Markets",           "Electronic market maker, ML",                 ["xtx", "xtx-markets"]),
    ("Cubist Systematic",     "Point72 quant arm",                           ["cubist", "cubist-systematic"]),
    # ── Banks / asset managers with strong ML teams ────────────────────────
    ("Goldman Sachs",         "GS AI / quant engineering",                   ["goldman-sachs", "goldmansachs", "gs"]),
    ("JPMorgan Chase",        "JPMC AI research + quant",                    ["jpmorgan", "jp-morgan", "jpmorganchase"]),
    ("Morgan Stanley",        "MS ML / quant",                               ["morgan-stanley", "morganstanley"]),
    ("BlackRock",             "Aladdin ML + systematic investing",           ["blackrock"]),
    ("Capital One",           "Tech-forward bank, strong ML",                ["capitalone", "capital-one-tech"]),
    ("Bloomberg",             "Financial data + ML/NLP",                     ["bloomberg"]),
    # ── Fintech product ────────────────────────────────────────────────────
    ("Stripe",                "Payments infra, ML fraud/risk",               ["stripe"]),
    ("Robinhood",             "Retail trading, ML",                          ["robinhood"]),
    ("Affirm",                "BNPL, credit ML",                             ["affirm"]),
    ("Plaid",                 "Fintech data infra",                          ["plaid"]),
    ("Chime",                 "Neobank, ML fraud/risk",                      ["chime"]),
    ("Brex",                  "Business finance, ML",                        ["brex"]),
    ("Ramp",                  "Corporate cards + spend AI",                  ["ramp"]),
    ("Coatue",                "Tech-focused hedge fund",                     ["coatue"]),
    ("Alphasense",            "Market intelligence AI / finance NLP",        ["alphasense", "alpha-sense"]),
    ("Kensho",                "S&P ML/AI for finance",                       ["kensho"]),
    ("Vise",                  "AI wealth management",                        ["vise"]),
    ("Mosaic",                "Finance AI / risk",                           ["mosaic", "mosaic-ml"]),
    # ── AI-native lending / credit ML ─────────────────────────────────────
    ("Upstart",               "AI-driven lending, credit ML",                ["upstart"]),
    ("Zest AI",               "ML underwriting / credit decisioning",        ["zestai", "zest-ai", "zest"]),
    ("Pagaya",                "AI credit / consumer lending",                ["pagaya"]),
    ("Nova Credit",           "ML credit scoring, alt data",                 ["nova-credit", "novacredit"]),
    ("Blend",                 "Digital lending platform, ML",                ["blend"]),
    ("LendingClub",           "Marketplace lending, ML risk",                ["lendingclub"]),
    ("Prism Data",            "ML cash-flow underwriting",                   ["prism-data", "prismdata"]),
    # ── Trading / investing ML ─────────────────────────────────────────────
    ("Coinbase",              "Crypto exchange, ML risk/fraud",              ["coinbase"]),
    ("Marqeta",               "Card issuing, payments ML",                   ["marqeta"]),
    ("Alpaca",                "Broker API + trading ML",                     ["alpaca"]),
    ("Public",                "Investing app, ML",                           ["public-holdings", "publicholdingscom"]),
    ("Betterment",            "Robo-advisor, portfolio ML",                  ["betterment"]),
    ("Wealthfront",           "Robo-advisor, ML",                            ["wealthfront"]),
    # ── Banking infra / data ──────────────────────────────────────────────
    ("Modern Treasury",       "Payment ops + reconciliation ML",             ["modern-treasury", "moderntreasury"]),
    ("Mercury",               "Business banking, ML",                        ["mercury"]),
    ("Column",                "National bank + API, data/ML",                ["column", "column-na"]),
    ("SoFi",                  "Neobank + lending, ML",                       ["sofi"]),
    ("NerdWallet",            "Finance comparison, ML personalization",      ["nerdwallet"]),
    ("Pinwheel",              "Payroll API, income/employment ML",           ["pinwheel"]),
    # ── Quant / alt data ──────────────────────────────────────────────────
    ("Numerai",               "ML-powered hedge fund, data science",         ["numerai"]),
    ("Orca Security",         "Cloud security ML (fintech infra adjacent)",  ["orca-security", "orcasecurity"]),
    ("Dune Analytics",        "On-chain data + ML",                          ["dune", "dune-analytics"]),
    # ── Payments processors ────────────────────────────────────────────────
    ("Checkout.com",          "Payments processor, ML risk",                 ["checkout", "checkoutcom", "checkout-com"]),
    ("Adyen",                 "Payments processor, ML risk",                 ["adyen"]),
    ("Braintree",             "PayPal payments, ML fraud",                   ["braintree"]),
    ("Mastercard",            "Card network, AI/ML fraud + data",            ["mastercard"]),
    ("Visa",                  "Card network, AI/ML fraud + data",            ["visa"]),
    ("Wise",                  "Cross-border payments, ML risk",              ["wise", "transferwise"]),
    ("Revolut",               "Neobank, ML fraud/risk",                      ["revolut"]),
    # ── Fraud / AML / risk-engineering ML (vertical specialists) ──────────
    ("Unit21",                "Fraud + AML detection ML platform",           ["unit21"]),
    ("Sardine",               "Fraud + AML ML, device/behavior signals",     ["sardine", "sardine-ai"]),
    ("Featurespace",          "Adaptive-behavioral fraud ML",                ["featurespace"]),
    ("Feedzai",               "Fraud/AML risk ML platform",                  ["feedzai"]),
]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def resolve(name: str, extra_slugs: list[str]) -> tuple[str, str] | None:
    auto_slugs = [s for s in candidate_slugs(name)[:6] if s not in extra_slugs]
    cn = _norm(name)
    with httpx.Client(headers=H, timeout=10, follow_redirects=True) as cli:
        for slug in extra_slugs + auto_slugs:
            is_curated = slug in extra_slugs
            for mod in PROBE:
                try:
                    r = cli.get(mod.probe_url(slug))
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    if not mod.is_valid_payload(data):
                        continue
                    jobs = data.get("jobs") if isinstance(data, dict) else data
                    if not jobs:
                        continue
                    sn = _norm(slug)
                    if mod.PLATFORM == "greenhouse":
                        comp = _norm(jobs[0].get("company_name", ""))
                        if comp and not (cn in comp or comp in cn or comp[:5] == cn[:5]):
                            continue
                    elif mod.PLATFORM == "workable":
                        nm = _norm(data.get("name", ""))
                        if nm and not (cn in nm or nm in cn or nm[:5] == cn[:5]):
                            continue
                    elif mod.PLATFORM in ("lever", "ashby"):
                        # No company name field exposed; for auto slugs, require the
                        # slug to be a strong match (cn in sn or sn == cn or covers 80%).
                        # Curated extra_slugs bypass strict check (we know them to be right).
                        if not is_curated:
                            ok = cn in sn or (sn in cn and len(sn) >= 0.8 * len(cn))
                            if not ok:
                                continue
                    return mod.PLATFORM, slug
                except Exception:
                    continue
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    existing = set()
    rows, fieldnames = [], None
    if config.WATCHLIST_RESOLVED.exists():
        with config.WATCHLIST_RESOLVED.open(newline="", encoding="utf-8") as f:
            rd = csv.DictReader(f); fieldnames = rd.fieldnames
            for r in rd:
                rows.append(r); existing.add(_norm(r["Company"]))
    fieldnames = fieldnames or ["Company", "Category", "What they do", "Careers / Jobs URL",
                                "ATS Platform", "ATS Token", "Relevant Team(s)", "Notes",
                                "Jobs API / Feed URL (auto)"]

    todo = [(name, desc, slugs) for name, desc, slugs in FINTECH_COMPANIES
            if _norm(name) not in existing]
    print(f"{len(todo)} fintech companies to resolve ({len(FINTECH_COMPANIES)-len(todo)} already in watchlist)…")

    resolved, unresolved = [], []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(resolve, name, slugs): (name, desc) for name, desc, slugs in todo}
        for fut, (name, desc) in futures.items():
            res = fut.result()
            if res:
                resolved.append((name, desc, res[0], res[1]))
                print(f"  ✓ {name:30s} {res[0]}:{res[1]}")
            else:
                unresolved.append(name)
                print(f"  ✗ {name:30s} unresolved")

    print(f"\nResolved {len(resolved)}/{len(todo)}")
    if unresolved:
        print("Unresolved:", ", ".join(unresolved))

    if args.dry_run or not resolved:
        return

    new_rows = [{**{k: "" for k in fieldnames},
                 "Company": name, "Category": CATEGORY,
                 "What they do": desc,
                 "ATS Platform": plat, "ATS Token": tok,
                 "Notes": "fintech resolver"} for name, desc, plat, tok in resolved]
    with config.WATCHLIST_RESOLVED.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows(rows + new_rows)
    print(f"Appended {len(new_rows)} companies → {config.WATCHLIST_RESOLVED.name}")


if __name__ == "__main__":
    main()
