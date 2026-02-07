# -*- coding: utf-8 -*-
"""Tests for canonicalize_line_items quantity parsing."""

from app.services.service_prices_service import canonicalize_line_items


def test_canonicalize_line_items_parses_qty_strings():
    line_items = [
        {"description": "שמן מנוע", "price_ils": 100, "qty": "2"},
        {"description": "שמן מנוע", "price_ils": 50, "qty": "x3"},
        {"description": "שמן מנוע", "price_ils": 20, "qty": None},
    ]

    result = canonicalize_line_items(line_items)

    assert len(result) == 1
    entry = result[0]
    assert entry["qty"] == 6
    assert isinstance(entry["qty"], int)
