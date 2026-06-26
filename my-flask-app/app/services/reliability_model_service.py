# -*- coding: utf-8 -*-
"""Reliability model call and parsing helpers."""

import concurrent.futures
import json
import logging
import os
import random
import re as _re
import time as pytime
from typing import Optional, Tuple

from json_repair import repair_json
from google.genai import types as genai_types
try:
    import google.generativeai as genai  # Legacy SDK (optional)
except Exception:
    genai = None

import app.extensions as extensions
from app.config import AI_CALL_TIMEOUT_SEC, AI_EXECUTOR, AI_EXECUTOR_WORKERS
from app.extensions import GEMINI_RELIABILITY_MODEL_ID

logger = logging.getLogger(__name__)

PRIMARY_MODEL = os.environ.get("PRIMARY_MODEL", GEMINI_RELIABILITY_MODEL_ID)
FALLBACK_MODEL = os.environ.get("FALLBACK_MODEL", GEMINI_RELIABILITY_MODEL_ID)
RETRIES = int(os.environ.get("RETRIES", "2"))
RETRY_BACKOFF_SEC = float(os.environ.get("RETRY_BACKOFF_SEC", "1"))


def call_model_with_retry(prompt: str) -> dict:
    """Call Gemini AI model with retry logic, exponential backoff, and timeout.
    
    Phase 1F: Reliability hardening with timeouts and bounded retries.
    
    Args:
        prompt: The prompt to send to the AI model
        
    Returns:
        dict: Parsed JSON response from the model
    
    Raises:
        RuntimeError: If all retries fail
    """
    if genai is None:
        raise RuntimeError("Legacy Gemini SDK unavailable")
    last_err = None
    for model_name in [PRIMARY_MODEL, FALLBACK_MODEL]:
        try:
            llm = genai.GenerativeModel(model_name)
        except Exception as e:
            last_err = e
            logger.error("[AI] init %s: %s", model_name, e)
            continue
        
        for attempt in range(1, RETRIES + 1):
            try:
                logger.debug("[AI] Calling %s (attempt %s/%s)", model_name, attempt, RETRIES)
                
                # Phase 1F: Configure timeout at SDK level if supported
                # Note: google-generativeai SDK doesn't expose direct timeout config in generate_content
                # but we can use request_options if available in newer versions
                generation_config = {
                    'temperature': 0.3,
                    'top_p': 0.9,
                    'top_k': 40,
                }
                
                # Call with timeout handling at application level
                # The SDK internally uses requests/httpx with default timeouts
                resp = llm.generate_content(
                    prompt,
                    generation_config=generation_config
                )
                
                raw = (getattr(resp, "text", "") or "").strip()
                
                # Phase 1C: Post-validate model output (JSON structure validation)
                if not raw:
                    raise ValueError("Empty response from model")
                
                try:
                    # Try to extract JSON from response
                    m = _re.search(r"\{.*\}", raw, _re.DOTALL)
                    data = json.loads(m.group()) if m else json.loads(raw)
                except Exception:
                    # Fallback: use json-repair for malformed JSON
                    data = json.loads(repair_json(raw))
                
                # Validate that response is a dict (not a list or primitive)
                if not isinstance(data, dict):
                    raise ValueError(f"Model returned non-object JSON: {type(data).__name__}")
                
                logger.info("[AI] success with %s", model_name)
                return data
                
            except Exception as e:
                error_type = type(e).__name__
                logger.warning("[AI] %s attempt %s/%s failed: %s: %s", model_name, attempt, RETRIES, error_type, e)
                last_err = e
                
                if attempt < RETRIES:
                    # Phase 1F: Exponential backoff with jitter
                    backoff = RETRY_BACKOFF_SEC * (2 ** (attempt - 1))  # exponential
                    jitter = random.uniform(0, 0.5)  # add up to 0.5s jitter
                    sleep_time = backoff + jitter
                    logger.debug("[AI] Retrying in %.2fs...", sleep_time)
                    pytime.sleep(sleep_time)
                continue
    
    # All retries exhausted
    error_msg = f"All AI model attempts failed. Last error: {type(last_err).__name__}"
    logger.error("[AI] %s", error_msg)
    raise RuntimeError(error_msg)


def _strip_code_fences(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = _re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped, count=1)
        stripped = _re.sub(r"\s*```$", "", stripped, count=1)
    return stripped.strip()


def _extract_first_json_object(text: str) -> Optional[str]:
    raw = text or ""
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escaped = False
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : idx + 1]
    return None


def parse_model_json(raw: str) -> Tuple[Optional[dict], Optional[str]]:
    if not raw:
        return None, "EMPTY_RESPONSE"
    cleaned = _strip_code_fences(raw)
    candidate = _extract_first_json_object(cleaned)
    for text in (candidate, cleaned):
        if not text:
            continue
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed, None
        except Exception:
            pass
        try:
            repaired = repair_json(text)
            parsed = json.loads(repaired)
            if isinstance(parsed, dict):
                return parsed, None
        except Exception:
            pass
    return None, "MODEL_JSON_INVALID"


def _execute_with_timeout(fn, timeout_sec: int):
    try:
        work_queue = getattr(AI_EXECUTOR, "_work_queue", None)
        if work_queue is not None:
            queued = work_queue.qsize()
            if queued >= AI_EXECUTOR_WORKERS:
                return None, "EXECUTOR_SATURATED"
        future = AI_EXECUTOR.submit(fn)
    except Exception:
        return None, "EXECUTOR_SATURATED"
    try:
        return future.result(timeout=timeout_sec), None
    except concurrent.futures.TimeoutError:
        # cancel() won't stop already-running work; it prevents callbacks, and any late response may keep the thread busy briefly
        future.cancel()
        return None, "CALL_TIMEOUT"
    except Exception as e:
        return None, e


def extract_grounding_meta(resp) -> dict:
    """Inspect a genai response for real Google Search grounding signals.

    Returns ``{"grounding_successful": bool, "source_count": int,
    "search_queries": [...]}``. This is the single source of truth for whether
    a grounded call actually happened — never trust a model-asserted flag.
    """
    meta = {"grounding_successful": False, "source_count": 0, "search_queries": []}
    try:
        candidates = getattr(resp, "candidates", None) or []
        for cand in candidates:
            gm = getattr(cand, "grounding_metadata", None)
            if gm is None:
                continue
            queries = list(getattr(gm, "web_search_queries", None) or [])
            chunks = list(getattr(gm, "grounding_chunks", None) or [])
            if queries:
                meta["search_queries"].extend([str(q) for q in queries])
            if chunks:
                meta["source_count"] += len(chunks)
            if queries or chunks:
                meta["grounding_successful"] = True
    except Exception:  # pragma: no cover - defensive
        logger.debug("[AI] grounding metadata extraction failed", exc_info=True)
    return meta


def _json_format_repair(raw_text: str, model_id: str, timeout_sec: int = 30) -> Tuple[Optional[dict], Optional[str]]:
    """Non-grounded JSON formatter/repair call.

    Uses response_mime_type="application/json" and only the facts already
    present in *raw_text*. Never adds new facts or performs web search.
    """
    if extensions.ai_client is None:
        return None, "CLIENT_NOT_INITIALIZED"
    truncated = (raw_text or "")[:8000]
    repair_prompt = (
        "Convert the following model response into valid JSON.\n"
        "Use ONLY facts present in the text. Do not add new facts or search the web.\n"
        "The response must be a single JSON object starting with '{' and ending with '}'.\n"
        "No markdown, no code fences, no explanation.\n\n"
        f"MODEL RESPONSE:\n{truncated}"
    )
    config_kwargs = {
        "temperature": 0.0,
        "max_output_tokens": 4096,
        "tools": [],
        "response_mime_type": "application/json",
    }
    try:
        config = genai_types.GenerateContentConfig(**config_kwargs)
    except TypeError:
        config_kwargs.pop("response_mime_type", None)
        config = genai_types.GenerateContentConfig(**config_kwargs)

    def _invoke():
        return extensions.ai_client.models.generate_content(
            model=model_id,
            contents=repair_prompt,
            config=config,
        )
    resp, err = _execute_with_timeout(_invoke, timeout_sec)
    if err:
        code = "CALL_TIMEOUT" if err == "CALL_TIMEOUT" else (
            "SERVER_BUSY" if err == "EXECUTOR_SATURATED" else f"REPAIR_FAILED:{err}"
        )
        return None, code
    if resp is None:
        return None, "REPAIR_EMPTY"
    text = (getattr(resp, "text", "") or "").strip()
    return parse_model_json(text)


def call_gemini_grounded_once(prompt: str) -> Tuple[Optional[dict], Optional[str]]:
    """Single grounded reliability call.

    Google Search grounding is mandatory. The grounding tool and a forced JSON
    mime type are mutually exclusive in the Gemini API, so we enable the tool
    and parse JSON defensively from text. Real grounding signals are attached
    to the parsed payload under ``_grounding_meta`` for honest research_status.
    """
    start_time = pytime.perf_counter()
    grounding_meta = {"grounding_successful": False, "source_count": 0, "search_queries": []}
    repair_used = False
    try:
        if extensions.ai_client is None:
            return None, "CLIENT_NOT_INITIALIZED"
        search_tool = genai_types.Tool(google_search=genai_types.GoogleSearch())
        config = genai_types.GenerateContentConfig(
            temperature=0.3,
            top_p=0.9,
            top_k=40,
            tools=[search_tool],
        )
        def _invoke():
            return extensions.ai_client.models.generate_content(
                model=GEMINI_RELIABILITY_MODEL_ID,
                contents=prompt,
                config=config,
            )
        resp, err = _execute_with_timeout(_invoke, AI_CALL_TIMEOUT_SEC)
        if err == "EXECUTOR_SATURATED":
            return None, "SERVER_BUSY"
        if err == "CALL_TIMEOUT":
            return None, "CALL_TIMEOUT"
        if isinstance(err, Exception):
            return None, f"CALL_FAILED:{type(err).__name__}"
        if resp is None:
            return None, "CALL_FAILED:EMPTY"
        grounding_meta = extract_grounding_meta(resp)
        text = (getattr(resp, "text", "") or "").strip()
        parsed, parse_err = parse_model_json(text)
        if parse_err and text:
            logger.info("[AI] reliability JSON parse failed, attempting format repair")
            repair_used = True
            parsed, parse_err = _json_format_repair(text, GEMINI_RELIABILITY_MODEL_ID)
        if isinstance(parsed, dict):
            parsed["_grounding_meta"] = grounding_meta
        return parsed, parse_err
    finally:
        duration_ms = (pytime.perf_counter() - start_time) * 1000
        logger.info(
            "[AI] feature=reliability model=%s duration_ms=%.2f tools_enabled=%s grounding_successful=%s source_count=%s repair_used=%s",
            GEMINI_RELIABILITY_MODEL_ID,
            duration_ms,
            True,
            grounding_meta.get("grounding_successful"),
            grounding_meta.get("source_count"),
            repair_used,
        )
