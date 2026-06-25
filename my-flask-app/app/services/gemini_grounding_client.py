# -*- coding: utf-8 -*-
"""Shared Gemini helpers for plain and Google Search-grounded calls."""

from __future__ import annotations

import concurrent.futures
import logging
from typing import Any, Dict, List, Optional

from google.genai import types as genai_types

import app.extensions as extensions
from app.config import AI_CALL_TIMEOUT_SEC, AI_EXECUTOR, AI_EXECUTOR_WORKERS

logger = logging.getLogger(__name__)

GROUNDING_FAILED_CODE = "GEMINI_GROUNDING_FAILED"
PROVIDER_FAILED_CODE = "GEMINI_PROVIDER_FAILED"
GROUNDING_HE_MESSAGE = "החיפוש האינטרנטי של ספק ה-AI לא זמין כרגע. נסה שוב מאוחר יותר."
PROVIDER_HE_MESSAGE = "שירות ה-AI לא זמין כרגע. נסה שוב מאוחר יותר."


def _default_meta() -> Dict[str, Any]:
    return {"grounding_successful": False, "source_count": 0, "search_queries": [], "sources": []}


def _execute_with_timeout(fn, timeout_sec: int):
    try:
        work_queue = getattr(AI_EXECUTOR, "_work_queue", None)
        if work_queue is not None and work_queue.qsize() >= AI_EXECUTOR_WORKERS:
            return None, "EXECUTOR_SATURATED"
        future = AI_EXECUTOR.submit(fn)
    except Exception:
        return None, "EXECUTOR_SATURATED"
    try:
        return future.result(timeout=timeout_sec), None
    except concurrent.futures.TimeoutError:
        future.cancel()
        return None, "CALL_TIMEOUT"
    except Exception as exc:
        return None, exc


def _get_text(resp: Any) -> str:
    text = getattr(resp, "output_text", None)
    if text:
        return str(text).strip()
    text = getattr(resp, "text", None)
    if text:
        return str(text).strip()
    parts: List[str] = []
    for item in getattr(resp, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            val = getattr(content, "text", None)
            if val:
                parts.append(str(val))
    return "\n".join(parts).strip()


def _as_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(exclude_none=True)
        except Exception:
            pass
    if hasattr(obj, "to_json_dict"):
        try:
            return obj.to_json_dict()
        except Exception:
            pass
    return {}


def _collect_interaction_grounding(resp: Any) -> Dict[str, Any]:
    meta = _default_meta()
    seen_sources = set()

    def add_source(url: Optional[str], title: Optional[str] = None):
        if not url:
            return
        key = str(url)
        if key in seen_sources:
            return
        seen_sources.add(key)
        meta["sources"].append({"url": key, "title": title or ""})

    def walk(obj: Any):
        data = _as_dict(obj)
        typ = data.get("type") or getattr(obj, "type", None)
        if typ == "google_search_call":
            query = data.get("query") or data.get("search_query") or data.get("q")
            if query:
                meta["search_queries"].append(str(query))
        if typ == "google_search_result":
            add_source(data.get("url"), data.get("title"))
        for ann in data.get("annotations") or []:
            ann_d = _as_dict(ann)
            if (ann_d.get("type") or "") == "url_citation" or ann_d.get("url"):
                add_source(ann_d.get("url"), ann_d.get("title"))
        for key in ("steps", "output", "content", "items", "results"):
            for child in data.get(key) or []:
                walk(child)
        if not data:
            for key in ("steps", "output", "content"):
                for child in getattr(obj, key, None) or []:
                    walk(child)

    walk(resp)
    # Some SDKs expose steps as rich attrs not included in model_dump.
    for step in getattr(resp, "steps", None) or []:
        walk(step)
    meta["source_count"] = len(meta["sources"])
    meta["grounding_successful"] = bool(meta["search_queries"] or meta["sources"])
    return meta


def _error_code(err: Any, grounded: bool) -> str:
    if err == "CALL_TIMEOUT":
        return "CALL_TIMEOUT"
    if err == "EXECUTOR_SATURATED":
        return "SERVER_BUSY"
    return GROUNDING_FAILED_CODE if grounded else PROVIDER_FAILED_CODE


def call_plain_model(model_id: str, prompt: str, *, timeout_sec: int = AI_CALL_TIMEOUT_SEC, use_executor: bool = True, **config_kwargs) -> Dict[str, Any]:
    if extensions.ai_client is None:
        return {"text": "", "grounding_meta": _default_meta(), "error_code": "CLIENT_NOT_INITIALIZED"}
    config_kwargs.pop("tools", None)
    config_kwargs.setdefault("temperature", 0.0)
    config = genai_types.GenerateContentConfig(**config_kwargs)

    def _invoke():
        return extensions.ai_client.models.generate_content(model=model_id, contents=prompt, config=config)

    if use_executor:
        resp, err = _execute_with_timeout(_invoke, timeout_sec)
    else:
        try:
            resp, err = _invoke(), None
        except Exception as exc:
            resp, err = None, exc
    if err or resp is None:
        return {"text": "", "grounding_meta": _default_meta(), "error_code": _error_code(err or "EMPTY", False)}
    return {"text": _get_text(resp), "grounding_meta": _default_meta(), "error_code": None}


def call_grounded_model(model_id: str, prompt: str, *, timeout_sec: int = AI_CALL_TIMEOUT_SEC, use_executor: bool = True) -> Dict[str, Any]:
    """Call Gemini with Google Search via the Interactions API; never fall back silently."""
    if extensions.ai_client is None:
        return {"text": "", "grounding_meta": _default_meta(), "error_code": "CLIENT_NOT_INITIALIZED"}
    interactions = getattr(extensions.ai_client, "interactions", None)
    create = getattr(interactions, "create", None)
    if create is None:
        logger.error("[AI] Gemini Interactions API unavailable on installed SDK/client")
        return {"text": "", "grounding_meta": _default_meta(), "error_code": GROUNDING_FAILED_CODE}

    def _invoke():
        return create(model=model_id, input=prompt, tools=[{"type": "google_search"}], store=False)

    if use_executor:
        resp, err = _execute_with_timeout(_invoke, timeout_sec)
    else:
        try:
            resp, err = _invoke(), None
        except Exception as exc:
            resp, err = None, exc
    if err or resp is None:
        logger.warning("[AI] grounded Gemini interaction failed: %s", type(err).__name__ if isinstance(err, Exception) else err)
        return {"text": "", "grounding_meta": _default_meta(), "error_code": _error_code(err or "EMPTY", True)}
    meta = _collect_interaction_grounding(resp)
    return {"text": _get_text(resp), "grounding_meta": meta, "error_code": None}
