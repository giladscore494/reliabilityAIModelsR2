# -*- coding: utf-8 -*-
"""Grounding evidence for Level 2 claims (google-genai 2.x, Gemini Developer API).

What the Gemini Developer API actually returns for Google Search grounding
(``candidates[i].grounding_metadata.grounding_chunks[j].web``):

* ``uri``    — an opaque ``https://vertexaisearch.cloud.google.com/grounding-api-redirect/...``
               URL, never the page URL;
* ``title``  — usually the site name (``audi-mediacenter.com``), sometimes a
               human-readable page title;
* ``domain`` — documented by the SDK as "not supported in Gemini API": it is
               absent in production and must never be relied on.

So a claim's ``source_url`` (written by the model) can only be correlated
with evidence Google itself produced:

1. the redirect target of a grounding chunk (resolved with one header-only
   request to Google's redirect endpoint — the target page is never fetched);
2. a URL the URL-context tool reports as successfully retrieved;
3. a grounding chunk title/domain that is a bare hostname.

Correlation tiers (strongest first), all requiring the claim URL to pass the
official allowlist FIRST and the grounded host to belong to the same
manufacturer's registry:

* ``url``  — the claim URL equals a retrieved URL;
* ``host`` — the claim host equals a retrieved host;
* ``site`` — the claim host and a retrieved host are on the same official
  site (``registry_site``), e.g. ``uploads.audi-mediacenter.com`` cited while
  Search reports ``audi-mediacenter.com``. This is the granularity the
  validator always intended (one registry site); before this module a more
  specific registry entry on the claim side made it fail.

Citation metadata (recitation attributions) is not search evidence and is
reported in diagnostics only.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import re
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from app.services.comparison_v2.source_registry import (
    check_official_url,
    classify_host,
    normalize_hostname,
    registry_site,
)

logger = logging.getLogger("comparison_v2")

REDIRECT_HOST = "vertexaisearch.cloud.google.com"
REDIRECT_PATH_PREFIX = "/grounding-api-redirect/"

TIER_URL = "url"
TIER_HOST = "host"
TIER_SITE = "site"
TIER_ORDER = (TIER_URL, TIER_HOST, TIER_SITE)

KIND_CHUNK = "grounding_chunk"
KIND_URL_CONTEXT = "url_context"
KIND_CITATION = "citation"

MAX_SOURCES = 60
MAX_REDIRECTS_RESOLVED = 24
REDIRECT_REQUEST_TIMEOUT_SEC = 3.0
REDIRECT_TOTAL_BUDGET_SEC = 6.0

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value) if value is not None else None


def min_tier() -> str:
    raw = (os.environ.get("COMPARISON_GROUNDING_MIN_TIER") or TIER_SITE).strip().lower()
    return raw if raw in TIER_ORDER else TIER_SITE


def redirect_resolution_enabled() -> bool:
    return (os.environ.get("COMPARISON_GROUNDING_RESOLVE_REDIRECTS") or "true").strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------
def is_grounding_redirect(uri: Any) -> bool:
    """Only Google's grounding redirect endpoint (exact host, https, no port/userinfo)."""
    if not isinstance(uri, str) or len(uri) > 4096:
        return False
    try:
        parts = urlsplit(uri.strip())
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and port is None
        and "@" not in parts.netloc
        and (parts.hostname or "").lower() == REDIRECT_HOST
        and parts.path.startswith(REDIRECT_PATH_PREFIX)
    )


def bare_hostname(text: Any) -> Optional[str]:
    """A title/domain value that is literally a hostname, else None."""
    if not isinstance(text, str):
        return None
    candidate = text.strip().lower()
    if "." not in candidate or " " in candidate or "/" in candidate or len(candidate) > 253:
        return None
    host = normalize_hostname(candidate)
    if not host or "." not in host:
        return None
    if not all(_HOST_LABEL.match(label) for label in host.split(".")):
        return None
    return host


def normalize_url_for_match(url: Any) -> Optional[str]:
    """https URL -> canonical string (lower host, no fragment, no trailing '/')."""
    if not isinstance(url, str) or not url.strip() or len(url) > 2048:
        return None
    try:
        parts = urlsplit(url.strip())
        if parts.port is not None:
            return None
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or "@" in parts.netloc:
        return None
    host = normalize_hostname(parts.hostname)
    if not host:
        return None
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, parts.query, ""))


def _url_host(url: Any) -> Optional[str]:
    norm = normalize_url_for_match(url)
    return urlsplit(norm).hostname if norm else None


# ---------------------------------------------------------------------------
# extraction from a generate_content response
# ---------------------------------------------------------------------------
def extract_grounding_evidence(resp: Any) -> Dict[str, Any]:
    """All Google-produced grounding signals of a response (no page text).

    Returns {"sources": [...], "stats": {...}}. ``sources`` items:
    {kind, uri, title, domain, retrieved_url?, status?}.
    """
    sources: List[Dict[str, Any]] = []
    seen = set()
    queries: List[str] = []
    chunks = supports = url_context_total = url_context_ok = citations = 0
    metadata_present = search_entry_point = False

    def add(item: Dict[str, Any]) -> None:
        key = (item.get("kind"), item.get("uri"), item.get("title"), item.get("retrieved_url"))
        if key in seen or len(sources) >= MAX_SOURCES:
            return
        seen.add(key)
        sources.append(item)

    for cand in _get(resp, "candidates") or []:
        meta = _get(cand, "grounding_metadata")
        if meta is not None:
            metadata_present = True
            queries.extend(str(q) for q in (_get(meta, "web_search_queries") or []) if q)
            supports += len(_get(meta, "grounding_supports") or [])
            search_entry_point = search_entry_point or _get(meta, "search_entry_point") is not None
            for chunk in _get(meta, "grounding_chunks") or []:
                web = _get(chunk, "web")
                if web is None:
                    continue
                chunks += 1
                uri, title, domain = _get(web, "uri"), _get(web, "title"), _get(web, "domain")
                if uri or title or domain:
                    add({"kind": KIND_CHUNK, "uri": uri, "title": (title or "")[:160], "domain": domain})
        ucm = _get(cand, "url_context_metadata")
        for item in _get(ucm, "url_metadata") or []:
            url_context_total += 1
            status = str(_enum_value(_get(item, "url_retrieval_status")) or "")
            retrieved = _get(item, "retrieved_url")
            ok = status.endswith("SUCCESS")
            url_context_ok += int(ok)
            if retrieved:
                add({"kind": KIND_URL_CONTEXT, "uri": None, "title": "", "domain": None,
                     "retrieved_url": retrieved, "status": status or None, "ok": ok})
        for cit in _get(_get(cand, "citation_metadata"), "citations") or []:
            uri = _get(cit, "uri")
            if isinstance(uri, str) and uri.startswith("https://"):
                citations += 1
                add({"kind": KIND_CITATION, "uri": uri, "title": (_get(cit, "title") or "")[:160], "domain": None})

    stats = {
        "grounding_metadata_present": metadata_present,
        "web_search_query_count": len(queries),
        "grounding_chunk_count": chunks,
        "grounding_support_count": supports,
        "search_entry_point_present": search_entry_point,
        "url_context_count": url_context_total,
        "url_context_success_count": url_context_ok,
        "citation_count": citations,
    }
    return {"sources": sources, "stats": stats}


# ---------------------------------------------------------------------------
# redirect resolution (header only, Google's endpoint only)
# ---------------------------------------------------------------------------
def _default_location_fetcher(uri: str, timeout: float) -> Optional[str]:
    import httpx

    with httpx.Client(follow_redirects=False, timeout=timeout) as client:
        with client.stream("GET", uri) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                return resp.headers.get("location")
    return None


def resolve_grounding_redirects(
    sources: List[Dict[str, Any]],
    *,
    fetch_location: Optional[Callable[[str, float], Optional[str]]] = None,
    request_timeout: float = REDIRECT_REQUEST_TIMEOUT_SEC,
    total_budget: float = REDIRECT_TOTAL_BUDGET_SEC,
    max_resolved: int = MAX_REDIRECTS_RESOLVED,
) -> Dict[str, Any]:
    """Annotate grounding chunks with ``retrieved_url`` (the redirect target).

    Only ``https://vertexaisearch.cloud.google.com/grounding-api-redirect/…``
    URIs are requested, redirects are never followed and the target page is
    never fetched. Failures leave the chunk unresolved (title evidence still
    applies). Returns resolution stats.
    """
    fetch = fetch_location or _default_location_fetcher
    todo: Dict[str, List[Dict[str, Any]]] = {}
    for src in sources:
        if src.get("kind") in (None, KIND_CHUNK) and is_grounding_redirect(src.get("uri")) and not src.get("retrieved_url"):
            todo.setdefault(src["uri"], []).append(src)
    uris = list(todo)[:max_resolved]
    stats = {"redirects_seen": len(todo), "redirects_attempted": len(uris), "redirects_resolved": 0, "redirect_errors": 0}
    if not uris:
        return stats
    started = time.monotonic()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(uris)))
    try:
        futures = {pool.submit(fetch, uri, request_timeout): uri for uri in uris}
        try:
            for future in concurrent.futures.as_completed(futures, timeout=total_budget):
                uri = futures[future]
                try:
                    location = future.result()
                except Exception:
                    stats["redirect_errors"] += 1
                    continue
                target = normalize_url_for_match(location)
                if not target:
                    stats["redirect_errors"] += 1
                    continue
                stats["redirects_resolved"] += 1
                for src in todo[uri]:
                    src["retrieved_url"] = target
        except concurrent.futures.TimeoutError:
            stats["redirect_errors"] += sum(1 for f in futures if not f.done())
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    stats["redirect_ms"] = int((time.monotonic() - started) * 1000)
    return stats


# ---------------------------------------------------------------------------
# correlation index
# ---------------------------------------------------------------------------
class GroundingIndex:
    """Official-registry view of the grounding evidence for one manufacturer."""

    def __init__(self, manufacturer: str, sources: Iterable[Dict[str, Any]]):
        self.manufacturer = manufacturer
        self.urls: Dict[str, str] = {}   # normalized url -> host
        self.hosts: Dict[str, str] = {}  # host -> strongest evidence kind
        self.sites: Dict[str, str] = {}  # site -> one grounded host
        self.ignored: List[str] = []
        for src in sources or []:
            if src.get("kind") == KIND_CITATION:
                continue
            if src.get("kind") == KIND_URL_CONTEXT and not src.get("ok"):
                continue
            matched_any = False
            seen_any = False
            url = src.get("retrieved_url")
            if not url and src.get("uri") and not is_grounding_redirect(src.get("uri")):
                url = src.get("uri")  # a direct (non-redirect) URL from Google
            norm = normalize_url_for_match(url)
            if norm:
                seen_any = True
                host = urlsplit(norm).hostname
                if self._add_host(host, src.get("kind")):
                    self.urls[norm] = host
                    matched_any = True
            for key in ("domain", "title"):
                host = bare_hostname(src.get(key))
                if host:
                    seen_any = True
                    matched_any = self._add_host(host, src.get("kind")) or matched_any
            if seen_any and not matched_any:
                ignored = _url_host(url) or bare_hostname(src.get("domain")) or bare_hostname(src.get("title"))
                if ignored and ignored not in self.ignored and len(self.ignored) < 20:
                    self.ignored.append(ignored)

    def _add_host(self, host: Optional[str], kind: Optional[str]) -> bool:
        if not host or not classify_host(self.manufacturer, host):
            return False
        self.hosts.setdefault(host, kind or KIND_CHUNK)
        site = registry_site(self.manufacturer, host)
        if site:
            self.sites.setdefault(site, host)
        return True

    @property
    def official_hosts(self) -> List[str]:
        return sorted(self.hosts)

    @property
    def official_markets(self) -> List[str]:
        """Registry markets (IL / GLOBAL) of the official hosts Google retrieved."""
        markets = {(classify_host(self.manufacturer, h) or {}).get("market") for h in self.hosts}
        return sorted(m for m in markets if m)

    def correlate(self, claim_url: Any) -> Tuple[Optional[str], Optional[str]]:
        """(tier, grounded_host) for an ALLOWLISTED claim URL, else (None, None)."""
        verdict = check_official_url(self.manufacturer, claim_url)
        if not verdict.get("allowed"):
            return None, None
        norm = normalize_url_for_match(claim_url)
        if norm and norm in self.urls:
            return TIER_URL, self.urls[norm]
        host = verdict.get("host")
        if host in self.hosts:
            return TIER_HOST, host
        site = verdict.get("site")
        if site and site in self.sites:
            return TIER_SITE, self.sites[site]
        return None, None

    def accepts(self, tier: Optional[str], minimum: Optional[str] = None) -> bool:
        if tier is None:
            return False
        minimum = minimum or min_tier()
        return TIER_ORDER.index(tier) <= TIER_ORDER.index(minimum)

    def summary(self) -> Dict[str, Any]:
        return {
            "official_hosts": self.official_hosts,
            "official_sites": sorted(self.sites),
            "official_url_count": len(self.urls),
            "ignored_hosts": list(self.ignored),
        }
