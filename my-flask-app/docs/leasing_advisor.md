# Leasing Advisor – Usage & File Format

## Overview

The **Leasing Advisor** helps users choose an optimal company (leasing) car
based on their BIK (Benefit-in-Kind / שווי שימוש) budget frame, personal
preferences, and either an uploaded company car list or the built-in Israeli
market catalog.

## Two Entry Modes

### Mode 1 – Upload Company Car List (Easy)

1. Upload a CSV or XLSX file with your company's available cars.
2. Optionally filter by max BIK, powertrain, or body type.
3. Answer a short questionnaire about your preferences.
4. Get AI-ranked recommendations from Gemini 3 Flash.

### Mode 2 – From Scratch

1. Enter either:
   - **Monthly BIK budget** (₪) – the tool inverts to approximate list price.
   - **List price** (₪) – the tool computes BIK.
2. Choose powertrain preference (EV/PHEV/Hybrid/ICE/All).
3. Candidates are selected from the built-in catalog.
4. Answer the questionnaire → get recommendations.

## File Format (CSV / XLSX)

### Required Columns (at least one of):

| Column              | Aliases (Hebrew OK)                 |
|---------------------|--------------------------------------|
| `make`              | `יצרן`, `manufacturer`              |
| `model`             | `דגם`, `model_name`                 |
| `list_price_ils`    | `list_price`, `מחיר מחירון`, `מחיר` |
| `powertrain`        | `הנעה`, `fuel`, `fuel_type`, `סוג דלק` |

### Optional Columns:

| Column       | Description                          |
|--------------|--------------------------------------|
| `trim`       | Trim level / גימור                  |
| `body_type`  | sedan / suv / hatchback              |
| `seats`      | Number of seats (default: 5)         |
| `trunk_liters` | Trunk volume in liters             |

### Constraints:

- **Max file size**: 5 MB
- **Supported formats**: `.csv` (UTF-8 or Latin-1) and `.xlsx`
- **Empty files** are rejected
- Column names are case-insensitive and whitespace-trimmed

## BIK 2026 Calculation

```
rate          = 0.0248
cap_price     = ₪596,860
green_discount = {ev: ₪1,380, phev: ₪1,150, hybrid: ₪580, ice: ₪0}

price_for_calc = min(list_price, cap_price)
monthly_bik    = price_for_calc × rate − discount
monthly_bik    = max(bik, 0)
```

> **Note**: Constants are labeled "verify annually" in source code.
> Update `BIK_RATE_2026`, `BIK_CAP_PRICE_2026`, and `BIK_GREEN_DISCOUNTS_2026`
> in `app/services/leasing_advisor_service.py` when tax rules change.

## Questionnaire (14 Questions)

| Question                   | Options                      |
|----------------------------|------------------------------|
| Driving type               | city / highway / mixed       |
| Daily km                   | 0-20 / 20-60 / 60+          |
| Typical passengers         | 1 / 2 / 3-5                 |
| Trunk importance           | low / med / high             |
| City parking ease          | low / med / high             |
| Ride comfort & quiet       | low / med / high             |
| Safety / ADAS              | low / med / high             |
| Tech (CarPlay/cameras)     | low / med / high             |
| Prestige / image           | low / med / high             |
| Body height preference     | low / med / high             |
| Charging availability      | home / work / none           |
| BIK sensitivity            | must_reduce / neutral        |
| Reliability sensitivity    | low / med / high             |
| **Optional**: Fuel/maint. cost sensitivity | low / med / high |

## API Endpoints

| Method | Path                     | Description                        |
|--------|--------------------------|------------------------------------|
| GET    | `/leasing`               | Render leasing advisor page        |
| POST   | `/api/leasing/frame`     | Compute BIK frame + candidates     |
| POST   | `/api/leasing/recommend` | Run Gemini ranking (quota-gated)   |

### POST `/api/leasing/frame`

**With file upload** (multipart/form-data):
- `file`: CSV or XLSX file
- `powertrain`: optional filter (ev/phev/hybrid/ice/unknown)
- `body_type`: optional filter (suv/sedan/hatchback)
- `max_bik`: optional max BIK filter (₪)

**Without file** (application/json):
```json
{
  "max_bik": 3000,
  "powertrain": "ev",
  "body_type": "suv"
}
```

### POST `/api/leasing/recommend`

```json
{
  "candidates": [...],
  "prefs": { "driving_type": "mixed", ... },
  "frame": { "source": "catalog", ... },
  "legal_confirm": true
}
```

**Requires**: Authentication, legal acceptance, daily quota (5/day).

## Daily Quota

- **5 AI recommendation runs per user per day** (shared with other AI tools).
- Resets at midnight in the configured timezone.
- Returns `DAILY_LIMIT_REACHED` error when exceeded.

## Security

- CSRF: Origin/Referer validation on all POST endpoints
- File upload: max 5 MB, CSV/XLSX only, sanitized filenames
- Legal gating: server-side enforcement on `/api/leasing/recommend`
- Rate limiting: per-IP per-minute + per-user per-day
- No stack traces exposed to client

## Database

- Results stored in `leasing_advisor_history` table
- Accessible via Dashboard → "יועץ ליסינג" tab
- Migration: `d1e2f3a4b5c6_add_leasing_advisor_history.py`
