# Gemini Debug Ops Note

## Overview

The app exposes a `/api/admin/gemini-health` endpoint (owner-only) that runs a
four-check matrix to pinpoint the exact failure class when Gemini calls return
HTTP 403 Forbidden or other errors.

---

## Render Environment Variables

1. **Check for conflicting API keys**

   Open Render > your service > Environment.

   - If **both** `GEMINI_API_KEY` and `GOOGLE_API_KEY` are set, the Google SDK
     may silently prefer `GOOGLE_API_KEY`.  Remove `GOOGLE_API_KEY` unless it is
     intentionally needed for OAuth.
   - Prefer **only** `GEMINI_API_KEY` for all Gemini model calls.

2. **Use a fresh key from Google AI Studio**

   - Go to <https://aistudio.google.com/app/apikey> and create a new API key.
   - Paste it into Render as `GEMINI_API_KEY`.
   - Redeploy the service.

---

## Enabling Verbose Debug Output

Set `GEMINI_DEBUG_VERBOSE=true` in Render environment variables to include:

- Full response body preview (up to 1 200 chars) in logs.
- Safe response headers (non-sensitive only) in error details.
- Detailed error summaries in `/api/admin/gemini-health` output.

> **Never** logs raw API keys, Authorization headers, cookies, OAuth tokens, or
> `x-goog-api-key` — even when verbose mode is on.

---

## After Redeploy — Interpreting the Health Check Matrix

Open `GET /api/admin/gemini-health` (must be logged in as owner).

The response contains a `checks` object with four keys:

| Check key | What it tests |
|---|---|
| `generate_content_plain` | `models.generateContent` — no tools |
| `interactions_plain` | `interactions.create` — no tools |
| `interactions_grounded` | `interactions.create` with `google_search` tool |
| `generate_content_grounded_legacy` | `models.generateContent` with `GoogleSearch` tool _(diagnostic only)_ |

### Diagnosis rules

| `diagnosis` value | Meaning |
|---|---|
| `GEMINI_KEY_OR_PROJECT_ACCESS_FAILED` | All four checks failed — wrong key or project has no Gemini access |
| `INTERACTIONS_ENDPOINT_PERMISSION_OR_SDK_ISSUE` | `generate_content_plain` OK but `interactions_plain` fails — SDK version or account doesn't have Interactions access |
| `GOOGLE_SEARCH_GROUNDING_PERMISSION_DENIED` | Plain calls OK, all grounded calls fail — Google Search grounding not enabled for this project/key |
| `LEGACY_GROUNDING_PATH_FAILED_USE_INTERACTIONS` | `interactions_grounded` OK but legacy `generate_content_grounded_legacy` fails — expected, Interactions is the product path |
| `OK` | All checks passed |

---

## Boot Diagnostics

At startup the app logs a `[BOOT_DIAG]` line that includes:

- Python version
- `google-genai` package version
- `authlib`, `requests`, `httpx` versions
- Render commit SHA and service name
- Whether `GEMINI_API_KEY` and `GOOGLE_API_KEY` are present
- Selected key source and fingerprint (sha256 prefix only — never the raw key)
- All configured Gemini model IDs

If both `GEMINI_API_KEY` and `GOOGLE_API_KEY` are set, a `HIGH` warning is
emitted:

```
[BOOT_DIAG] HIGH: Both GEMINI_API_KEY and GOOGLE_API_KEY are set. Google SDKs
may prefer GOOGLE_API_KEY unless client is initialized with explicit api_key.
```

---

## Key Restrictions Checklist

If the health check shows `GEMINI_KEY_OR_PROJECT_ACCESS_FAILED`:

1. Verify the key is not restricted to specific IPs or referers in the Google
   Cloud Console (`APIs & Services > Credentials`).
2. Confirm that **Generative Language API** is enabled for the project.
3. Confirm the key is not expired or revoked.
4. Create a fresh unrestricted key from Google AI Studio for testing.

If the health check shows `GOOGLE_SEARCH_GROUNDING_PERMISSION_DENIED`:

1. Google Search grounding may require a billing-enabled project.
2. Check that your Google Cloud project has the Grounding API or Google Search
   grounding feature enabled.
3. Alternatively, set `WEB_GROUNDING_PROVIDER=external_search` and configure
   `BRAVE_SEARCH_API_KEY` or `SERPAPI_API_KEY` as fallback.
