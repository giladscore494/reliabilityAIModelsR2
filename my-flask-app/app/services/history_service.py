# -*- coding: utf-8 -*-
"""History service - handles search history retrieval and comparison logic."""

from typing import List, Dict, Any, Optional
import json
from flask import current_app


def get_user_history_list(user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Get list of user's search history.
    
    Args:
        user_id: User ID to fetch history for
        limit: Maximum number of results to return
        
    Returns:
        List of history items with basic metadata
    """
    from app.models import SearchHistory
    
    searches = SearchHistory.query.filter_by(
        user_id=user_id
    ).order_by(SearchHistory.timestamp.desc()).limit(limit).all()
    
    history_items = []
    for s in searches:
        history_items.append({
            'id': s.id,
            'timestamp': s.timestamp.isoformat(),
            'make': s.make,
            'model': s.model,
            'year': s.year,
            'mileage_range': s.mileage_range,
            'fuel_type': s.fuel_type,
            'transmission': s.transmission,
            'display_name': f"{s.make} {s.model} {s.year}"
        })
    
    return history_items


def get_history_item(user_id: int, item_id: int) -> Optional[Dict[str, Any]]:
    """
    Get a specific search history item.
    
    Args:
        user_id: User ID (for authorization)
        item_id: History item ID
        
    Returns:
        History item with full result data, or None if not found
    """
    from app.models import SearchHistory
    
    search = SearchHistory.query.filter_by(
        id=item_id,
        user_id=user_id
    ).first()
    
    if not search:
        return None
    
    result_data = json.loads(search.result_json) if search.result_json else {}
    
    return {
        'id': search.id,
        'timestamp': search.timestamp.isoformat(),
        'make': search.make,
        'model': search.model,
        'year': search.year,
        'mileage_range': search.mileage_range,
        'fuel_type': search.fuel_type,
        'transmission': s.transmission,
        'result': result_data
    }


# Hebrew labels mapping for UI
HEBREW_LABELS = {
    # Score fields
    'base_score_calculated': 'ציון אמינות כללי',
    'reliability_score': 'ציון אמינות',
    'avg_repair_cost_ILS': 'עלות תיקון ממוצעת (₪)',
    
    # Score breakdown
    'engine_transmission_score': 'מנוע ותיבת הילוכים',
    'electrical_score': 'חשמל ואלקטרוניקה',
    'suspension_brakes_score': 'מתלים ובלמים',
    'maintenance_cost_score': 'עלויות תחזוקה',
    'satisfaction_score': 'שביעות רצון',
    'recalls_score': 'זכורות וכשלים',
    
    # Vehicle specs
    'make': 'יצרן',
    'model': 'דגם',
    'year': 'שנה',
    'mileage_range': 'טווח קילומטראז\'',
    'fuel_type': 'סוג דלק',
    'transmission': 'תיבת הילוכים',
    
    # Analysis sections
    'reliability_summary': 'סיכום אמינות',
    'common_issues': 'תקלות נפוצות',
    'recommended_checks': 'בדיקות מומלצות',
    'issues_with_costs': 'תקלות ועלויות',
    'common_competitors_brief': 'רכבים מתחרים',
}


def get_hebrew_label(key: str) -> str:
    """Get Hebrew label for a field key."""
    return HEBREW_LABELS.get(key, key)
