# -*- coding: utf-8 -*-
"""Shared Gemini helpers for plain and Google Search-grounded calls."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests
from google.genai import types as genai_types

import app.extensions as extensions
from app.config import (
    AI_CALL_TIMEOUT_SEC,
    AI_EXECUTOR,
    AI_EXECUTOR_WORKERS,
    ALLOW_EXTERNAL_SEARCH_GROUNDING,
    WEB_GROUNDING_PROVIDER,
)

logger = logging.getLogger(__name__)

GROUNDING_FAILED_CODE = "GEMINI_GROUNDING_FAILED"
GROUNDING_PERMISSION_DENIED_CODE = "GEMINI_GROUNDING_PERMISSION_DENIED"
PROVIDER_FAILED_CODE = "GEMINI_PROVIDER_FAILED"
GROUNDING_HE_MESSAGE = "החיפוש האינטרנטי של ספק ה-AI לא זמין כרגע. נסה שוב מאוחר יותר."
GROUNDING_PERMISSION_DENIED_HE_MESSAGE = "המודל פעיל, אבל Google Search grounding אינו מורשה בפרויקט הנוכחי."
PROVIDER_HE_MESSAGE = "שירות ה-AI לא זמין כרגע."

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "key",
    "password",
    "secret",
    "token",
)


def _redact_safe(value: Any, key: str = "") -> Any:
    """Bound and redact diagnostic values before they reach logs or health output."""
    if any(part in key.lower() for part in _SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact_safe(v, str(k)) for k, v in list(value.items())[:25]}
    if isinstance(value, list):
        return [_redact_safe(item, key) for item in value[:20]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    return text[:800]


def _safe_error_details(err: Any) -> Dict[str, Any]:
    """Return safe provider diagnostics without API keys, auth headers, cookies, or payloads."""
    if err is None:
        return {"type": "None"}
    details: Dict[str, Any] = {
        "type": type(err).__name__,
        "message": str(err)[:800],
    }
    for attr in ("code", "status_code"):
        val = getattr(err, attr, None)
        if val is not None:
            details[attr] = val
    response = getattr(err, "response", None)
    if response is not None:
        status_code = getattr(response, "status_code", None)
        if status_code is not None:
            details["response_status_code"] = status_code
        text = getattr(response, "text", None)
        if text:
            details["response_text"] = str(text)[:800]
        body = getattr(response, "body", None)
        if body:
            details["response_body"] = str(body)[:800]
        try:
            response_json = response.json()
        except Exception:
            response_json = None
        if response_json is not None:
            details["response_json"] = response_json
    body = getattr(err, "body", None)
    if body:
        details["body"] = body
    return _redact_safe(details)


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
    if grounded:
        status = getattr(err, "status_code", None) or getattr(getattr(err, "response", None), "status_code", None)
        code = getattr(err, "code", None)
        err_text = f"{type(err).__name__} {err}".lower()
        if status == 403 or code == 403 or "permission" in err_text or "forbidden" in err_text:
            return GROUNDING_PERMISSION_DENIED_CODE
        return GROUNDING_FAILED_CODE
    return PROVIDER_FAILED_CODE


def _external_search_available() -> bool:
    return bool(
        os.environ.get("BRAVE_SEARCH_API_KEY")
        or os.environ.get("SERPAPI_API_KEY")
        or os.environ.get("EXTERNAL_SEARCH_API_KEY")
    )


def _external_web_search(query: str, timeout_sec: int) -> Dict[str, Any]:
    brave_key = os.environ.get("BRAVE_SEARCH_API_KEY") or os.environ.get("EXTERNAL_SEARCH_API_KEY")
    if brave_key:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 5},
            headers={"Accept": "application/json", "X-Subscription-Token": brave_key},
            timeout=min(timeout_sec, 20),
        )
        resp.raise_for_status()
        results = (resp.json().get("web") or {}).get("results") or []
        return {
            "search_queries": [query],
            "sources": [
                {"url": r.get("url", ""), "title": r.get("title", ""), "snippet": r.get("description", "")}
                for r in results
                if r.get("url")
            ],
        }
    serp_key = os.environ.get("SERPAPI_API_KEY")
    if serp_key:
        resp = requests.get(
            "https://serpapi.com/search.json",
            params={"q": query, "api_key": serp_key, "num": 5},
            timeout=min(timeout_sec, 20),
        )
        resp.raise_for_status()
        results = resp.json().get("organic_results") or []
        return {
            "search_queries": [query],
            "sources": [
                {"url": r.get("link", ""), "title": r.get("title", ""), "snippet": r.get("snippet", "")}
                for r in results
                if r.get("link")
            ],
        }
    raise RuntimeError("external_search_provider_key_missing")


def _call_external_search_grounded_model(model_id: str, prompt: str, *, timeout_sec: int, use_executor: bool) -> Dict[str, Any]:
    def _invoke_search():
        return _external_web_search(prompt[:500], timeout_sec)

    if use_executor:
        search, search_err = _execute_with_timeout(_invoke_search, timeout_sec)
    else:
        try:
            search, search_err = _invoke_search(), None
        except Exception as exc:
            search, search_err = None, exc
    if search_err or not search:
        details = _safe_error_details(search_err or "EMPTY")
        logger.warning("[AI] external web grounding failed details=%s", json.dumps(details, ensure_ascii=False, sort_keys=True))
        return {"text": "", "grounding_meta": _default_meta(), "error_code": GROUNDING_FAILED_CODE, "error_details": details}
    sources = search.get("sources") or []
    context = "\n".join(
        f"- {src.get('title', '')}: {src.get('snippet', '')} ({src.get('url', '')})"
        for src in sources
    )
    grounded_prompt = (
        "Use ONLY the following external web search sources as grounded context. "
        "Preserve the requested output schema and source tracking.\n\n"
        f"Sources:\n{context}\n\nUser request:\n{prompt}"
    )
    result = call_plain_model(model_id, grounded_prompt, timeout_sec=timeout_sec, use_executor=use_executor)
    result["grounding_meta"] = {
        "grounding_successful": bool(sources),
        "source_count": len(sources),
        "search_queries": search.get("search_queries") or [],
        "sources": [{"url": src.get("url", ""), "title": src.get("title", "")} for src in sources],
    }
    if not result.get("error_code") and not result["grounding_meta"]["grounding_successful"]:
        result["error_code"] = GROUNDING_FAILED_CODE
    return result


def call_plain_model(model_id: str, prompt: str, *, timeout_sec: int = AI_CALL_TIMEOUT_SEC, use_executor: bool = True, **config_kwargs) -> Dict[str, Any]:
    if extensions.ai_client is None:
        return {"text": "", "grounding_meta": _default_meta(), "error_code": "CLIENT_NOT_INITIALIZED", "error_details": {"type": "ClientNotInitialized"}}
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
        return {"text": "", "grounding_meta": _default_meta(), "error_code": _error_code(err or "EMPTY", False), "error_details": _safe_error_details(err or "EMPTY")}
    return {"text": _get_text(resp), "grounding_meta": _default_meta(), "error_code": None, "error_details": None}


def call_grounded_model(model_id: str, prompt: str, *, timeout_sec: int = AI_CALL_TIMEOUT_SEC, use_executor: bool = True) -> Dict[str, Any]:
    """Call Gemini with mandatory web grounding; never fall back silently."""
    provider = (os.environ.get("WEB_GROUNDING_PROVIDER") or WEB_GROUNDING_PROVIDER).strip().lower()
    allow_external = (os.environ.get("ALLOW_EXTERNAL_SEARCH_GROUNDING") or str(ALLOW_EXTERNAL_SEARCH_GROUNDING)).strip().lower() == "true"
    if provider == "external_search" and allow_external and _external_search_available():
        return _call_external_search_grounded_model(model_id, prompt, timeout_sec=timeout_sec, use_executor=use_executor)
    if provider == "external_search" and not allow_external:
        logger.warning("[AI] external_search grounding requested but ALLOW_EXTERNAL_SEARCH_GROUNDING is false; using gemini_search")
    if extensions.ai_client is None:
        return {"text": "", "grounding_meta": _default_meta(), "error_code": "CLIENT_NOT_INITIALIZED", "error_details": {"type": "ClientNotInitialized"}}
    interactions = getattr(extensions.ai_client, "interactions", None)
    create = getattr(interactions, "create", None)
    if create is None:
        logger.error("[AI] Gemini Interactions API unavailable on installed SDK/client")
        return {"text": "", "grounding_meta": _default_meta(), "error_code": GROUNDING_FAILED_CODE, "error_details": {"type": "InteractionsUnavailable"}}

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
        details = _safe_error_details(err or "EMPTY")
        logger.warning("[AI] grounded Gemini interaction failed details=%s", json.dumps(details, ensure_ascii=False, sort_keys=True))
        return {"text": "", "grounding_meta": _default_meta(), "error_code": _error_code(err or "EMPTY", True), "error_details": details}
    meta = _collect_interaction_grounding(resp)
    return {"text": _get_text(resp), "grounding_meta": meta, "error_code": None, "error_details": None}
