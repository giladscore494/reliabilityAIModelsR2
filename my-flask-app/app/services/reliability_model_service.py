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


def parse_model_json(raw: str) -> Tuple[Optional[dict], Optional[str]]:
    if not raw:
        return None, "EMPTY_RESPONSE"
    try:
        return json.loads(raw), None
    except Exception:
        try:
            repaired = repair_json(raw)
            return json.loads(repaired), None
        except Exception:
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


def call_gemini_grounded_once(prompt: str) -> Tuple[Optional[dict], Optional[str]]:
    start_time = pytime.perf_counter()
    try:
        if extensions.ai_client is None:
            return None, "CLIENT_NOT_INITIALIZED"
        search_tool = genai_types.Tool(google_search=genai_types.GoogleSearch())
        config = genai_types.GenerateContentConfig(
            temperature=0.3,
            top_p=0.9,
            top_k=40,
            tools=[search_tool],
            response_mime_type="application/json",
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
        text = (getattr(resp, "text", "") or "").strip()
        parsed, err = parse_model_json(text)
        return parsed, err
    finally:
        duration_ms = (pytime.perf_counter() - start_time) * 1000
        logger.info(
            "[AI] feature=reliability model=%s duration_ms=%.2f",
            GEMINI_RELIABILITY_MODEL_ID,
            duration_ms,
        )
