#!/usr/bin/env python3
"""Offline static QA helper for the advisor (Car Advisor) frontend contract.

This script is intentionally OFFLINE-ONLY. It does not import the Flask app,
does not touch the network, does not call any external model/API, and needs no
API keys. It performs simple text checks against the static frontend assets so
a redesign run cannot silently drop a required flow function, a required result
field, or the safe-rendering helpers.

Run:

    python my-flask-app/scripts/dev/check_advisor_frontend_contract.py

Exit code 0 = all checks pass, 1 = at least one check failed.
"""

from __future__ import annotations

import sys
from pathlib import Path

# my-flask-app/  (two levels up from scripts/dev/)
APP_ROOT = Path(__file__).resolve().parents[2]
RECOMMENDATIONS_JS = APP_ROOT / "static" / "recommendations.js"
THEME_CSS = APP_ROOT / "static" / "theme.css"
RECOMMENDATIONS_HTML = APP_ROOT / "templates" / "recommendations.html"

# Required frontend flow functions (state machine + compare/details flow).
REQUIRED_FUNCTIONS = [
    "setAdvisorStep",
    "renderPreferenceSummary",
    "showPreferenceSummary",
    "showAnalyzingScreen",
    "showResultsScreen",
    "resetAdvisorFlow",
    "renderCompareView",
    "openCompareView",
    "renderDetailsView",
    "openDetailsView",
    "renderResults",
    "renderCarCard",
    "computeCostBreakdown",
    "renderRiskNotes",
    "escapeHtml",
]

# Required result field names that must still be referenced somewhere in the JS
# so they are rendered in a card / details / compare / methodology section.
REQUIRED_FIELDS = [
    "brand",
    "model",
    "year",
    "trim",
    "fuel",
    "gear",
    "engine_cc",
    "turbo",
    "price_range_nis",
    "avg_fuel_consumption",
    "annual_fee",
    "reliability_score",
    "reliability_index",
    "reliability",
    "maintenance_cost",
    "safety_rating",
    "insurance_cost",
    "resale_value",
    "performance_score",
    "comfort_features",
    "suitability",
    "market_supply",
    "annual_energy_cost",
    "annual_fuel_cost",
    "total_annual_cost",
    "fuel_method",
    "fee_method",
    "reliability_method",
    "maintenance_method",
    "safety_method",
    "insurance_method",
    "resale_method",
    "performance_method",
    "comfort_method",
    "suitability_method",
    "supply_method",
    "comparison_comment",
    "not_recommended_reason",
]

# Missing-data / confidence sentinels that must remain in the UI so the output
# never looks more certain than the data.
REQUIRED_SENTINELS = [
    "לא זמין",          # missing value
    "דורש בדיקה",       # unknown / needs check
    "חלקי",             # partial calculation
    "פחת / שונות משוערת",  # residual labelled as estimated, not certain depreciation
]

# New, small, safe analytics events that must keep firing if present.
REQUIRED_ANALYTICS = [
    "advisor_summary_viewed",
    "advisor_analysis_started",
    "advisor_compare_opened",
    "advisor_details_opened",
    "result_requested",
    "result_rendered",
    "result_opened",
]

# Accidental temporary instruction-file name patterns that must NOT be committed
# inside the app tree. (The real prototype reference files and the parity spec
# are NOT in this list — they are legitimate repo references.)
FORBIDDEN_TEMP_PATTERNS = [
    "scratch_proto",
    "TEMP_RUN3",
    "RUN3_SCRATCH",
    ".tmp.instructions",
]


def _fail(failures: list[str], message: str) -> None:
    failures.append(message)


def main() -> int:
    failures: list[str] = []

    if not RECOMMENDATIONS_JS.exists():
        print(f"FATAL: cannot find {RECOMMENDATIONS_JS}")
        return 1
    js = RECOMMENDATIONS_JS.read_text(encoding="utf-8")
    css = THEME_CSS.read_text(encoding="utf-8") if THEME_CSS.exists() else ""
    html = RECOMMENDATIONS_HTML.read_text(encoding="utf-8") if RECOMMENDATIONS_HTML.exists() else ""

    # 1. Required flow functions exist.
    for fn in REQUIRED_FUNCTIONS:
        if f"function {fn}" not in js and f"{fn} =" not in js and f"{fn}(" not in js:
            _fail(failures, f"missing required function: {fn}")

    # 2. Required result fields still referenced.
    for field in REQUIRED_FIELDS:
        if field not in js:
            _fail(failures, f"missing required result field reference: {field}")

    # 3. Missing-data / confidence sentinels preserved.
    for sentinel in REQUIRED_SENTINELS:
        if sentinel not in js:
            _fail(failures, f"missing required missing-data/confidence sentinel: {sentinel}")

    # 4. Analytics events preserved.
    for event in REQUIRED_ANALYTICS:
        if event not in js:
            _fail(failures, f"missing required analytics event: {event}")

    # 5. Safe rendering: no inline on*= event handlers in generated JS template
    #    strings (blocked by the enforced nonce CSP). escapeHtml must exist.
    if "onclick=" in js or "onerror=" in js or "onload=" in js:
        _fail(failures, "found inline on*= handler in recommendations.js (blocked by CSP nonce)")

    # 6. Premium empty state present (no raw error-dump fallback).
    if "yr-empty-state" not in js or "yr-empty-state" not in css:
        _fail(failures, "premium empty state (.yr-empty-state) missing in JS or CSS")

    # 7. RTL preserved in the template.
    if html and 'dir="rtl"' not in html:
        _fail(failures, 'recommendations.html lost dir="rtl" (RTL must be preserved)')

    # 8. No accidental temporary instruction files inside the app tree.
    for pattern in FORBIDDEN_TEMP_PATTERNS:
        for hit in APP_ROOT.rglob(f"*{pattern}*"):
            _fail(failures, f"accidental temporary file present: {hit.relative_to(APP_ROOT.parent)}")

    if failures:
        print("ADVISOR FRONTEND CONTRACT: FAIL")
        for message in failures:
            print(f"  - {message}")
        return 1

    print("ADVISOR FRONTEND CONTRACT: OK")
    print(f"  functions checked : {len(REQUIRED_FUNCTIONS)}")
    print(f"  fields checked    : {len(REQUIRED_FIELDS)}")
    print(f"  sentinels checked : {len(REQUIRED_SENTINELS)}")
    print(f"  analytics checked : {len(REQUIRED_ANALYTICS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
