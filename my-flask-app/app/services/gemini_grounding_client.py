# -*- coding: utf-8 -*-
"""Shared Gemini helpers for plain and Google Search-grounded calls."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import uuid
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
GROUNDING_PERMISSION_DENIED_HE_MESSAGE = "החיפוש האינטרנטי של ספק ה-AI לא מורשה או לא זמין בפרויקט הנוכחי."
PROVIDER_HE_MESSAGE = "שירות ה-AI לא זמין כרגע."

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "key",
    "password",
    "proxy-authorization",
    "secret",
    "set-cookie",
    "token",
)

# Headers safe to include in diagnostics (exact lowercase match required).
_SAFE_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "content-length",
        "x-request-id",
        "x-goog-request-id",
        "server",
        "date",
        "grpc-status",
        "grpc-message",
        "grpc-status-details-bin",
    }
)

# Controls verbose debug output; never logs secrets even when true.
# Note: _VERBOSE is not used directly; _is_verbose() re-reads at call time to support tests.


def _is_verbose() -> bool:
    """Re-read env var at call time to support runtime changes in tests."""
    return os.environ.get("GEMINI_DEBUG_VERBOSE", "false").strip().lower() == "true"


def _redact_safe(value: Any, key: str = "") -> Any:
    """Bound and redact diagnostic values before they reach logs or health output."""
    key_lower = key.lower()
    if any(part in key_lower for part in _SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact_safe(v, str(k)) for k, v in list(value.items())[:25]}
    if isinstance(value, list):
        return [_redact_safe(item, key) for item in value[:20]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    return text[:800]


def _safe_response_headers(response: Any) -> Dict[str, str]:
    """Return only safe, non-sensitive response headers."""
    raw_headers = getattr(response, "headers", None)
    if not raw_headers:
        return {}
    out: Dict[str, str] = {}
    try:
        items = raw_headers.items() if hasattr(raw_headers, "items") else raw_headers
        for k, v in items:
            k_lower = k.lower()
            if k_lower in _SAFE_RESPONSE_HEADERS:
                out[k_lower] = str(v)[:200]
    except Exception:
        pass
    return out


def _safe_gemini_error_details(err: Any) -> Dict[str, Any]:
    """Return safe Gemini provider diagnostics without API keys, auth headers, cookies, or payloads."""
    if err is None:
        return {"type": "None"}
    details: Dict[str, Any] = {
        "type": type(err).__name__,
        "message": str(err)[:1200],
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
        if _is_verbose():
            text = getattr(response, "text", None)
            if text:
                details["response_text"] = str(text)[:1200]
            body = getattr(response, "body", None)
            if body:
                details["response_body"] = str(body)[:1200]
            details["response_headers"] = _safe_response_headers(response)
        else:
            # Even in compact mode include text truncated to 300 chars for triage.
            text = getattr(response, "text", None)
            if text:
                details["response_text"] = str(text)[:300]
        try:
            response_json = response.json()
        except Exception:
            response_json = None
        if response_json is not None:
            details["response_json"] = _redact_safe(response_json)
    body = getattr(err, "body", None)
    if body:
        details["body"] = str(body)[:1200] if _is_verbose() else str(body)[:300]
    return _redact_safe(details)


# Backward-compat alias — existing callers that use _safe_error_details continue to work.
# Deprecated: prefer _safe_gemini_error_details in new code.
_safe_error_details = _safe_gemini_error_details


def _gemini_key_info() -> Dict[str, Any]:
    """Return safe key-source info; never the raw key."""
    gemini_key = os.environ.get("GEMINI_API_KEY") or ""
    google_key = os.environ.get("GOOGLE_API_KEY") or ""
    if gemini_key:
        source = "GEMINI_API_KEY"
        key = gemini_key
    elif google_key:
        source = "GOOGLE_API_KEY"
        key = google_key
    else:
        source = "none"
        key = ""
    if key:
        # SHA256 used as a non-reversible diagnostic fingerprint only — not for password storage.
        fingerprint = "sha256:" + hashlib.sha256(key.encode()).hexdigest()[:16]
    else:
        fingerprint = "none"
    return {"source": source, "fingerprint": fingerprint}


def _sdk_version() -> str:
    try:
        import importlib.metadata
        return importlib.metadata.version("google-genai")
    except Exception:
        return "unknown"


def _log_request_config(
    *,
    request_id: str,
    feature: str,
    model: str,
    api_method: str,
    endpoint_family: str,
    tools: List[str],
    response_mime_type: Optional[str],
    temperature: Optional[float],
    max_output_tokens: Optional[int],
    prompt: str,
) -> None:
    """Log a safe [AI_DEBUG] gemini_request_config event before each Gemini call."""
    key_info = _gemini_key_info()
    prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()[:16]
    log_data: Dict[str, Any] = {
        "request_id": request_id,
        "feature": feature,
        "model": model,
        "api_method": api_method,
        "endpoint_family": endpoint_family,
        "tools": tools,
        "response_mime_type": response_mime_type,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "prompt_chars": len(prompt),
        "prompt_sha256_prefix": prompt_sha,
        "selected_key_source": key_info["source"],
        "selected_key_fingerprint": key_info["fingerprint"],
        "sdk_version": _sdk_version(),
    }
    logger.info(
        "[AI_DEBUG] gemini_request_config %s",
        json.dumps(log_data, ensure_ascii=False, sort_keys=True),
    )


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


def call_plain_model(model_id: str, prompt: str, *, timeout_sec: int = AI_CALL_TIMEOUT_SEC, use_executor: bool = True, feature: str = "unknown", request_id: Optional[str] = None, **config_kwargs) -> Dict[str, Any]:
    if extensions.ai_client is None:
        return {"text": "", "grounding_meta": _default_meta(), "error_code": "CLIENT_NOT_INITIALIZED", "error_details": {"type": "ClientNotInitialized"}}
    config_kwargs.pop("tools", None)
    config_kwargs.setdefault("temperature", 0.0)
    config = genai_types.GenerateContentConfig(**config_kwargs)

    _log_request_config(
        request_id=request_id or str(uuid.uuid4())[:8],
        feature=feature,
        model=model_id,
        api_method="generate_content_plain",
        endpoint_family="models.generateContent",
        tools=[],
        response_mime_type=getattr(config, "response_mime_type", None),
        temperature=config_kwargs.get("temperature"),
        max_output_tokens=config_kwargs.get("max_output_tokens"),
        prompt=prompt,
    )

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
        details = _safe_gemini_error_details(err or "EMPTY")
        if _is_verbose():
            logger.info("[AI_DEBUG] generate_content_plain error request_id=%s details=%s", request_id, json.dumps(details, ensure_ascii=False, sort_keys=True))
        return {"text": "", "grounding_meta": _default_meta(), "error_code": _error_code(err or "EMPTY", False), "error_details": details}
    return {"text": _get_text(resp), "grounding_meta": _default_meta(), "error_code": None, "error_details": None}


def call_grounded_model(model_id: str, prompt: str, *, timeout_sec: int = AI_CALL_TIMEOUT_SEC, use_executor: bool = True, feature: str = "unknown", request_id: Optional[str] = None) -> Dict[str, Any]:
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

    _log_request_config(
        request_id=request_id or str(uuid.uuid4())[:8],
        feature=feature,
        model=model_id,
        api_method="interactions_grounded",
        endpoint_family="interactions",
        tools=["google_search"],
        response_mime_type=None,
        temperature=None,
        max_output_tokens=None,
        prompt=prompt,
    )

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
        details = _safe_gemini_error_details(err or "EMPTY")
        logger.warning("[AI] grounded Gemini interaction failed details=%s", json.dumps(details, ensure_ascii=False, sort_keys=True))
        if _is_verbose():
            logger.info("[AI_DEBUG] interactions_grounded error request_id=%s details=%s", request_id, json.dumps(details, ensure_ascii=False, sort_keys=True))
        return {"text": "", "grounding_meta": _default_meta(), "error_code": _error_code(err or "EMPTY", True), "error_details": details}
    meta = _collect_interaction_grounding(resp)
    return {"text": _get_text(resp), "grounding_meta": meta, "error_code": None, "error_details": None}
