# -*- coding: utf-8 -*-
"""Official source registry for Level 2 enrichment.

This is the ONLY place where official domains live. Prompts render the
allowlist from here, and every URL the model cites is re-checked here in code:
a prompt instruction is not a security boundary.

Matching rules (``is_allowed_official_url``):
* https only, no userinfo, no explicit port, no IP literals;
* the hostname must equal an allowed host, or be a subdomain of an allowed
  host that is explicitly marked ``subdomains=True`` (so ``www.bmw.co.il``
  passes while ``bmw.co.il.evil.com`` and ``evilbmw.co.il`` do not).
"""

from __future__ import annotations

import ipaddress
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

SOURCE_REGISTRY_VERSION = "official-source-registry/1"

MARKET_IL = "IL"
MARKET_GLOBAL = "GLOBAL"

REASON_DOMAIN_NOT_ALLOWED = "SOURCE_DOMAIN_NOT_ALLOWED"
REASON_URL_INVALID = "SOURCE_URL_INVALID"
REASON_MANUFACTURER_UNKNOWN = "MANUFACTURER_NOT_IN_REGISTRY"


def _host(host: str, *, subdomains: bool) -> Dict[str, Any]:
    return {"host": host, "subdomains": subdomains}


# Keys are the Level 1.5 (Ministry of Transport) manufacturer names exactly as
# MILO stores them. ``display`` is presentation only.
#
# ``seed_urls`` are discovery starting points rendered into the enrichment
# prompt. They are NOT proof that a fact applies to a variant and they do not
# widen anything: every returned claim must still pass the host allowlist
# below, grounding corroboration, the variant matcher, market validation and
# the field validator. ``discovery_notes`` are per-brand guidance for the
# extractor (prompt text only).
OFFICIAL_SOURCE_REGISTRY: Dict[str, Dict[str, Any]] = {
    "אאודי": {
        "display": "Audi",
        "israel": [_host("audi.co.il", subdomains=True), _host("campaign.audi.co.il", subdomains=False)],
        "manufacturer": [
            _host("audi.com", subdomains=True),
            _host("media.audi.com", subdomains=False),
            _host("audi-mediacenter.com", subdomains=True),
            _host("uploads.audi-mediacenter.com", subdomains=False),
        ],
        "seed_urls": [
            "https://www.audi.co.il/",
            "https://www.audi.com/en/audi-q3-57",
            "https://uploads.audi-mediacenter.com/system/production/car_motorizations/1244/file_en/ceabc2b0fd48a749c10ea1fd2c8552b20f96e0c7/eTD-Audi-Q3-40-TFSI-quattro-S_tronic-140kW_250108.pdf",
        ],
        "discovery_notes": [
            "audi.com/en/audi-q3-57 is the previous-generation Q3 (until 2025); prefer it over the new-generation Q3 page for a 2024 vehicle.",
        ],
    },
    "ב מ וו": {
        "display": "BMW",
        "israel": [_host("bmw.co.il", subdomains=True)],
        "manufacturer": [_host("bmw.com", subdomains=True), _host("bmw.scene7.com", subdomains=False)],
        "seed_urls": [
            "https://www.bmw.co.il/he/All-Models.html",
            "https://www.bmw.co.il/he/all-models/i-series/i4/i4-gran-coupe-2024-g26bev-technical-data.html",
        ],
        "discovery_notes": [
            "The i4 Gran Coupe 2024 technical-data page carries technical data, battery, charging, range, dimensions and cargo for i4 variants; take only values stated for the exact variant.",
        ],
    },
    "יונדאי": {
        "display": "Hyundai",
        "israel": [
            _host("hyundaimotors.co.il", subdomains=True),
            _host("campaigns.hyundaimotors.co.il", subdomains=False),
        ],
        "manufacturer": [_host("hyundai.com", subdomains=True), _host("dmassets.hyundai.com", subdomains=False)],
        "seed_urls": [
            "https://www.hyundaimotors.co.il/models/tucson-hybrid",
            "https://www.hyundaimotors.co.il/prices/",
            "https://campaigns.hyundaimotors.co.il/catalogue/tucson_hybrid_catalogue.pdf",
        ],
        "discovery_notes": [
            "Prefer the Israeli Tucson Hybrid model page and the Israeli catalogue over foreign Hyundai markets.",
        ],
    },
    "אקספנג": {
        "display": "XPENG",
        "israel": [
            _host("heyxpeng.co.il", subdomains=True),
            _host("media.heyxpeng.co.il", subdomains=False),
            _host("campaigns.heyxpeng.co.il", subdomains=False),
        ],
        "manufacturer": [_host("xpeng.com", subdomains=True), _host("s-cdn.xpeng.com", subdomains=False)],
        "seed_urls": [
            "https://heyxpeng.co.il/pricing/",
            "https://heyxpeng.co.il/service/",
            "https://campaigns.heyxpeng.co.il/?car=P7",
            "https://www.xpeng.com/",
        ],
        "discovery_notes": [
            "The current Israeli site may no longer show the historical P7i Wing Edition page. Never substitute current P7 / P7+ data for a 2023 P7i Wing Edition; if a page does not establish that a value belongs to the Wing Edition AWD, omit it.",
        ],
    },
    "טויוטה": {
        "display": "Toyota",
        "israel": [_host("toyota.co.il", subdomains=True)],
        "manufacturer": [
            _host("toyota.com", subdomains=True),
            _host("pressroom.toyota.com", subdomains=False),
            _host("global.toyota", subdomains=False),
            _host("toyota-europe.com", subdomains=True),
        ],
        "seed_urls": [
            "https://www.toyota.co.il/new-cars",
            "https://www.toyota.co.il/owners/warranty",
            "https://pressroom.toyota.com/toyota-marks-25th-anniversary-of-sienna-with-special-limited-edition/",
            "https://www.toyota.com/content/dam/toyota/toyota-fleet-vehicles/pdf/2023_Fleet_Guide.pdf",
        ],
        "discovery_notes": [
            "A 2023 Sienna is not a normal current Israeli-market listing: use official Toyota USA historical sources for technical data only.",
            "Do not report an Israeli price or Israeli warranty for the Sienna unless an Israeli official page states it for this exact variant.",
            "A generic Sienna specification is model_generic unless the page ties it to the exact trim and powertrain.",
        ],
    },
    "מרצדס": {
        "display": "Mercedes-Benz",
        "israel": [_host("mercedes-benz.co.il", subdomains=True)],
        "manufacturer": [_host("mercedes-benz.com", subdomains=True), _host("media.mercedes-benz.com", subdomains=False)],
        "seed_urls": [
            "https://www.mercedes-benz.co.il/models/cle-coupe/",
            "https://www.mercedes-benz.co.il/models/",
            "https://www.mercedes-benz.co.il/services/customer-service/",
        ],
        "discovery_notes": [
            "For the CLE start with the Israeli CLE coupe page: it carries Israeli trim, pricing and technical information for CLE 300 4MATIC variants.",
        ],
    },
    "קאדילאק": {
        "display": "Cadillac",
        "israel": [_host("cadillac.co.il", subdomains=True)],
        "manufacturer": [_host("cadillac.com", subdomains=True), _host("news.cadillac.com", subdomains=False)],
        "seed_urls": [
            "https://www.cadillac.co.il/ESCALADE-IQ/",
            "https://www.cadillac.co.il/קטלוג-קאדילק/",
            "https://www.cadillac.co.il/שירות-קאדילק/מחירון-דגמים/",
            "https://www.cadillac.co.il/שירות-קאדילק/הרחבת-אחריות/",
            "https://www.cadillac.com/electric/escalade-iq",
            "https://news.cadillac.com/newsroom.detail.html/Pages/news/us/en/2023/aug/0809-escaladeiq.html",
        ],
        "discovery_notes": [
            "Israeli price and warranty only from the Israeli Cadillac pages. Global Cadillac pages may supplement battery, charging, dimensions and performance.",
        ],
    },
}

# What each source market may contribute (prompt guidance; enforcement of
# Israeli-only fields lives in field_registry/field_validator).
ISRAELI_PRIORITY_TOPICS = ("trim/configuration", "Israeli equipment", "price", "registration fee", "local warranty")
GLOBAL_SUPPLEMENT_TOPICS = ("torque", "acceleration", "top speed", "dimensions", "battery", "range", "charging", "technical specifications")


def seed_urls(manufacturer: str) -> List[str]:
    entry = OFFICIAL_SOURCE_REGISTRY.get((manufacturer or "").strip())
    return list(entry.get("seed_urls") or []) if entry else []


def discovery_notes(manufacturer: str) -> List[str]:
    entry = OFFICIAL_SOURCE_REGISTRY.get((manufacturer or "").strip())
    return list(entry.get("discovery_notes") or []) if entry else []


def brand_display(manufacturer: str) -> str:
    entry = OFFICIAL_SOURCE_REGISTRY.get((manufacturer or "").strip())
    return entry["display"] if entry else (manufacturer or "")


def allowed_hosts(manufacturer: str) -> Dict[str, List[str]]:
    """Plain host lists per market, for rendering into the enrichment prompt."""
    entry = OFFICIAL_SOURCE_REGISTRY.get((manufacturer or "").strip())
    if not entry:
        return {MARKET_IL: [], MARKET_GLOBAL: []}
    return {
        MARKET_IL: [h["host"] for h in entry["israel"]],
        MARKET_GLOBAL: [h["host"] for h in entry["manufacturer"]],
    }


def normalize_hostname(raw_host: Optional[str]) -> Optional[str]:
    """Lowercase, strip one trailing dot, IDNA-encode; None when unusable."""
    if not raw_host:
        return None
    host = raw_host.strip().lower()
    if host.endswith("."):
        host = host[:-1]
    if not host or ".." in host or any(ch in host for ch in "/\\@:%?# "):
        return None
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    try:
        ipaddress.ip_address(host)
        return None  # IP literals are never official sources
    except ValueError:
        pass
    return host


def _parse_url_host(url: Any) -> Tuple[Optional[str], Optional[str]]:
    """Return (hostname, error_reason)."""
    if not isinstance(url, str) or not url.strip() or len(url) > 2048:
        return None, REASON_URL_INVALID
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None, REASON_URL_INVALID
    if parts.scheme.lower() != "https":
        return None, REASON_URL_INVALID
    netloc = parts.netloc
    if not netloc or "@" in netloc:
        return None, REASON_URL_INVALID
    try:
        if parts.port is not None:
            return None, REASON_URL_INVALID
    except ValueError:
        return None, REASON_URL_INVALID
    host = normalize_hostname(parts.hostname)
    if not host:
        return None, REASON_URL_INVALID
    return host, None


def _entry_matches(host: str, entry: Dict[str, Any]) -> bool:
    allowed = entry["host"]
    if host == allowed:
        return True
    return bool(entry["subdomains"]) and host.endswith("." + allowed)


def classify_host(manufacturer: str, host: Optional[str]) -> Optional[Dict[str, str]]:
    """Return {market, source_type, registry_host} for an allowed host, else None.

    The most specific matching entry wins (``campaign.audi.co.il`` before
    ``audi.co.il``), so two hosts classify to the same registry entry only
    when they really belong to it.
    """
    entry = OFFICIAL_SOURCE_REGISTRY.get((manufacturer or "").strip())
    host = normalize_hostname(host) if host else None
    if not entry or not host:
        return None
    best: Optional[Dict[str, str]] = None
    for market, key, source_type in (
        (MARKET_IL, "israel", "official_importer"),
        (MARKET_GLOBAL, "manufacturer", "manufacturer"),
    ):
        for host_entry in entry[key]:
            if _entry_matches(host, host_entry):
                if best is None or len(host_entry["host"]) > len(best["registry_host"]):
                    best = {"market": market, "source_type": source_type, "registry_host": host_entry["host"]}
    return best


def registry_site(manufacturer: str, host: Optional[str]) -> Optional[str]:
    """The official *site* an allowed host belongs to, else None.

    The site is the broadest ``subdomains=True`` registry entry covering the
    host (``uploads.audi-mediacenter.com`` and ``audi-mediacenter.com`` are one
    site); a host covered only by exact entries is its own site
    (``bmw.scene7.com``). Used to correlate a cited URL with grounding
    evidence: Google Search grounding usually names the site, not the exact
    subdomain the model cites.
    """
    entry = OFFICIAL_SOURCE_REGISTRY.get((manufacturer or "").strip())
    host = normalize_hostname(host) if host else None
    if not entry or not host:
        return None
    roots: List[str] = []
    exact: List[str] = []
    for key in ("israel", "manufacturer"):
        for host_entry in entry[key]:
            if _entry_matches(host, host_entry):
                (roots if host_entry["subdomains"] else exact).append(host_entry["host"])
    if roots:
        return min(roots, key=len)
    if exact:
        return max(exact, key=len)
    return None


def check_official_url(manufacturer: str, url: Any) -> Dict[str, Any]:
    """Full verdict for one URL: {allowed, reason, host, site, market, source_type, registry_host}."""
    if (manufacturer or "").strip() not in OFFICIAL_SOURCE_REGISTRY:
        return {"allowed": False, "reason": REASON_MANUFACTURER_UNKNOWN, "host": None}
    host, err = _parse_url_host(url)
    if err:
        return {"allowed": False, "reason": err, "host": None}
    info = classify_host(manufacturer, host)
    if not info:
        return {"allowed": False, "reason": REASON_DOMAIN_NOT_ALLOWED, "host": host}
    return {"allowed": True, "reason": None, "host": host, "site": registry_site(manufacturer, host), **info}


def is_allowed_official_url(manufacturer: str, url: Any) -> bool:
    return bool(check_official_url(manufacturer, url)["allowed"])


def registry_snapshot() -> Dict[str, Any]:
    """Serializable view of the registry (docs / diagnostics)."""
    return {
        "version": SOURCE_REGISTRY_VERSION,
        "manufacturers": {
            name: {
                "display": entry["display"],
                "israel": [dict(h) for h in entry["israel"]],
                "manufacturer": [dict(h) for h in entry["manufacturer"]],
                "seed_urls": list(entry.get("seed_urls") or []),
            }
            for name, entry in OFFICIAL_SOURCE_REGISTRY.items()
        },
    }
