# -*- coding: utf-8 -*-
"""List the TypeSafe JEV model ids/aliases available to this account.

Usage (server-side, e.g. a Render shell):
    python -m scripts.jev_models

Reads TYPESAFE_API_KEY (and optional TYPESAFE_BASE_URL) from the environment,
calls GET /v1/models, and prints model ids/aliases plus whether JEV_MODEL is
among them. The API key is never printed; only a short sha256 fingerprint.
"""

from __future__ import annotations

import hashlib
import os
import sys


def main() -> int:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.services.comparison_v2.jev_client import TypeSafeJevClient, jev_model_configured, typesafe_base_url

    key = os.environ.get("TYPESAFE_API_KEY") or ""
    if not key:
        print("TYPESAFE_API_KEY is not set.")
        return 2
    print(f"base_url={typesafe_base_url()} key_fingerprint=sha256:{hashlib.sha256(key.encode()).hexdigest()[:12]}")
    client = TypeSafeJevClient(api_key=key, model=jev_model_configured())
    try:
        models = client.list_models()
    except Exception as exc:  # never echo request internals
        print(f"GET /v1/models failed: {type(exc).__name__}: {str(exc)[:120]}")
        return 1
    print("available models/aliases:")
    for model in models:
        print(f"  - {model}")
    configured = jev_model_configured()
    if configured:
        verdict = "AVAILABLE" if configured in models else "NOT AVAILABLE for this account"
        print(f"JEV_MODEL={configured} -> {verdict}")
    else:
        print("JEV_MODEL is not set. Pick one of the ids/aliases above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
