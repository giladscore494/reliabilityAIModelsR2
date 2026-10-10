# -*- coding: utf-8 -*-
"""V3 versions, vocabulary and messages."""

from __future__ import annotations

ENGINE_VERSION = "comparison-v3/1"
SNAPSHOT_CONTRACT_VERSION = "canonical-vehicle-snapshot/2"
FACTS_CONTRACT = "vehicle-facts/1"
# The row / reason rules inside comparison-v3/1. Part of the request hash, so a rule change never serves a comparison
# cached under the old rules (live fixes: one row set, N-car reasons, no body fit, CO2 across families).
DECISION_RULES_VERSION = "v3-rules/3"          # /3: the driver-assistance systems listed per car

SLOT_KEYS = ("car_1", "car_2", "car_3")
MIN_CARS = 2
MAX_CARS = 3

# Source levels of a fact. Government facts keep the V2 Level 1.5 label; TRIPY's open-data facts are a new level;
# the ministry's data.gov.il datasets (new-car prices, recall notices, final cancellations) are a level of their own,
# never open data.
SOURCE_LEVEL_GOVERNMENT = "1.5"
SOURCE_LEVEL_GOVERNMENT_DATASET = "government_dataset"
SOURCE_LEVEL_OPEN_DATA = "open_data"
SOURCE_LEVEL_USER = "user_supplied"
GOVERNMENT_DATASET_LABEL_HE = "משרד התחבורה — מאגר data.gov.il"

# Decision vocabulary (identical strings to V2 so the summary validator and the UI read both).
CHOICE_TIE = "tie"
CHOICE_INSUFFICIENT = "insufficient_evidence"
DECISION_UNAVAILABLE = "decision_unavailable"

FACTS_UNAVAILABLE = "facts_unavailable"
FACTS_UNAVAILABLE_HE = "ההשוואה לא זמינה כרגע, נסו שוב בעוד כמה דקות"
# The only "no data" text of V3: no weighted category is left after the row rule.
NO_COMMON_DATA_HE = "אין מספיק נתונים משותפים להשוואה בין הרכבים האלה"
BUDGET_NEEDS_PRICES_HE = "כדי לבדוק תקציב יש להזין מחיר לכל רכב"

ASKING_PRICE_MIN = 1_000
ASKING_PRICE_MAX = 3_000_000

PROGRESS_STAGES = (
    "resolving_vehicles",
    "loading_facts",
    "comparing_facts",
    "evaluating_decision",
    "writing_explanations",
    "writing_summary",
    "complete",
)
PROGRESS_LABELS_HE = {
    "resolving_vehicles": "מזהה גרסאות",
    "loading_facts": "טוען את נתוני הרכבים",
    "comparing_facts": "משווה את הנתונים",
    "evaluating_decision": "שוקל את ההבדלים לפי הצרכים שלך",
    "writing_explanations": "מנסח הסברים לשורות",
    "writing_summary": "מנסח את הסיכום",
    "complete": "הושלם",
}
