from pathlib import Path

from app.legal import (
    COMPARE_RESULT_ACK_KEY,
    COMPARE_RESULT_ACK_VERSION,
)
from app.models import LegalFeatureAcceptance


ROOT = Path(__file__).resolve().parents[1]


def test_terms_jurisdiction_placeholder_removed_and_has_default_clause():
    terms_html = (ROOT / "templates" / "terms.html").read_text(encoding="utf-8")
    assert "[INSERT_ISRAELI_COURT_JURISDICTION_AFTER_LEGAL_REVIEW]" not in terms_html
    assert "בתי המשפט המוסמכים בתל אביב-יפו" in terms_html
    assert "צריך להיבדק ולאושר על-ידי עורך דין ישראלי" in terms_html


def test_result_templates_include_prominent_hardening_disclaimers():
    reliability_html = (ROOT / "templates" / "reliability_app.html").read_text(encoding="utf-8")
    compare_html = (ROOT / "templates" / "compare.html").read_text(encoding="utf-8")
    recommendations_html = (ROOT / "templates" / "recommendations.html").read_text(encoding="utf-8")
    service_prices_html = (ROOT / "templates" / "service_prices.html").read_text(encoding="utf-8")
    service_prices_report_html = (ROOT / "templates" / "service_prices_report.html").read_text(encoding="utf-8")

    assert "דיסקליימר חשוב" in reliability_html
    assert "אינה ייעוץ מקצועי, בדיקה מכנית, או חוות דעת שמאית" in reliability_html

    assert "דיסקליימר חשוב" in compare_html
    assert "אינה ייעוץ מקצועי, בדיקה מכנית או חוות דעת שמאית" in compare_html

    assert "דיסקליימר חשוב" in recommendations_html
    assert "ואינן ייעוץ מקצועי, בדיקה מכנית או חוות דעת שמאית" in recommendations_html

    assert "אינו הוכחה לחיוב יתר" in service_prices_html
    assert "אינו האשמה כלפי מוסך מסוים" in service_prices_html

    assert "דוח זה הוא כלי תומך החלטה בלבד" in service_prices_report_html
    assert "אינו האשמה נגד מוסך מסוים" in service_prices_report_html


def test_sensitive_flows_contain_result_acknowledgement_gating_hooks():
    compare_html = (ROOT / "templates" / "compare.html").read_text(encoding="utf-8")
    reliability_js = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
    service_prices_html = (ROOT / "templates" / "service_prices.html").read_text(encoding="utf-8")

    assert "if (!ensureCompareResultAcknowledgement(options)) return;" in compare_html
    assert "if (!ensureReliabilityResultAcknowledgement(options)) return;" in reliability_js
    assert "if (ensureServicePricesResultAcknowledgement(currentReport))" in service_prices_html


def test_compare_result_acknowledgement_saved_and_reflected(logged_in_client, app):
    client, user_id = logged_in_client

    before = client.get("/compare")
    assert before.status_code == 200
    assert "const compareResultAckAcceptedInitial = false;" in before.data.decode("utf-8")

    resp = client.post(
        "/api/legal/accept",
        json={
            "legal_confirm": True,
            "feature_consents": [
                {
                    "feature_key": COMPARE_RESULT_ACK_KEY,
                    "version": COMPARE_RESULT_ACK_VERSION,
                }
            ],
        },
    )
    assert resp.status_code == 200

    with app.app_context():
        saved = LegalFeatureAcceptance.query.filter_by(
            user_id=user_id,
            feature_key=COMPARE_RESULT_ACK_KEY,
            version=COMPARE_RESULT_ACK_VERSION,
        ).first()
        assert saved is not None

    after = client.get("/compare")
    assert after.status_code == 200
    assert "const compareResultAckAcceptedInitial = true;" in after.data.decode("utf-8")
