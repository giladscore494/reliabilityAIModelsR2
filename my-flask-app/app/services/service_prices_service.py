# -*- coding: utf-8 -*-
"""Service Price Check service logic."""

import json
import os
import re
import unicodedata
from urllib.parse import urlparse
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app

from app.extensions import db
import app.extensions as extensions
from app.models import ServiceInvoice, ServiceInvoiceItem, User
from app.legal import GEMINI_VISION_MODEL_ID

# Pricing mode configuration
SERVICE_PRICES_MODE = os.environ.get("SERVICE_PRICES_MODE", "warmup_web_first")
MIN_INTERNAL_SAMPLES = int(os.environ.get("MIN_INTERNAL_SAMPLES", "20"))
MIN_WEB_SAMPLES = int(os.environ.get("MIN_WEB_SAMPLES", "10"))
MIN_MARKET_SAMPLES = int(os.environ.get("MIN_MARKET_SAMPLES", "3"))
MIN_HIGH_CONFIDENCE_SAMPLES = int(os.environ.get("MIN_HIGH_CONFIDENCE_SAMPLES", "10"))
MIN_GROUNDED_ITEMS = int(os.environ.get("MIN_GROUNDED_ITEMS", "2"))
WEB_GROUNDING_MAX_ITEMS = int(os.environ.get("WEB_GROUNDING_MAX_ITEMS", "5"))
WEB_GROUNDING_MAX_SOURCES = int(os.environ.get("WEB_GROUNDING_MAX_SOURCES", "5"))
WEB_GROUNDING_TIMEOUT_SEC = int(os.environ.get("WEB_GROUNDING_TIMEOUT_SEC", "25"))

# Canonical codes mapping - Hebrew and English keywords
CANONICAL_MAPPINGS = {
    "oil_change": {
        "keywords": ["שמן", "oil", "שמן מנוע", "engine oil", "החלפת שמן", "oil change"],
        "category": "engine",
        "is_labor": False,
    },
    "filters": {
        "keywords": ["פילטר", "filter", "מסנן", "פילטר אוויר", "air filter", "פילטר שמן", "oil filter"],
        "category": "engine",
        "is_labor": False,
    },
    "brake_pads_front": {
        "keywords": ["רפידות קדמיות", "רפידות בלם קדמי", "front brake pads", "רפידות קידמי"],
        "category": "brakes",
        "is_labor": False,
    },
    "brake_discs_front": {
        "keywords": ["דיסקים קדמיים", "דיסקי בלם קדמי", "front brake discs", "front rotors"],
        "category": "brakes",
        "is_labor": False,
    },
    "brake_pads_rear": {
        "keywords": ["רפידות אחוריות", "רפידות בלם אחורי", "rear brake pads", "רפידות אחורי"],
        "category": "brakes",
        "is_labor": False,
    },
    "battery": {
        "keywords": ["מצבר", "battery", "סוללה", "אקומולטור"],
        "category": "electrical",
        "is_labor": False,
    },
    "tires": {
        "keywords": ["צמיגים", "tires", "צמיג", "tire", "גלגלים"],
        "category": "tires",
        "is_labor": False,
    },
    "ac_gas": {
        "keywords": ["מילוי גז", "ac gas", "גז מזגן", "freon", "פריאון", "r134a", "מזגן"],
        "category": "ac",
        "is_labor": False,
    },
    "spark_plugs": {
        "keywords": ["מצתים", "spark plugs", "מצת", "spark plug"],
        "category": "engine",
        "is_labor": False,
    },
    "timing_belt": {
        "keywords": ["רצועת תזמון", "timing belt", "חגורת תזמון", "קמשפט"],
        "category": "engine",
        "is_labor": False,
    },
    "clutch": {
        "keywords": ["מצמד", "clutch", "קלאץ"],
        "category": "transmission",
        "is_labor": False,
    },
    "alternator": {
        "keywords": ["אלטרנטור", "alternator", "גנרטור", "דינמו"],
        "category": "electrical",
        "is_labor": False,
    },
    "starter": {
        "keywords": ["סטרטר", "starter", "מתנע"],
        "category": "electrical",
        "is_labor": False,
    },
    "suspension_arm": {
        "keywords": ["זרוע מתלה", "control arm", "זרוע", "suspension arm"],
        "category": "suspension",
        "is_labor": False,
    },
    "shock_absorber": {
        "keywords": ["בולם זעזועים", "shock absorber", "בולם", "shocks", "אמורטיזטור"],
        "category": "suspension",
        "is_labor": False,
    },
    "wheel_alignment": {
        "keywords": ["כיוון גלגלים", "wheel alignment", "alignment", "פרונט"],
        "category": "tires",
        "is_labor": True,
    },
    "diagnostic_scan": {
        "keywords": ["בדיקת מחשב", "diagnostic", "סריקה", "קודים", "scan", "אבחון"],
        "category": "diagnostic",
        "is_labor": True,
    },
    "transmission_fluid": {
        "keywords": ["שמן גיר", "transmission fluid", "גיר אוטומטי", "atf", "שמן גירת"],
        "category": "transmission",
        "is_labor": False,
    },
    "coolant": {
        "keywords": ["נוזל קירור", "coolant", "אנטיפריז", "antifreeze", "מי קירור"],
        "category": "engine",
        "is_labor": False,
    },
    "wipers": {
        "keywords": ["מגבים", "wipers", "מגב", "wiper blades"],
        "category": "other",
        "is_labor": False,
    },
    "labor": {
        "keywords": ["עבודה", "labor", "שעה", "התקנה", "הרכבה", "פירוק", "שירות"],
        "category": "labor",
        "is_labor": True,
    },
}

# Labor detection keywords
LABOR_KEYWORDS = ["עבודה", "labor", "שעה", "התקנה", "הרכבה", "פירוק", "שירות", "installation", "work"]


def normalize_text(text: str) -> str:
    """
    Normalize text for matching: lowercase, strip, remove punctuation,
    unify Hebrew final letters.
    """
    if not text:
        return ""
    # Lowercase
    text = text.lower().strip()
    # Remove punctuation
    text = re.sub(r'[^\w\sא-ת]', '', text)
    # Unify Hebrew final letters
    finals = {'ך': 'כ', 'ם': 'מ', 'ן': 'נ', 'ף': 'פ', 'ץ': 'צ'}
    for final, normal in finals.items():
        text = text.replace(final, normal)
    return text


def parse_price(price_str: Any) -> Optional[int]:
    """
    Parse a price string into an integer (ILS).
    Handles ₪, commas, decimals, etc.
    """
    if price_str is None:
        return None
    if isinstance(price_str, (int, float)):
        return int(round(price_str))
    
    price_str = str(price_str)
    # Remove currency symbols and whitespace
    price_str = re.sub(r'[₪$€\s]', '', price_str)
    # Remove commas
    price_str = price_str.replace(',', '')
    
    try:
        return int(round(float(price_str)))
    except (ValueError, TypeError):
        return None


def parse_qty(qty: Any) -> int:
    """
    Parse a quantity into a safe integer.
    Defaults to 1 when missing or unparseable.
    """
    if qty is None:
        return 1
    if isinstance(qty, bool):
        return 1
    if isinstance(qty, (int, float)):
        try:
            return max(1, int(round(qty)))
        except (TypeError, ValueError):
            return 1
    if isinstance(qty, str):
        qty_clean = qty.replace(",", "")
        match = re.search(r"(\d+(?:\.\d+)?)", qty_clean)
        if not match:
            return 1
        try:
            value = float(match.group(1))
            return max(1, int(round(value)))
        except (TypeError, ValueError):
            return 1
    return 1


def is_labor_line(description: str) -> bool:
    """Check if a line item is labor (not parts)."""
    normalized = normalize_text(description)
    return any(keyword in normalized for keyword in LABOR_KEYWORDS)


def match_canonical_code(description: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Match a line item description to a canonical code.
    Returns (canonical_code, category) or (None, None) if no match.
    """
    normalized = normalize_text(description)
    
    # Check each canonical code's keywords
    for code, config in CANONICAL_MAPPINGS.items():
        for keyword in config["keywords"]:
            if normalize_text(keyword) in normalized:
                return code, config["category"]
    
    # Default to generic "other" if labor detected
    if is_labor_line(description):
        return "labor", "labor"
    
    return None, None


def deterministic_sanitize_no_pii(obj: Any) -> Any:
    """
    Walk JSON recursively and redact any leftover PII patterns.
    Second line of defense after model redaction.
    """
    if obj is None:
        return None
    
    if isinstance(obj, str):
        # Phone patterns (Israeli)
        obj = re.sub(r'0\d{1,2}[-\s]?\d{3}[-\s]?\d{4}', '[REDACTED]', obj)
        obj = re.sub(r'\+972[-\s]?\d{1,2}[-\s]?\d{3}[-\s]?\d{4}', '[REDACTED]', obj)
        # Email patterns
        obj = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '[REDACTED]', obj)
        # Israeli license plate patterns
        obj = re.sub(r'\d{2,3}[-\s]?\d{2,3}[-\s]?\d{2,3}', '[REDACTED]', obj)
        # Invoice serial number patterns (common formats)
        obj = re.sub(r'(?:חשבונית|invoice)[\s:#]*\d{4,}', '[REDACTED]', obj, flags=re.IGNORECASE)
        return obj
    
    if isinstance(obj, dict):
        return {k: deterministic_sanitize_no_pii(v) for k, v in obj.items()}
    
    if isinstance(obj, list):
        return [deterministic_sanitize_no_pii(item) for item in obj]
    
    return obj


def _is_israel_context(text: Optional[str]) -> bool:
    if not text:
        return False
    lowered = text.lower()
    if "ישראל" in text or "israel" in lowered:
        return True
    if "₪" in text or "ש\"ח" in text or "שח" in text:
        return True
    if "ils" in lowered or "nis" in lowered:
        return True
    return False


def _is_il_domain(url_text: str) -> bool:
    if not url_text:
        return False
    try:
        parsed = urlparse(url_text)
        host = (parsed.hostname or "").lower()
    except Exception:
        return False
    return host.endswith(".il") or host.endswith(".co.il") or host.endswith(".gov.il")


def filter_and_deduplicate_israeli_sources(sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned_sources = []
    seen_urls = set()
    for source in sources:
        if not isinstance(source, dict):
            continue
        url = source.get("url")
        title = source.get("title")
        if not url and not title:
            continue
        url_text = str(url) if url else ""
        title_text = str(title) if title else ""
        is_il_domain = _is_il_domain(url_text)
        if not (is_il_domain or _is_israel_context(url_text) or _is_israel_context(title_text)):
            continue
        if url_text and url_text in seen_urls:
            continue
        if url_text:
            seen_urls.add(url_text)
        cleaned_sources.append({"url": url, "title": title})
    return cleaned_sources


def _extract_range_samples(range_data: Any) -> List[int]:
    if not isinstance(range_data, dict):
        return []
    min_val = range_data.get("min")
    max_val = range_data.get("max")
    median_val = range_data.get("median")
    if median_val is None and isinstance(min_val, (int, float)) and isinstance(max_val, (int, float)):
        median_val = (min_val + max_val) / 2
    values = []
    for value in (min_val, median_val, max_val):
        if isinstance(value, (int, float)) and value > 0:
            values.append(int(round(value)))
    return values


def extract_benchmark_samples(benchmark: Dict[str, Any]) -> List[int]:
    samples = benchmark.get("market_samples_ils") or benchmark.get("samples_ils") or []
    valid_samples = [int(round(s)) for s in samples if isinstance(s, (int, float)) and s > 0]
    if len(valid_samples) < MIN_MARKET_SAMPLES:
        range_samples = _extract_range_samples(benchmark.get("price_range_ils"))
        for value in range_samples:
            if value not in valid_samples:
                valid_samples.append(value)
    return valid_samples


def benchmark_has_usable_content(benchmark: Dict[str, Any]) -> bool:
    sources_raw = benchmark.get("sources") or []
    cleaned_sources = filter_and_deduplicate_israeli_sources(sources_raw if isinstance(sources_raw, list) else [])
    if not cleaned_sources:
        return False
    samples = extract_benchmark_samples(benchmark)
    return len(samples) >= MIN_MARKET_SAMPLES


def extract_grounding_metadata(candidate: Any) -> Tuple[Optional[Any], Optional[Any]]:
    grounding_meta = getattr(candidate, "grounding_metadata", None) or getattr(
        candidate, "groundingMetadata", None
    )
    if not grounding_meta:
        return None, None
    if isinstance(grounding_meta, dict):
        grounding_chunks = grounding_meta.get("grounding_chunks") or grounding_meta.get("groundingChunks")
        citations = grounding_meta.get("citations")
    else:
        grounding_chunks = getattr(grounding_meta, "grounding_chunks", None) or getattr(
            grounding_meta, "groundingChunks", None
        )
        citations = getattr(grounding_meta, "citations", None)
    return grounding_chunks, citations


def canonicalize_line_items(line_items: List[Dict]) -> List[Dict]:
    """
    Convert raw line items to canonical items with normalized data.
    Groups by canonical_code and sums prices.
    """
    if not line_items:
        return []
    
    grouped: Dict[str, Dict] = {}
    
    for item in line_items:
        description = item.get("description", "") or ""
        price = parse_price(item.get("price_ils") or item.get("invoice_price_ils") or item.get("price"))
        qty = parse_qty(item.get("qty"))
        
        canonical_code, category = match_canonical_code(description)
        if not canonical_code:
            # Skip items we can't categorize
            canonical_code = "other"
            category = "other"
        
        is_labor = is_labor_line(description)
        
        if canonical_code not in grouped:
            grouped[canonical_code] = {
                "canonical_code": canonical_code,
                "category": category,
                "raw_description": description,
                "price_ils": 0,
                "labor_ils": 0,
                "parts_ils": 0,
                "qty": 0,
                "confidence": 0.7,  # Default confidence
            }
        
        entry = grouped[canonical_code]
        existing_qty = entry.get("qty")
        if isinstance(existing_qty, (int, float)) and not isinstance(existing_qty, bool):
            entry["qty"] = int(existing_qty)
        elif isinstance(existing_qty, str):
            try:
                entry["qty"] = int(round(float(existing_qty)))
            except (TypeError, ValueError):
                entry["qty"] = 0
        else:
            entry["qty"] = 0
        entry["qty"] += qty
        
        if price:
            entry["price_ils"] += price
            if is_labor:
                entry["labor_ils"] += price
            else:
                entry["parts_ils"] += price
        
        # Concat descriptions if multiple
        if description and description not in entry["raw_description"]:
            entry["raw_description"] = f"{entry['raw_description']}; {description}".strip("; ")
    
    return list(grouped.values())


def compute_percentiles(prices: List[int]) -> Dict[str, Optional[int]]:
    """
    Compute p50, p75, p90 percentiles from a list of prices.
    Uses percentile_cont style (linear interpolation).
    """
    if not prices:
        return {"p50": None, "p75": None, "p90": None}
    
    sorted_prices = sorted(prices)
    n = len(sorted_prices)
    
    def percentile_cont(p: float) -> int:
        """Linear interpolation percentile."""
        if n == 1:
            return sorted_prices[0]
        pos = p * (n - 1)
        lower = int(pos)
        upper = min(lower + 1, n - 1)
        frac = pos - lower
        return int(round(sorted_prices[lower] + frac * (sorted_prices[upper] - sorted_prices[lower])))
    
    return {
        "p50": percentile_cont(0.5),
        "p75": percentile_cont(0.75),
        "p90": percentile_cont(0.9),
    }


def percentile_rank(prices: List[int], value: int) -> float:
    """
    Compute percentile rank of a value within a list of prices.
    Returns fraction (0-1) of values <= the given value.
    """
    if not prices or value is None:
        return 0.5
    
    count_le = sum(1 for p in prices if p <= value)
    return count_le / len(prices)


def compute_market_range(
    samples: List[int],
) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[str], Optional[str]]:
    """
    Compute market range (min/max/median) from grounded samples.
    Returns (min, max, median, confidence, label).
    """
    if len(samples) < MIN_MARKET_SAMPLES:
        return None, None, None, None, "אין מספיק נתונים להשוואה"
    sorted_samples = sorted(samples)
    n = len(sorted_samples)
    if n == 1:
        median = sorted_samples[0]
    else:
        pos = 0.5 * (n - 1)
        lower = int(pos)
        upper = min(lower + 1, n - 1)
        frac = pos - lower
        median = int(round(sorted_samples[lower] + frac * (sorted_samples[upper] - sorted_samples[lower])))
    confidence = "high" if len(samples) >= MIN_HIGH_CONFIDENCE_SAMPLES else "low"
    label = "השוואה מבוססת" if confidence == "high" else "השוואה חלקית (מעט דגימות)"
    return sorted_samples[0], sorted_samples[-1], median, confidence, label


def compute_item_verdict(
    invoice_price: Optional[int],
    market_min: Optional[int],
    market_max: Optional[int],
) -> str:
    """
    Classify invoice price vs. market range deterministically.
    """
    if invoice_price is None or market_min is None or market_max is None:
        return "אין מספיק נתונים להשוואה"
    low_threshold = market_min * 0.9
    high_threshold = market_max * 1.1
    if invoice_price < low_threshold:
        return "נמוך מהשוק"
    if invoice_price <= high_threshold:
        return "תואם שוק"
    return "גבוה מהשוק"


def classify_market_verdict(
    invoice_price: Optional[int],
    market_min: Optional[int],
    market_max: Optional[int],
) -> str:
    """Backward-compatible wrapper for compute_item_verdict (deprecated)."""
    return compute_item_verdict(invoice_price, market_min, market_max)


def compute_price_deviation(
    invoice_price: Optional[int],
    market_min: Optional[int],
    market_max: Optional[int],
) -> Optional[float]:
    """
    Compute deviation from market range for weighting fairness score.
    """
    if invoice_price is None or market_min is None or market_max is None:
        return None
    low_threshold = market_min * 0.9
    high_threshold = market_max * 1.1
    if invoice_price < low_threshold:
        return (low_threshold - invoice_price) / low_threshold
    if invoice_price > high_threshold:
        return (invoice_price - high_threshold) / high_threshold
    return 0.0


def compute_overall_score(items: List[Dict[str, Any]]) -> Tuple[Optional[int], Optional[str]]:
    """
    Compute overall fairness score deterministically.
    """
    total_weighted_deviation = 0.0
    total_weight = 0.0
    grounded_items = 0

    for item in items:
        price = item.get("price_ils")
        market_min = item.get("market_min_ils")
        market_max = item.get("market_max_ils")
        qty = item.get("qty") or 1
        deviation = compute_price_deviation(price if price else None, market_min, market_max)
        if deviation is None or not price:
            continue
        grounded_items += 1
        weight = price * max(qty, 1)
        if weight > 0:
            total_weight += weight
            total_weighted_deviation += deviation * weight

    if grounded_items >= MIN_GROUNDED_ITEMS and total_weight > 0:
        avg_deviation = total_weighted_deviation / total_weight
        fairness_score = max(0, int(round(100 - min(1, avg_deviation) * 100)))
        return fairness_score, None

    return None, "אין מספיק נתונים לציון כולל"


def build_invoice_report_narrative(report: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a deterministic narrative for the invoice report.
    """
    def format_ils(value: Optional[int]) -> str:
        return f"₪{value:,.0f}" if value is not None else "-"

    totals = report.get("totals", {})
    items = report.get("items", [])
    items_sorted = sorted(items, key=lambda x: x.get("price_ils") or 0, reverse=True)

    total_price = totals.get("total_price_ils")
    labor_ils = totals.get("labor_ils")
    parts_ils = totals.get("parts_ils")

    summary = (
        f"בדקנו {len(items)} פריטים בחשבונית. "
        f"סה\"כ החשבונית {format_ils(total_price)}, "
        f"מתוכם עבודה {format_ils(labor_ils)} וחלקים {format_ils(parts_ils)}."
    )

    item_lines = []
    missing_items = 0
    for item in items_sorted:
        desc = item.get("raw_description") or item.get("canonical_code") or "פריט"
        invoice_price = item.get("price_ils")
        market_min = item.get("market_min_ils")
        market_max = item.get("market_max_ils")
        verdict = item.get("verdict") or item.get("label") or "אין מספיק נתונים להשוואה"
        if market_min is not None and market_max is not None:
            range_text = f"טווח שוק {format_ils(market_min)}–{format_ils(market_max)}"
        else:
            range_text = "אין מספיק נתוני שוק ישראליים"
            missing_items += 1
        item_lines.append(
            f"{desc}: מחיר חשבונית {format_ils(invoice_price)} מול {range_text} → {verdict}"
        )

    methodology = [
        "מחירי השוק נאספו ממקורות ישראליים ברשת עם קישורים.",
        "לא ניחשנו מחירים. כשאין מספיק מקורות מוצג שאין מספיק נתונים.",
        "החישוב בוצע לפי חוקים קבועים בקוד.",
    ]
    if missing_items:
        methodology.append("חלק מהפריטים ללא השוואה בגלל מחסור במקורות ישראליים.")
        methodology.append("הוספת מקורות מחיר בישראל תשפר את ההשוואה.")
    if report.get("fairness_score") is None:
        methodology.append("אין מספיק נתונים לציון כולל.")

    return {
        "summary": summary,
        "items": item_lines,
        "methodology": methodology,
    }


def cohort_price_samples(
    canonical_code: str,
    make: Optional[str] = None,
    model: Optional[str] = None,
    year: Optional[int] = None,
    mileage: Optional[int] = None,
    region: Optional[str] = None,
    garage_type: Optional[str] = None,
) -> List[int]:
    """
    Query historical price samples for a canonical code.
    Filters by car context if provided.
    """
    from sqlalchemy import and_
    
    query = db.session.query(ServiceInvoiceItem.price_ils).join(ServiceInvoice)
    
    filters = [
        ServiceInvoiceItem.canonical_code == canonical_code,
        ServiceInvoiceItem.price_ils.isnot(None),
        ServiceInvoiceItem.price_ils > 0,
    ]
    
    if make:
        filters.append(ServiceInvoice.make == make)
    if model:
        filters.append(ServiceInvoice.model == model)
    if year:
        # Allow +/- 2 years
        filters.append(ServiceInvoice.year.between(year - 2, year + 2))
    if mileage:
        # Bucket by 50k
        bucket_low = (mileage // 50000) * 50000
        bucket_high = bucket_low + 100000  # Include neighbor bucket
        filters.append(ServiceInvoice.mileage.between(bucket_low, bucket_high))
    if region:
        filters.append(ServiceInvoice.region == region)
    if garage_type:
        filters.append(ServiceInvoice.garage_type == garage_type)
    
    query = query.filter(and_(*filters))
    results = query.limit(1000).all()
    
    return [r[0] for r in results if r[0] is not None]


def fallback_ranges(canonical_code: str) -> Dict[str, Any]:
    """
    Get fallback price ranges from static data file.
    """
    try:
        data_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "data",
            "cost_ranges_il.json"
        )
        with open(data_path, "r", encoding="utf-8") as f:
            ranges = json.load(f)
        
        if canonical_code in ranges:
            min_price, max_price = ranges[canonical_code]
            return {"min": min_price, "max": max_price, "source": "fallback"}
    except Exception:
        pass
    
    return {"min": None, "max": None, "source": "unknown"}


def build_report(
    ctx: Dict[str, Any],
    canonical_items: List[Dict],
    total_price: Optional[int],
    samples_meta: Dict[str, Any],
    web_samples_map: Optional[Dict[str, Dict[str, Any]]] = None,
    grounding_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build the final report with grounded ranges, labels, and analysis.
    """
    now = datetime.utcnow()
    
    per_item = []
    red_flags = []
    sum_items = 0
    total_labor = 0
    total_parts = 0
    discount_target = 0
    sources_seen = set()
    grounding_sources = []
    if grounding_status is None:
        grounding_status = {"verified": False, "reason": "missing_grounding_metadata"}
    if "verified" not in grounding_status:
        grounding_status["verified"] = False
    grounding_verified = grounding_status.get("verified", False)
    grounding_reason = grounding_status.get("reason")
    content_verified = grounding_reason == "verified_by_content"
    
    for item in canonical_items:
        code = item["canonical_code"]
        price = item.get("price_ils") or 0
        qty = item.get("qty") or 1
        sum_items += price
        total_labor += item.get("labor_ils") or 0
        total_parts += item.get("parts_ils") or 0
 
        web_entry = (web_samples_map or {}).get(code, {})
        samples = []
        for sample in web_entry.get("samples") or []:
            if isinstance(sample, (int, float)) and sample > 0:
                samples.append(int(round(sample)))
        sources_raw = web_entry.get("sources") or []
        notes = web_entry.get("notes") or []
        cleaned_sources = filter_and_deduplicate_israeli_sources(sources_raw)
        for source in cleaned_sources:
            url = source.get("url")
            title = source.get("title")
            if url and url not in sources_seen:
                sources_seen.add(url)
                grounding_sources.append({"url": url, "title": title})
 
        market_min = market_max = market_median = None
        market_confidence = None
        market_label = None
        market_note = None
        if not cleaned_sources:
            market_note = "אין מספיק נתונים להשוואה"
            samples = []
        else:
            market_min, market_max, market_median, market_confidence, market_label = compute_market_range(samples)
            if market_min is None:
                market_note = "אין מספיק נתונים להשוואה"
            elif content_verified and not market_note:
                market_note = "נאסף מהרשת ללא אימות אוטומטי"
        verdict = compute_item_verdict(price if price else None, market_min, market_max)
 
        if verdict == "גבוה מהשוק":
            red_flags.append(f"{code}: מחיר גבוה מהשוק")
 
        overpay = 0
        if market_max is not None and price:
            overpay = max(0, int(round(price - market_max * 1.1)))
            if overpay > 0:
                discount_target += overpay

        per_item.append({
            "canonical_code": code,
            "raw_description": item.get("raw_description"),
            "price_ils": price,
            "cohort_n": len(samples),
            "market_median_ils": market_median,
            "market_confidence": market_confidence,
            "market_label": market_label,
            "label": verdict,
            "verdict": verdict,
            "overpay_estimate_ils": overpay,
            "source": "unverified" if not grounding_verified else ("web_grounding" if cleaned_sources else "no_grounding"),
            "market_min_ils": market_min,
            "market_max_ils": market_max,
            "market_note": market_note,
            "market_verification": "content_only" if content_verified and cleaned_sources else None,
            "market_samples_n": len(samples),
            "market_sources": cleaned_sources,
            "market_notes": notes,
            "qty": qty,
        })
    
    # Calculate labor share
    labor_share = total_labor / sum_items if sum_items > 0 else 0
    if labor_share > 0.55:
        red_flags.append("חלק העבודה גבוה מ-55%")
    
    # Check discrepancy between total and sum of items
    discrepancy_pct = 0
    if total_price and sum_items:
        discrepancy_pct = abs(total_price - sum_items) / total_price * 100
        if discrepancy_pct > 12:
            red_flags.append(f"פער של {discrepancy_pct:.1f}% בין הסכום הכולל לסכום הפריטים")
    
    # Fairness score
    fairness_score, fairness_note = compute_overall_score(per_item)
    
    # Build negotiation script
    negotiation_lines = []
    if discount_target > 0:
        negotiation_lines.append(f"לפי נתוני השוק, ניתן לנסות להוריד כ-₪{discount_target:,} מהמחיר הכולל")
        for item in per_item:
            if item.get("overpay_estimate_ils", 0) > 0:
                negotiation_lines.append(
                    f"  - {item['canonical_code']}: מחיר גבוה ב-₪{item['overpay_estimate_ils']:,} מעל גבול השוק"
                )
    
    report = {
        "meta": {
            "car": {
                "make": ctx.get("make"),
                "model": ctx.get("model"),
                "year": ctx.get("year"),
                "mileage": ctx.get("mileage"),
            },
            "invoice_date": ctx.get("invoice_date"),
            "region": ctx.get("region"),
            "garage_type": ctx.get("garage_type"),
            "created_at": now.isoformat(),
        },
        "totals": {
            "total_price_ils": total_price,
            "sum_items_ils": sum_items,
            "discrepancy_pct": round(discrepancy_pct, 1),
            "labor_share": round(labor_share, 2),
            "labor_ils": total_labor,
            "parts_ils": total_parts,
        },
        "items": per_item,
        "red_flags": red_flags,
        "fairness_score": fairness_score,
        "fairness_note": fairness_note,
        "negotiation_script": negotiation_lines,
        "grounding_sources": grounding_sources,
        "grounding_status": grounding_status,
        "disclaimer": "מידע כללי, לא אבחון/התחייבות מחיר",
    }

    report["narrative"] = build_invoice_report_narrative(report)
    return report


def validate_vision_payload(result: Dict[str, Any]) -> None:
    """
    Validate vision payload types and required shapes for deterministic processing.
    """
    if not isinstance(result, dict):
        raise ValueError("Vision payload must be a JSON object.")

    extracted = result.get("extracted", result)
    if not isinstance(extracted, dict):
        raise ValueError("Vision payload 'extracted' must be an object.")

    line_items = extracted.get("line_items") or []
    if not isinstance(line_items, list):
        raise ValueError("Vision payload 'line_items' must be a list.")
    for item in line_items:
        if not isinstance(item, dict):
            raise ValueError("Each line item must be an object.")
        qty = item.get("qty")
        if qty is not None:
            if isinstance(qty, bool):
                raise ValueError("Line item qty must be a numeric value (not boolean).")
            if not isinstance(qty, (int, float)):
                raise ValueError("Line item qty must be a numeric value (not boolean).")
        invoice_price = item.get("invoice_price_ils", item.get("price_ils"))
        if invoice_price is not None:
            if isinstance(invoice_price, bool):
                raise ValueError("Line item invoice_price_ils must be a numeric value (not boolean).")
            if not isinstance(invoice_price, (int, float)):
                raise ValueError("Line item invoice_price_ils must be a numeric value (not boolean).")

    benchmarks = result.get("benchmarks_web", [])
    if not isinstance(benchmarks, list):
        raise ValueError("benchmarks_web must be a list.")
    for benchmark in benchmarks:
        if not isinstance(benchmark, dict):
            raise ValueError("Each benchmark entry must be an object.")
        samples = benchmark.get("market_samples_ils", benchmark.get("samples_ils", []))
        if samples is None:
            samples = []
        if not isinstance(samples, list):
            raise ValueError("market_samples_ils must be a list.")
        for sample in samples:
            if isinstance(sample, bool) or not isinstance(sample, (int, float)):
                raise ValueError("market_samples_ils must contain numbers only.")
        sources = benchmark.get("sources", [])
        if sources is None:
            sources = []
        if not isinstance(sources, list):
            raise ValueError("sources must be a list.")
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError("Each source must be an object.")
            if "url" not in source or "title" not in source:
                raise ValueError("Each source must include url and title fields.")
            url = source.get("url")
            title = source.get("title")
            if url is not None and not isinstance(url, str):
                raise ValueError("Source url must be a string.")
            if title is not None and not isinstance(title, str):
                raise ValueError("Source title must be a string.")


def vision_extract_invoice(
    image_bytes: bytes,
    mime_type: str,
    request_id: str,
) -> Dict[str, Any]:
    """
    Use Gemini Vision to extract structured data from invoice image.
    Requests redaction of PII in the response.
    DEPRECATED: Use vision_extract_invoice_with_web_benchmarks for new code.
    Kept for backward compatibility.
    """
    import base64
    from google.genai import types as genai_types
    
    ai_client = extensions.ai_client
    if not ai_client:
        raise RuntimeError("AI client not initialized")
    
    # Encode image as base64
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    
    prompt = """
אנא נתח את תמונת החשבונית המצורפת וחלץ את המידע הבא לפורמט JSON מדויק.

חשוב מאוד:
1. החלף כל מידע מזהה אישי (שם, טלפון, מספר חשבונית, שם מוסך, לוחית רישוי) ב-"[REDACTED]"
2. אם שדה לא קיים או לא ניתן לקרוא, השתמש ב-null
3. אל תנחש - אם לא ברור, השתמש ב-null

פורמט הפלט הנדרש (JSON בלבד):
{
  "car": {
    "make": "יצרן הרכב או null",
    "model": "דגם הרכב או null",
    "year": שנה כמספר או null,
    "mileage": קילומטראז' כמספר או null
  },
  "invoice": {
    "date": "תאריך בפורמט YYYY-MM-DD או null",
    "total_price_ils": סכום כולל כמספר או null,
    "region": "אזור גיאוגרפי כללי או null",
    "garage_type": "dealer" או "private" או "unknown"
  },
  "line_items": [
    {
      "description": "תיאור הפריט/שירות",
      "price_ils": מחיר כמספר או null,
      "qty": כמות כמספר או 1
    }
  ],
  "redaction": {
    "applied": true,
    "notes": "פרטי ההשחרה שבוצעה"
  },
  "confidence": {
    "overall": ציון בין 0.0 ל-1.0
  }
}

החזר אך ורק JSON תקין, ללא טקסט נוסף.
"""
    
    try:
        response = ai_client.models.generate_content(
            model=GEMINI_VISION_MODEL_ID,
            contents=[
                genai_types.Part.from_text(prompt),
                genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            ],
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        
        result_text = response.text
        
        # Parse JSON response
        try:
            result = json.loads(result_text)
        except json.JSONDecodeError:
            # Try to extract JSON from response
            json_match = re.search(r'\{[\s\S]*\}', result_text)
            if json_match:
                result = json.loads(json_match.group())
            else:
                raise ValueError("Failed to parse model response as JSON")
        
        return result
        
    except Exception as e:
        current_app.logger.error(f"Vision extraction failed: {e}")
        raise


def vision_extract_invoice_with_web_benchmarks(
    image_bytes: bytes,
    mime_type: str,
    request_id: str,
) -> Dict[str, Any]:
    """
    Use Gemini Vision to extract structured data from invoice image
    AND get web-grounded benchmark samples for Israel market.
    Single model call for both OCR + grounding (warmup mode).
    """
    import base64
    from google.genai import types as genai_types

    ai_client = extensions.ai_client
    if not ai_client:
        raise RuntimeError("AI client not initialized")

    system_instruction = "You are a strict JSON extraction engine. Output must be valid JSON only."

    prompt = (
        "אתה מנתח/ת חשבונית טיפול רכב מישראל (תמונה). המשימה כפולה ובקריטיות גבוהה:\n\n"
        "(1) חילוץ נתונים מהחשבונית (OCR) + השחרה/הסרה של פרטים מזהים (Redaction).\n"
        "(2) בנצ׳מרק מחירי שוק בישראל לכל שורת טיפול ע\"י חיפוש/grounding (מחירים ממקורות ישראליים בלבד).\n\n"
        "כללים מחייבים:\n"
        "- החזר/י *אך ורק* JSON תקני. אין להחזיר טקסט חופשי. אין Markdown. אין הסברים.\n"
        "- אם אין מידע: null / [] / \"unknown\". לא לנחש.\n"
        "- חובה להשתמש בכלי google_search לכל חיפוש בנצ'מרק. לכל שורת טיפול חייב להתבצע חיפוש.\n"
        "- אסור להשתמש בזיכרון/הערכה כללית למחירים. כל מחיר חייב להגיע ממקור ישראלי עם URL.\n"
        "- אין להחזיר ציונים, הערכות או סיכומים; רק נתונים גולמיים (מחירים, דגימות, מקורות).\n"
        "- טיפוסים קשיחים: qty מספר (לא מחרוזת), invoice_price_ils מספר, market_samples_ils מערך מספרים, sources מערך של אובייקטים {url,title}.\n"
        "- כל פרט מזהה שמופיע (שמות אנשים, טלפון, אימייל, כתובת, שם מוסך, מספר חשבונית/קבלה, מספר רישוי, VIN) חייב להיות מוחלף במחרוזת \"[REDACTED]\".\n"
        "- בבנצ׳מרק: החזר/י אך ורק מספרים (market_samples_ils) וטווח מחיר (price_range_ils) וקישורים (sources). אסור להעתיק טקסט מהאתרים. אין ציטוטים.\n"
        "- הבנצ׳מרק חייב להתבסס על *שוק מוסכים ישראלי בלבד*:\n"
        "  - כל שאילתה חייבת לכלול עברית + \"ישראל\" + \"₪\".\n"
        "  - אם משתמשים באנגלית, חובה לציין \"Israel\" + ILS + ₪.\n"
        "  - הימנע/י ממחירים בשווקים זרים.\n"
        "- אם לא נמצאו מקורות ישראליים רלוונטיים: החזר/י price_range_ils=null, confidence נמוך, והסבר/י ב-notes.\n\n"
        "תבניות שאילתות (להשתמש בהן, להתאים לכל שורת טיפול):\n"
        "1) \"מחיר {service_he} {make} {model} {year} ישראל ₪ כולל עבודה וחלקים\"\n"
        "2) \"טווח מחירים {service_he} במוסך בישראל ₪\"\n"
        "3) \"מחיר {service_he} עבודה וחלקים ש\\\"ח מוסך {garage_type_he}\"\n"
        "4) \"עלות {service_he} ש\\\"ח ישראל\"\n"
        "5) (fallback) \"{service_en} price Israel ILS ₪ labor parts\"\n\n"
        "הגדרות:\n"
        "- garage_type_he: \"מורשה\" אם מופיע רמז למורשה/יבואן, אחרת \"פרטי\" אם נראה מוסך כללי, אחרת \"כללי\".\n"
        "- service_he: ניסוח קצר בעברית לשירות (לדוגמה: \"החלפת שמן ופילטר\", \"רפידות בלם קדמיות\", \"כיוון פרונט\", \"בדיקת מחשב\").\n"
        "- service_en: תרגום קצר במקרה הצורך.\n\n"
        "פורמט JSON חובה (אל תסטה ממנו):\n"
        "{\n"
        "  \"extracted\": {\n"
        "    \"car\": {\n"
        "      \"make\": null,\n"
        "      \"model\": null,\n"
        "      \"year\": null,\n"
        "      \"mileage\": null\n"
        "    },\n"
        "    \"invoice\": {\n"
        "      \"date\": null,\n"
        "      \"total_price_ils\": null,\n"
        "      \"region\": null,\n"
        "      \"garage_type\": \"dealer|private|unknown\"\n"
        "    },\n"
        "    \"line_items\": [\n"
        "      {\n"
        "        \"description\": null,\n"
        "        \"invoice_price_ils\": null,\n"
        "        \"qty\": null\n"
        "      }\n"
        "    ],\n"
        "    \"redaction\": {\n"
        "      \"applied\": true,\n"
        "      \"notes\": null\n"
        "    },\n"
        "    \"confidence\": {\n"
        "      \"overall\": 0.0\n"
        "    }\n"
        "  },\n"
        "  \"benchmarks_web\": [\n"
        "    {\n"
        "      \"line_item_description\": null,\n"
        "      \"suggested_service_type\": \"oil_change|filters|brake_pads_front|brake_discs_front|brake_pads_rear|battery|tires|ac_gas|spark_plugs|timing_belt|clutch|alternator|starter|suspension_arm|shock_absorber|wheel_alignment|diagnostic_scan|transmission_fluid|coolant|wipers|unknown\",\n"
        "      \"search_queries\": [],\n"
        "      \"market_samples_ils\": [],\n"
        "      \"price_range_ils\": {\"min\": null, \"max\": null},\n"
        "      \"sources\": [\n"
        "        {\"url\": null, \"title\": null}\n"
        "      ],\n"
        "      \"confidence\": 0.0,\n"
        "      \"notes\": null\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "הנחיות איכות לבנצ׳מרק:\n"
        "- עבור כל שורת טיפול: נסה/י להחזיר לפחות 10 מחירים שונים ב-market_samples_ils. אם לא אפשרי, החזר/י כמה שיש.\n"
        "- נקה/י מחירים שאינם בש\"ח או שאינם רלוונטיים (למשל חלק בלבד בלי עבודה) אם ניתן לזהות זאת.\n"
        "- עבור כל רשומת בנצ׳מרק: רשום/י את כל השאילתות שבוצעו בפועל ב-search_queries.\n"
        "- sources: נסה/י לכלול לפחות 2 מקורות כאשר אפשר, עם url + title + date_retrieved_utc + locality_hint.\n"
        "- confidence לבנצ׳מרק:\n"
        "  - 0.8+ אם יש 10+ דגימות ממקורות אמינים/רלוונטיים בישראל\n"
        "  - 0.5 אם יש 5–9 דגימות\n"
        "  - 0.2 אם פחות מ-5 או מקורות חלשים\n\n"
        "התחל/י עכשיו."
    )

    try:
        try:
            grounding_tool = genai_types.Tool(google_search=genai_types.GoogleSearch())
        except Exception as exc:
            current_app.logger.error("Failed to initialize Google Search grounding tool.", exc_info=True)
            raise RuntimeError("Google Search grounding unavailable for invoice benchmarks.") from exc
        config_kwargs = {
            "response_mime_type": "application/json",
            "temperature": 0,
            "tools": [grounding_tool],
        }
        try:
            config = genai_types.GenerateContentConfig(**config_kwargs)
        except Exception:
            try:
                config = genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0,
                    tools=[grounding_tool],
                )
            except Exception:
                config = genai_types.GenerateContentConfig(
                    temperature=0,
                    tools=[grounding_tool],
                )

        contents = [
            genai_types.Part.from_text(prompt),
            genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        ]

        # Try with system_instruction if supported
        try:
            response = ai_client.models.generate_content(
                model=GEMINI_VISION_MODEL_ID,
                contents=contents,
                config=config,
            )
        except Exception:
            try:
                fallback_config = genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0,
                    tools=[grounding_tool],
                )
            except Exception:
                fallback_config = genai_types.GenerateContentConfig(
                    temperature=0,
                    tools=[grounding_tool],
                )
            response = ai_client.models.generate_content(
                model=GEMINI_VISION_MODEL_ID,
                contents=contents,
                config=fallback_config,
            )

        result_text = response.text or ""
        if not result_text.strip():
            raise ValueError("Empty model response for invoice extraction.")

        grounding_status = {"verified": False, "reason": "לא התקבלו מטא-נתונים מאומתים של grounding."}
        try:
            candidates = getattr(response, "candidates", None)
            if candidates:
                grounding_chunks, citations = extract_grounding_metadata(candidates[0])
                if grounding_chunks or citations:
                    grounding_status = {"verified": True}
        except Exception:
            grounding_status = {"verified": False, "reason": "כשל באימות מקורות grounding."}

        try:
            result = json.loads(result_text)
        except json.JSONDecodeError:
            json_match = re.search(r'\{[\s\S]*\}', result_text)
            if json_match:
                result = json.loads(json_match.group())
            else:
                raise ValueError("Failed to parse model response as JSON for invoice extraction.")

        if grounding_status.get("verified"):
            current_app.logger.info(
                "Invoice grounding metadata present (request_id=%s).", request_id
            )

        # Handle both old format (flat) and new format (nested under "extracted")
        if "extracted" in result:
            try:
                validate_vision_payload(result)
            except Exception:
                current_app.logger.exception("Invalid vision payload for invoice extraction.")
                raise
            result["grounding_status"] = grounding_status
            return result
        # Wrap old format into new format
        wrapped_result = {
            "extracted": {
                "car": result.get("car", {}),
                "invoice": result.get("invoice", {}),
                "line_items": result.get("line_items", []),
                "redaction": result.get("redaction", {}),
                "confidence": result.get("confidence", {}),
            },
            "benchmarks_web": result.get("benchmarks_web", []),
            "grounding_status": grounding_status,
        }
        try:
            validate_vision_payload(wrapped_result)
        except Exception:
            current_app.logger.exception("Invalid wrapped vision payload for invoice extraction.")
            raise
        return wrapped_result

    except Exception as e:
        current_app.logger.error(f"Vision extraction with web benchmarks failed: {e}")
        raise


def fetch_text_web_benchmarks(
    canonical_items: List[Dict[str, Any]],
    request_id: str,
) -> List[Dict[str, Any]]:
    import concurrent.futures
    from google.genai import types as genai_types
    from json_repair import repair_json

    if not canonical_items:
        return []
    ai_client = extensions.ai_client
    if not ai_client:
        current_app.logger.warning("AI client not initialized for text web grounding (request_id=%s).", request_id)
        return []

    results: List[Dict[str, Any]] = []
    search_tool = genai_types.Tool(google_search=genai_types.GoogleSearch())
    config = genai_types.GenerateContentConfig(
        temperature=0,
        tools=[search_tool],
        response_mime_type="application/json",
    )

    for item in canonical_items[:WEB_GROUNDING_MAX_ITEMS]:
        description = item.get("raw_description") or item.get("canonical_code") or ""
        if not description:
            continue
        prompt = f"""
מצא מקורות ישראליים למחיר השירות הבא והחזר JSON בלבד.
החזר מבנה:
{{
  "line_item_description": "{description}",
  "market_samples_ils": [120, 140, 160],
  "price_range_ils": {{"min": 120, "max": 160, "median": 140}},
  "sources": [{{"url": "https://example.co.il", "title": "כותרת מקור"}}],
  "notes": ["הערות אופציונליות"]
}}
דרישות:
- מקורות ישראליים בלבד (.il או הקשר ישראלי).
- מספרים בלבד (ILS) וללא ציטוט טקסט מהמקור.
- החזר כמה דגימות מספריות שאפשר.
- אין להחזיר מידע מזהה אישי.
""".strip()

        def _invoke():
            return ai_client.models.generate_content(
                model=GEMINI_VISION_MODEL_ID,
                contents=prompt,
                config=config,
            )

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_invoke)
                response = future.result(timeout=WEB_GROUNDING_TIMEOUT_SEC)
        except concurrent.futures.TimeoutError:
            current_app.logger.warning(
                "Text web grounding timeout (request_id=%s).", request_id
            )
            continue
        except Exception:
            current_app.logger.exception(
                "Text web grounding failed (request_id=%s).", request_id
            )
            continue

        text = (getattr(response, "text", "") or "").strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except Exception:
            try:
                parsed = json.loads(repair_json(text))
            except Exception:
                continue

        benchmark = None
        if isinstance(parsed, dict):
            if isinstance(parsed.get("benchmarks_web"), list) and parsed.get("benchmarks_web"):
                benchmark = parsed["benchmarks_web"][0]
            else:
                benchmark = parsed
        elif isinstance(parsed, list) and parsed:
            if isinstance(parsed[0], dict):
                benchmark = parsed[0]

        if not isinstance(benchmark, dict):
            continue

        sources = benchmark.get("sources") or []
        if isinstance(sources, list):
            sources = [s for s in sources if isinstance(s, dict)][:WEB_GROUNDING_MAX_SOURCES]
        else:
            sources = []

        benchmark["line_item_description"] = benchmark.get("line_item_description") or description
        benchmark["sources"] = sources
        results.append(benchmark)

    return results


def match_web_benchmarks_to_items(
    benchmarks_web: List[Dict],
    canonical_items: List[Dict],
) -> Dict[str, Dict[str, Any]]:
    """
    Match web benchmark entries to canonical items by fuzzy matching on description.
    Returns dict: canonical_code -> samples/sources/notes.
    Ignores model's suggested_service_type; uses deterministic canonical_code instead.
    """
    matched: Dict[str, Dict[str, Any]] = {}

    for bm in benchmarks_web:
        bm_desc = bm.get("line_item_description") or ""
        bm_samples = extract_benchmark_samples(bm)
        if not bm_samples:
            continue

        # Try to find matching canonical item
        best_code = None
        bm_normalized = normalize_text(bm_desc)
        bm_words = [w for w in bm_normalized.split() if len(w) > 2] if bm_normalized else []

        for item in canonical_items:
            item_desc = normalize_text(item.get("raw_description") or "")
            item_code = item.get("canonical_code", "")
            # Check if descriptions share significant overlap
            if bm_normalized and item_desc and (
                bm_normalized in item_desc or item_desc in bm_normalized
                or any(w in item_desc for w in bm_words)
            ):
                best_code = item_code
                break

        # Fallback: try matching by deterministic code from description
        if not best_code:
            code, _ = match_canonical_code(bm_desc)
            if code:
                best_code = code

        if best_code:
            valid_samples = [int(round(s)) for s in bm_samples if isinstance(s, (int, float)) and s > 0]
            sources = bm.get("sources") or []
            notes = bm.get("notes") or bm.get("note") or []
            entry = matched.setdefault(best_code, {"samples": [], "sources": [], "notes": []})
            if valid_samples:
                entry["samples"].extend(valid_samples)
            if sources:
                entry["sources"].extend(sources)
            if notes:
                if isinstance(notes, list):
                    entry["notes"].extend(notes)
                else:
                    entry["notes"].append(notes)

    return matched


def compute_year_bucket(year: Optional[int]) -> Optional[str]:
    """Compute 5-year bucket string from year."""
    if not year:
        return None
    bucket_start = (year // 5) * 5
    return f"{bucket_start}-{bucket_start + 4}"


def compute_mileage_bucket(mileage: Optional[int]) -> Optional[str]:
    """Compute 50k bucket string from mileage."""
    if not mileage:
        return None
    bucket_start = (mileage // 50000) * 50000
    return f"{bucket_start}-{bucket_start + 50000}"


def persist_benchmark_items(
    canonical_items: List[Dict],
    ctx: Dict[str, Any],
) -> None:
    """
    Persist anonymized benchmark items derived ONLY from invoice-extracted data.
    No web samples, no report_json, no PII.
    Only called when user has given anonymized_storage consent.
    """
    from app.models import ServicePriceBenchmarkItem

    invoice_date = ctx.get("invoice_date")
    invoice_month = None
    if invoice_date:
        try:
            if isinstance(invoice_date, str) and len(invoice_date) >= 7:
                invoice_month = invoice_date[:7]
        except Exception:
            pass

    year_bucket = compute_year_bucket(ctx.get("year"))
    mileage_bucket = compute_mileage_bucket(ctx.get("mileage"))

    for item in canonical_items:
        price = item.get("price_ils")
        if not price or price <= 0:
            continue

        benchmark = ServicePriceBenchmarkItem(
            canonical_code=item["canonical_code"],
            category=item.get("category"),
            price_ils=price,
            parts_ils=item.get("parts_ils"),
            labor_ils=item.get("labor_ils"),
            qty=item.get("qty"),
            make=ctx.get("make"),
            model=ctx.get("model"),
            year_bucket=year_bucket,
            mileage_bucket=mileage_bucket,
            region=ctx.get("region"),
            garage_type=ctx.get("garage_type"),
            invoice_month=invoice_month,
        )
        db.session.add(benchmark)


def persist_invoice(
    user_id: int,
    parsed_json: Dict,
    report_json: Dict,
    ctx: Dict[str, Any],
    canonical_items: List[Dict],
    duration_ms: int,
    request_id: str,
) -> int:
    """
    Persist invoice data to database.
    Returns the invoice ID.
    """
    from datetime import date as date_type
    
    # Parse invoice_date to date object if string
    invoice_date = ctx.get("invoice_date")
    if invoice_date and isinstance(invoice_date, str):
        try:
            invoice_date = datetime.strptime(invoice_date, "%Y-%m-%d").date()
        except ValueError:
            invoice_date = None
    elif isinstance(invoice_date, date_type):
        pass  # Already a date
    else:
        invoice_date = None
    
    # Create invoice record
    invoice = ServiceInvoice(
        user_id=user_id,
        make=ctx.get("make"),
        model=ctx.get("model"),
        year=ctx.get("year"),
        mileage=ctx.get("mileage"),
        region=ctx.get("region"),
        garage_type=ctx.get("garage_type"),
        invoice_date=invoice_date,
        total_price_ils=ctx.get("total_price"),
        currency="ILS",
        parsed_json=json.dumps(parsed_json, ensure_ascii=False),
        report_json=json.dumps(report_json, ensure_ascii=False),
        duration_ms=duration_ms,
        request_id=request_id,
    )
    db.session.add(invoice)
    db.session.flush()  # Get the ID
    
    # Create item records
    for item in canonical_items:
        invoice_item = ServiceInvoiceItem(
            invoice_id=invoice.id,
            canonical_code=item["canonical_code"],
            category=item.get("category"),
            raw_description=item.get("raw_description"),
            price_ils=item.get("price_ils"),
            labor_ils=item.get("labor_ils"),
            parts_ils=item.get("parts_ils"),
            qty=item.get("qty"),
            confidence=item.get("confidence"),
        )
        db.session.add(invoice_item)
    
    # Increment user's counter
    user = User.query.get(user_id)
    if user:
        user.service_price_checks_count = (user.service_price_checks_count or 0) + 1
    
    db.session.commit()
    
    return invoice.id


def handle_invoice_analysis(
    user_id: int,
    image_bytes: bytes,
    mime_type: str,
    request_id: str,
    overrides: Optional[Dict] = None,
    anon_storage_consented: bool = False,
) -> Tuple[Dict, int]:
    """
    Main handler for invoice analysis.
    Returns (report, invoice_id).
    """
    import time as pytime
    
    start_time = pytime.time()

    # Extract data from image + web benchmarks (single call)
    raw_result = vision_extract_invoice_with_web_benchmarks(image_bytes, mime_type, request_id)

    extracted = raw_result.get("extracted", raw_result)
    benchmarks_web = raw_result.get("benchmarks_web", [])
    grounding_status = raw_result.get("grounding_status")
    if grounding_status is None:
        grounding_status = {"verified": False, "reason": "missing_grounding_metadata"}
    if "verified" not in grounding_status:
        grounding_status["verified"] = False
    if not isinstance(benchmarks_web, list):
        benchmarks_web = []

    current_app.logger.info(
        "Invoice raw benchmarks_web=%s grounding_status=%s request_id=%s",
        len(benchmarks_web),
        grounding_status,
        request_id,
    )
    if benchmarks_web:
        first_benchmark = benchmarks_web[0] if isinstance(benchmarks_web[0], dict) else {}
        sources_count = len(first_benchmark.get("sources") or []) if isinstance(first_benchmark, dict) else 0
        samples_count = len(extract_benchmark_samples(first_benchmark)) if isinstance(first_benchmark, dict) else 0
        current_app.logger.info(
            "Invoice benchmark keys=%s sources_n=%s samples_n=%s request_id=%s",
            list(first_benchmark.keys()) if isinstance(first_benchmark, dict) else [],
            sources_count,
            samples_count,
            request_id,
        )

    if not benchmarks_web:
        try:
            fallback_extracted = vision_extract_invoice(image_bytes, mime_type, request_id)
            if isinstance(fallback_extracted, dict):
                extracted = fallback_extracted
        except Exception:
            current_app.logger.warning(
                "Fallback vision extraction failed (request_id=%s).", request_id,
                exc_info=True,
            )

    # Sanitize extracted data
    sanitized = deterministic_sanitize_no_pii(extracted)

    # Build context from extracted data + overrides
    car_data = sanitized.get("car") or {}
    invoice_data = sanitized.get("invoice") or {}

    ctx = {
        "make": (overrides or {}).get("make") or car_data.get("make"),
        "model": (overrides or {}).get("model") or car_data.get("model"),
        "year": (overrides or {}).get("year") or car_data.get("year"),
        "mileage": (overrides or {}).get("mileage") or car_data.get("mileage"),
        "region": (overrides or {}).get("region") or invoice_data.get("region"),
        "garage_type": (overrides or {}).get("garage_type") or invoice_data.get("garage_type"),
        "invoice_date": invoice_data.get("date"),
        "total_price": invoice_data.get("total_price_ils"),
    }

    # Canonicalize line items
    raw_items = sanitized.get("line_items") or []
    canonical_items = canonicalize_line_items(raw_items)

    if not benchmarks_web:
        benchmarks_web = fetch_text_web_benchmarks(canonical_items, request_id)
        if benchmarks_web:
            current_app.logger.info(
                "Fallback web benchmarks created=%s request_id=%s",
                len(benchmarks_web),
                request_id,
            )
        else:
            current_app.logger.warning(
                "No usable web benchmarks found (request_id=%s).", request_id
            )

    if not grounding_status.get("verified", False) and any(
        benchmark_has_usable_content(bm) for bm in benchmarks_web
    ):
        grounding_status = {"verified": True, "reason": "verified_by_content"}

    # Match web benchmarks to canonical items
    web_samples_map = match_web_benchmarks_to_items(benchmarks_web, canonical_items)

    # Compute samples metadata
    total_web_samples = sum(len(v.get("samples", [])) for v in web_samples_map.values())
    samples_meta = {"total_cohort_n": total_web_samples}

    # Build report
    report = build_report(
        ctx,
        canonical_items,
        ctx.get("total_price"),
        samples_meta,
        web_samples_map=web_samples_map,
        grounding_status=grounding_status,
    )

    # Include web benchmarks in report (for user history display only)
    report["benchmarks_web"] = benchmarks_web

    duration_ms = int((pytime.time() - start_time) * 1000)

    # Persist to database (full report for user history)
    invoice_id = persist_invoice(
        user_id,
        sanitized,
        report,
        ctx,
        canonical_items,
        duration_ms,
        request_id,
    )

    # Persist anonymized benchmark items if consent given
    if anon_storage_consented:
        try:
            persist_benchmark_items(canonical_items, ctx)
            db.session.commit()
        except Exception as e:
            current_app.logger.warning(f"Failed to persist benchmark items: {e}")
            db.session.rollback()

    return report, invoice_id
