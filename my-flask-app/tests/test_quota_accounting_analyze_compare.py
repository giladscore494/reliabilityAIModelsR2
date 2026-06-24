from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_analyze_non_cache_finalizes_quota_and_logs_bypass_reason():
    src = (ROOT / "app" / "services" / "analyze_service.py").read_text(encoding="utf-8")
    assert "cache_hit = False" in src
    assert "finalize_quota_reservation" in src
    assert "quota_bypass_reason={'owner/admin' if bypass_owner else 'none'}" in src


def test_compare_non_cache_finalizes_quota_and_logs_bypass_reason():
    src = (ROOT / "app" / "routes" / "comparison_routes.py").read_text(encoding="utf-8")
    assert "finalize_quota_reservation" in src
    assert "quota_bypass_reason=%s" in src
    assert "owner/admin" in src
