# -*- coding: utf-8 -*-
"""Service Price Check service logic."""

import json
import os
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app

from app.extensions import db
import app.extensions as extensions
from app.models import ServiceInvoice, ServiceInvoiceItem, User
from app.legal import GEMINI_VISION_MODEL_ID

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
        price = parse_price(item.get("price_ils") or item.get("price"))
        qty = item.get("qty") or 1
        
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
) -> Dict[str, Any]:
    """
    Build the final report with percentiles, labels, and analysis.
    """
    now = datetime.utcnow()
    
    per_item = []
    red_flags = []
    sum_items = 0
    total_labor = 0
    total_parts = 0
    discount_target = 0
    
    for item in canonical_items:
        code = item["canonical_code"]
        price = item.get("price_ils") or 0
        sum_items += price
        total_labor += item.get("labor_ils") or 0
        total_parts += item.get("parts_ils") or 0
        
        # Get cohort samples
        samples = cohort_price_samples(
            code,
            make=ctx.get("make"),
            model=ctx.get("model"),
            year=ctx.get("year"),
            mileage=ctx.get("mileage"),
            region=ctx.get("region"),
            garage_type=ctx.get("garage_type"),
        )
        
        cohort_n = len(samples)
        percentiles = compute_percentiles(samples)
        rank = percentile_rank(samples, price) if price else 0.5
        
        # Determine label
        if cohort_n >= 20:
            p50 = percentiles["p50"] or 0
            p75 = percentiles["p75"] or 0
            p90 = percentiles["p90"] or 0
            
            if price <= p50:
                label = "סביר/נמוך"
            elif price <= p75:
                label = "סביר"
            elif price <= p90:
                label = "יקר"
            else:
                label = "חריג"
                red_flags.append(f"{code}: מחיר חריג")
            
            # Calculate overpay estimate
            overpay = max(0, price - p75)
            if overpay > 0:
                discount_target += overpay
        else:
            # Use fallback ranges
            fb = fallback_ranges(code)
            if fb["min"] is not None and fb["max"] is not None:
                if price <= fb["max"]:
                    label = "סביר (טווח כללי)"
                else:
                    label = "יקר (טווח כללי)"
                    red_flags.append(f"{code}: מעל טווח כללי")
                overpay = max(0, price - fb["max"])
            else:
                label = "אין מספיק נתונים"
                overpay = 0
        
        per_item.append({
            "canonical_code": code,
            "raw_description": item.get("raw_description"),
            "price_ils": price,
            "cohort_n": cohort_n,
            "p50": percentiles.get("p50"),
            "p75": percentiles.get("p75"),
            "p90": percentiles.get("p90"),
            "percentile_rank": round(rank, 2),
            "label": label,
            "overpay_estimate_ils": overpay,
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
    total_cohort_n = samples_meta.get("total_cohort_n", 0)
    if total_cohort_n >= 20:
        avg_rank = sum(i.get("percentile_rank", 0.5) for i in per_item) / len(per_item) if per_item else 0.5
        fairness_score = max(0, min(100, int(100 - avg_rank * 100)))
        fairness_note = None
    else:
        fairness_score = 60
        fairness_note = "insufficient cohort"
    
    # Build negotiation script
    negotiation_lines = []
    if discount_target > 0:
        negotiation_lines.append(f"לפי נתוני השוק, ניתן לנסות להוריד כ-₪{discount_target:,} מהמחיר הכולל")
        for item in per_item:
            if item.get("overpay_estimate_ils", 0) > 0:
                negotiation_lines.append(
                    f"  - {item['canonical_code']}: מחיר גבוה ב-₪{item['overpay_estimate_ils']:,} מאחוזון 75"
                )
    
    return {
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
        "disclaimer": "מידע כללי, לא אבחון/התחייבות מחיר",
    }


def vision_extract_invoice(
    image_bytes: bytes,
    mime_type: str,
    request_id: str,
) -> Dict[str, Any]:
    """
    Use Gemini Vision to extract structured data from invoice image.
    Requests redaction of PII in the response.
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
) -> Tuple[Dict, int]:
    """
    Main handler for invoice analysis.
    Returns (report, invoice_id).
    """
    import time as pytime
    
    start_time = pytime.time()
    
    # Extract data from image
    raw_extracted = vision_extract_invoice(image_bytes, mime_type, request_id)
    
    # Sanitize extracted data
    sanitized = deterministic_sanitize_no_pii(raw_extracted)
    
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
    
    # Compute samples metadata - only pass relevant context fields
    cohort_ctx = {
        k: v for k, v in ctx.items()
        if k in ("make", "model", "year", "mileage", "region", "garage_type")
    }
    total_samples = sum(
        len(cohort_price_samples(item["canonical_code"], **cohort_ctx))
        for item in canonical_items
    )
    samples_meta = {"total_cohort_n": total_samples}
    
    # Build report
    report = build_report(
        ctx,
        canonical_items,
        ctx.get("total_price"),
        samples_meta,
    )
    
    duration_ms = int((pytime.time() - start_time) * 1000)
    
    # Persist to database
    invoice_id = persist_invoice(
        user_id,
        sanitized,
        report,
        ctx,
        canonical_items,
        duration_ms,
        request_id,
    )
    
    return report, invoice_id
