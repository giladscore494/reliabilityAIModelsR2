# -*- coding: utf-8 -*-
"""TypeSafe JEV client: ONE System One call carrying many narrow judgments.

JEV never discovers facts, never validates sources and never picks a winner.
The request is built by ``judgments.JevJudgmentRegistry`` (compact validated
state + independent ``score`` questions); this module sends it once and
parses every answer with strict, typed, per-question validation. A malformed
answer makes only that question ``judgment_unavailable``.

``confidence`` is JEV's own measure of how concentrated its answer
distribution is. It is stored as returned, never computed or averaged here,
and it is not a probability of being correct.

The model id is taken from ``JEV_MODEL`` and verified against
``GET /v1/models`` before use; an unverified id is never used.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("comparison_v2")

DEFAULT_TYPESAFE_BASE_URL = "https://api.typesafe.ai"
MODELS_CACHE_TTL_SEC = 6 * 3600

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "judgment_unavailable"

# Expected value vs. distribution may differ slightly by rounding.
SCORE_CONSISTENCY_TOLERANCE = 0.25
PROBABILITY_SUM_TOLERANCE = 0.02


class JevUnavailable(Exception):
    pass


def jev_model_configured() -> str:
    return (os.environ.get("JEV_MODEL") or "").strip()


def typesafe_base_url() -> str:
    return (os.environ.get("TYPESAFE_BASE_URL") or DEFAULT_TYPESAFE_BASE_URL).rstrip("/")


class TypeSafeJevClient:
    """Thin HTTP client. ``session`` is injectable (tests never hit the network)."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, base_url: Optional[str] = None,
                 session: Any = None, timeout_sec: Optional[float] = None):
        self.api_key = api_key if api_key is not None else (os.environ.get("TYPESAFE_API_KEY") or "")
        self.model = model if model is not None else jev_model_configured()
        self.base_url = (base_url or typesafe_base_url()).rstrip("/")
        self.timeout_sec = timeout_sec or float(os.environ.get("JEV_TIMEOUT_SEC", "30"))
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        self.calls = 0  # systemone calls made by this client

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Accept": "application/json"}

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)

    def list_models(self) -> List[str]:
        resp = self.session.get(f"{self.base_url}/v1/models", headers=self._headers(), timeout=min(self.timeout_sec, 15))
        if getattr(resp, "status_code", 500) >= 400:
            raise JevUnavailable(f"models_http_{resp.status_code}")
        return extract_model_ids(resp.json())

    def systemone(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        resp = self.session.post(f"{self.base_url}/v1/systemone", json=payload, headers=self._headers(), timeout=self.timeout_sec)
        if getattr(resp, "status_code", 500) >= 400:
            raise JevUnavailable(f"systemone_http_{resp.status_code}")
        data = resp.json()
        if not isinstance(data, dict):
            raise JevUnavailable("systemone_invalid_json")
        return data


def extract_model_ids(payload: Any) -> List[str]:
    """Collect ids and aliases from a /v1/models response of unknown exact shape."""
    ids: List[str] = []

    def add(val):
        if isinstance(val, str) and val.strip() and val not in ids:
            ids.append(val.strip())

    items = []
    if isinstance(payload, dict):
        for key in ("data", "models", "items"):
            if isinstance(payload.get(key), list):
                items.extend(payload[key])
    elif isinstance(payload, list):
        items = payload
    for item in items:
        if isinstance(item, str):
            add(item)
        elif isinstance(item, dict):
            for key in ("id", "name", "model", "slug"):
                add(item.get(key))
            for key in ("aliases", "alias"):
                val = item.get(key)
                if isinstance(val, list):
                    for alias in val:
                        add(alias)
                else:
                    add(val)
    return ids


_verified_lock = threading.Lock()
_verified: Dict[Tuple[str, str], float] = {}


def reset_model_verification_cache() -> None:
    with _verified_lock:
        _verified.clear()


def verify_model(client: TypeSafeJevClient) -> Tuple[bool, Optional[str]]:
    """True only when JEV_MODEL appears in GET /v1/models for this account."""
    if not client.configured:
        return False, "jev_not_configured"
    cache_key = (client.base_url, client.model)
    now = time.monotonic()
    with _verified_lock:
        seen = _verified.get(cache_key)
        if seen and now - seen < MODELS_CACHE_TTL_SEC:
            return True, None
    try:
        available = client.list_models()
    except Exception as exc:
        return False, f"models_unavailable:{type(exc).__name__}"
    if client.model not in available:
        logger.warning("comparison_v2 jev_model_not_available configured=%s available_count=%s", client.model, len(available))
        return False, "jev_model_not_in_account_models"
    with _verified_lock:
        _verified[cache_key] = now
    return True, None


# ---------------------------------------------------------------------------
# typed answers
# ---------------------------------------------------------------------------
@dataclass
class JevMicroJudgmentResult:
    question_id: str
    type: str  # score | noul | choice
    status: str  # ok | judgment_unavailable
    reason: Optional[str] = None
    score: Optional[float] = None  # expected level, 0..levels-1 (score)
    level_count: Optional[int] = None
    noul: Optional[float] = None  # probability (noul)
    choice: Optional[str] = None  # (choice)
    confidence: Optional[float] = None  # JEV's own value, stored as returned
    probabilities: Optional[Dict[str, float]] = None
    legend: Optional[Dict[str, str]] = None
    score_consistent: Optional[bool] = None

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _unavailable(qid: str, qtype: str, reason: str) -> JevMicroJudgmentResult:
    return JevMicroJudgmentResult(question_id=qid, type=qtype, status=STATUS_UNAVAILABLE, reason=reason)


def _probabilities(raw: Any, allowed_keys: List[str]) -> Tuple[Optional[Dict[str, float]], Optional[str]]:
    if raw is None:
        return None, None
    if not isinstance(raw, dict) or not raw:
        return None, "probabilities_not_object"
    out: Dict[str, float] = {}
    for key, val in raw.items():
        key = str(key)
        if key not in allowed_keys:
            return None, "probability_key_unknown"
        num = _finite(val)
        if num is None or not 0.0 <= num <= 1.0:
            return None, "probability_value_invalid"
        out[key] = num
    if abs(sum(out.values()) - 1.0) > PROBABILITY_SUM_TOLERANCE:
        return None, "probabilities_do_not_sum_to_1"
    return out, None


def _confidence(raw: Any) -> Tuple[Optional[float], Optional[str]]:
    if raw is None:
        return None, None
    num = _finite(raw)
    if num is None or not 0.0 <= num <= 1.0:
        return None, "confidence_invalid"
    return num, None


def parse_score_answer(qid: str, answer: Any, level_count: int) -> JevMicroJudgmentResult:
    if not isinstance(answer, dict):
        return _unavailable(qid, "score", "missing_answer" if answer is None else "answer_not_object")
    if answer.get("type") not in (None, "score"):
        return _unavailable(qid, "score", "unexpected_type")
    score = _finite(answer.get("score"))
    if score is None:
        return _unavailable(qid, "score", "score_not_finite_number")
    if not 0.0 <= score <= level_count - 1:
        return _unavailable(qid, "score", "score_out_of_range")
    probs, err = _probabilities(answer.get("probabilities"), [str(i) for i in range(level_count)])
    if err:
        return _unavailable(qid, "score", err)
    conf, err = _confidence(answer.get("confidence"))
    if err:
        return _unavailable(qid, "score", err)
    consistent = None
    if probs:
        expected = sum(int(k) * p for k, p in probs.items())
        consistent = abs(expected - score) <= SCORE_CONSISTENCY_TOLERANCE
        if not consistent:
            return _unavailable(qid, "score", "score_inconsistent_with_distribution")
    legend_raw = answer.get("legend")
    legend = None
    if isinstance(legend_raw, dict):
        legend = {str(k): str(v)[:300] for k, v in legend_raw.items() if str(k) in {str(i) for i in range(level_count)}}
    return JevMicroJudgmentResult(question_id=qid, type="score", status=STATUS_OK, score=score, level_count=level_count,
                                  confidence=conf, probabilities=probs, legend=legend, score_consistent=consistent)


def parse_noul_answer(qid: str, answer: Any) -> JevMicroJudgmentResult:
    if not isinstance(answer, dict):
        return _unavailable(qid, "noul", "missing_answer" if answer is None else "answer_not_object")
    if answer.get("type") not in (None, "noul"):
        return _unavailable(qid, "noul", "unexpected_type")
    p = _finite(answer.get("noul"))
    if p is None or not 0.0 <= p <= 1.0:
        return _unavailable(qid, "noul", "noul_invalid")
    return JevMicroJudgmentResult(question_id=qid, type="noul", status=STATUS_OK, noul=p)


def parse_choice_answer(qid: str, answer: Any, allowed: List[str]) -> JevMicroJudgmentResult:
    if not isinstance(answer, dict):
        return _unavailable(qid, "choice", "missing_answer" if answer is None else "answer_not_object")
    if answer.get("type") not in (None, "choice"):
        return _unavailable(qid, "choice", "unexpected_type")
    choice = answer.get("choice")
    if choice not in allowed:
        return _unavailable(qid, "choice", "choice_not_allowed")
    probs, err = _probabilities(answer.get("probabilities"), list(allowed))
    if err:
        return _unavailable(qid, "choice", err)
    conf, err = _confidence(answer.get("confidence"))
    if err:
        return _unavailable(qid, "choice", err)
    return JevMicroJudgmentResult(question_id=qid, type="choice", status=STATUS_OK, choice=choice, confidence=conf, probabilities=probs)


def parse_answers(data: Dict[str, Any], questions: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, JevMicroJudgmentResult], List[str]]:
    """Validate each requested question independently; ignore unrequested ids."""
    answers = data.get("answers") if isinstance(data.get("answers"), dict) else {}
    out: Dict[str, JevMicroJudgmentResult] = {}
    for qid, q in questions.items():
        raw = answers.get(qid)
        if q["type"] == "score":
            out[qid] = parse_score_answer(qid, raw, len(q["criteria"]))
        elif q["type"] == "noul":
            out[qid] = parse_noul_answer(qid, raw)
        else:
            out[qid] = parse_choice_answer(qid, raw, list(q["criteria"]))
    unexpected = sorted(str(k) for k in answers if k not in questions)
    return out, unexpected


# ---------------------------------------------------------------------------
# the single call
# ---------------------------------------------------------------------------
def _usage(data: Dict[str, Any]) -> Dict[str, Optional[int]]:
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {
        k: (usage.get(k) if isinstance(usage.get(k), int) and not isinstance(usage.get(k), bool) else None)
        for k in ("input_tokens", "output_tokens")
    }


def run_system_one(client: Optional[TypeSafeJevClient], body_builder, questions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Send every question in ONE request (zero requests when there is nothing to ask).

    ``body_builder(model) -> request body`` keeps the model id out of the
    question registry. Returns a JSON-serialisable run record.
    """
    base = {"question_count": len(questions), "answers": {}, "unexpected_answer_ids": [], "response_model": None,
            "requested_model": getattr(client, "model", None) or None, "usage": None, "duration_ms": None}
    if not questions:
        return {**base, "status": "not_needed", "reason": "no_questions"}
    if client is None:
        return {**base, "status": "failed", "reason": "jev_disabled"}
    ok, reason = verify_model(client)
    if not ok:
        return {**base, "status": "failed", "reason": reason or "jev_unverified"}
    payload = body_builder(client.model)
    started = time.perf_counter()
    try:
        data = client.systemone(payload)
    except Exception as exc:
        logger.warning("comparison_v2 jev_failed error=%s", type(exc).__name__)
        return {**base, "status": "failed", "reason": f"jev_error:{type(exc).__name__}",
                "duration_ms": int((time.perf_counter() - started) * 1000)}
    parsed, unexpected = parse_answers(data, questions)
    usable = sum(1 for r in parsed.values() if r.ok)
    return {
        **base,
        "status": "ok" if usable else "failed",
        "reason": None if usable else "no_usable_answers",
        "answers": {qid: r.to_dict() for qid, r in parsed.items()},
        "unexpected_answer_ids": unexpected,
        "response_model": data.get("model") if isinstance(data.get("model"), str) else None,
        "usage": _usage(data),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "usable_answers": usable,
    }


def redacted_request_shape(payload: Dict[str, Any]) -> Dict[str, Any]:
    """For docs/diagnostics: headers are never part of the payload; the key never appears."""
    return {
        "endpoint": "POST {TYPESAFE_BASE_URL}/v1/systemone",
        "headers": {"Authorization": "Bearer [REDACTED]", "Content-Type": "application/json"},
        "body": {
            "model": payload.get("model"),
            "state_keys": sorted(payload.get("state", {}).keys()),
            "questions": {qid: {"type": q["type"], "criteria_count": len(q["criteria"])} for qid, q in payload.get("questions", {}).items()},
        },
    }
