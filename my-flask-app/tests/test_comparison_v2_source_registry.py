# -*- coding: utf-8 -*-
import pytest

from app.services.comparison_v2.source_registry import (
    OFFICIAL_SOURCE_REGISTRY,
    SOURCE_REGISTRY_VERSION,
    allowed_hosts,
    check_official_url,
    is_allowed_official_url,
)

CASES = {
    "ב מ וו": ("bmw.co.il", "bmw.com"),
    "אאודי": ("audi.co.il", "audi.com"),
    "יונדאי": ("hyundaimotors.co.il", "hyundai.com"),
    "אקספנג": ("heyxpeng.co.il", "xpeng.com"),
    "טויוטה": ("toyota.co.il", "toyota-europe.com"),
    "מרצדס": ("mercedes-benz.co.il", "mercedes-benz.com"),
    "קאדילאק": ("cadillac.co.il", "cadillac.com"),
}


def test_registry_version_and_brands():
    assert SOURCE_REGISTRY_VERSION == "official-source-registry/1"
    assert set(CASES) == set(OFFICIAL_SOURCE_REGISTRY)


def test_bmw_examples_from_spec():
    assert is_allowed_official_url("ב מ וו", "https://www.bmw.co.il/he/index.html")
    assert is_allowed_official_url("ב מ וו", "https://sub.bmw.co.il/x")
    assert not is_allowed_official_url("ב מ וו", "https://bmw.co.il.evil.com/x")
    assert not is_allowed_official_url("ב מ וו", "https://car-review.example/bmw")


@pytest.mark.parametrize("manufacturer,hosts", CASES.items())
def test_each_manufacturer_allow_and_reject(manufacturer, hosts):
    il, glob = hosts
    ok_il = check_official_url(manufacturer, f"https://www.{il}/models")
    assert ok_il["allowed"] and ok_il["market"] == "IL" and ok_il["source_type"] == "official_importer"
    ok_gl = check_official_url(manufacturer, f"https://www.{glob}/models")
    assert ok_gl["allowed"] and ok_gl["market"] == "GLOBAL" and ok_gl["source_type"] == "manufacturer"
    for bad in (
        f"https://{il}.evil.com/x",
        f"https://evil{il}/x",
        f"https://{glob}.attacker.net/x",
        "https://www.yad2.co.il/vehicles",
        "https://www.carzone.co.il/x",
        f"http://www.{il}/insecure",
        f"https://user@{il}/x",
        f"https://{il}:8443/x",
        "javascript:alert(1)",
        "",
        None,
    ):
        assert not is_allowed_official_url(manufacturer, bad), bad


def test_exact_only_hosts_do_not_allow_subdomains():
    assert is_allowed_official_url("ב מ וו", "https://bmw.scene7.com/is/image/x")
    assert not is_allowed_official_url("ב מ וו", "https://evil.bmw.scene7.com/x")
    assert not is_allowed_official_url("ב מ וו", "https://scene7.com/x")
    assert is_allowed_official_url("טויוטה", "https://global.toyota/en/newsroom")
    assert not is_allowed_official_url("טויוטה", "https://x.global.toyota/en")


def test_brand_domains_do_not_cross():
    assert not is_allowed_official_url("אאודי", "https://www.bmw.co.il/x")
    assert not is_allowed_official_url("ב מ וו", "https://www.audi.co.il/x")


def test_unknown_manufacturer_rejected():
    assert check_official_url("פולקסווגן", "https://www.vw.co.il")["reason"] == "MANUFACTURER_NOT_IN_REGISTRY"


def test_ip_literal_and_trailing_dot():
    assert not is_allowed_official_url("ב מ וו", "https://192.168.0.1/x")
    assert is_allowed_official_url("ב מ וו", "https://www.bmw.co.il./x")


def test_prompt_hosts_rendered_from_registry():
    hosts = allowed_hosts("אקספנג")
    assert "heyxpeng.co.il" in hosts["IL"] and "xpeng.com" in hosts["GLOBAL"]


# --- seed URLs (discovery starting points; enforcement unchanged) ---------
from app.services.comparison_v2.source_registry import discovery_notes, seed_urls  # noqa: E402


def test_every_seed_url_passes_its_own_brand_allowlist_only():
    for manufacturer, entry in OFFICIAL_SOURCE_REGISTRY.items():
        assert entry["seed_urls"], manufacturer
        for url in entry["seed_urls"]:
            assert is_allowed_official_url(manufacturer, url), url
            for other in OFFICIAL_SOURCE_REGISTRY:
                if other != manufacturer:
                    assert not is_allowed_official_url(other, url), (other, url)


def test_new_official_hosts_and_markets():
    assert check_official_url("אאודי", "https://uploads.audi-mediacenter.com/x.pdf")["market"] == "GLOBAL"
    assert check_official_url("טויוטה", "https://pressroom.toyota.com/sienna")["market"] == "GLOBAL"
    assert check_official_url("טויוטה", "https://www.toyota.com/sienna")["market"] == "GLOBAL"
    assert check_official_url("יונדאי", "https://campaigns.hyundaimotors.co.il/c.pdf")["market"] == "IL"
    assert check_official_url("קאדילאק", "https://news.cadillac.com/x")["market"] == "GLOBAL"
    assert check_official_url("אקספנג", "https://s-cdn.xpeng.com/x.png")["market"] == "GLOBAL"
    assert not is_allowed_official_url("אאודי", "https://uploads.audi-mediacenter.com.evil.io/x.pdf")


def test_seed_helpers():
    assert "https://www.audi.com/en/audi-q3-57" in seed_urls("אאודי")
    assert any("P7" in n for n in discovery_notes("אקספנג"))
    assert seed_urls("unknown") == []
