# -*- coding: utf-8 -*-


def test_compare_page_uses_general_transmission_labels(client):
    resp = client.get("/compare")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "רובוטית (DSG)" not in html
    assert "רובוטית (כפולת מצמדים)" not in html
    assert "אוטומטית (פלנטרית/רציפה)" not in html
    assert "רובוטית" in html
    assert "רציפה" in html
    assert "לא ידוע / לבדיקה" in html
