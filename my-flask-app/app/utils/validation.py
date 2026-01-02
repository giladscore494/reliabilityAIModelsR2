# -*- coding: utf-8 -*-
"""
Input validation module for Car Reliability Analyzer.
Provides centralized validation with strict rules to prevent:
- Prompt injection attacks
- DoS via oversized payloads
- Invalid input reaching the AI model
"""


class ValidationError(Exception):
    """Custom exception for validation errors with field-specific details."""
    def __init__(self, field, message):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


def validate_analyze_request(data):
    """
    Validate /analyze POST payload.
    Raises ValidationError if invalid.
    Returns cleaned dict if valid.
    
    Args:
        data: Dictionary with request payload
        
    Returns:
        dict: Validated and cleaned data
        
    Raises:
        ValidationError: If validation fails
    """
    # Strict field allowlist
    ALLOWED_FIELDS = {
        'budget_min', 'budget_max', 'year_min', 'year_max',
        'fuels', 'gears', 'turbo', 'body_style', 'seats',
        'trim_level', 'annual_km', 'driver_age', 'license_years',
        'family_size', 'cargo_need', 'driver_gender',
        'main_use', 'driving_style', 'safety_required',
        'consider_supply', 'excluded_colors',
        'insurance_history', 'violations',
        'fuel_price', 'electricity_price',
        'w_reliability', 'w_resale', 'w_fuel', 'w_performance', 'w_comfort',
        # Fields from /analyze endpoint
        'make', 'model', 'sub_model', 'year', 'mileage_range', 'fuel_type', 'transmission',
        # Fields from /advisor_api endpoint
        'fuels_he', 'gears_he', 'turbo_choice_he', 'weights', 'seats_choice'
    }
    
    # Reject unknown fields
    for key in data.keys():
        if key not in ALLOWED_FIELDS:
            raise ValidationError(key, f"Unknown field (not in allowlist)")
    
    # Validate numeric ranges
    if 'budget_min' in data:
        val = data['budget_min']
        if not isinstance(val, (int, float)) or val < 0 or val > 500000:
            raise ValidationError('budget_min', 'Must be 0–500000')
    
    if 'budget_max' in data:
        val = data['budget_max']
        if not isinstance(val, (int, float)) or val < 0 or val > 500000:
            raise ValidationError('budget_max', 'Must be 0–500000')
    
    if 'year_min' in data:
        val = data['year_min']
        if not isinstance(val, int) or val < 1995 or val > 2025:
            raise ValidationError('year_min', 'Must be 1995–2025')
    
    if 'year_max' in data:
        val = data['year_max']
        if not isinstance(val, int) or val < 1995 or val > 2025:
            raise ValidationError('year_max', 'Must be 1995–2025')
    
    if 'year' in data:
        val = data['year']
        if val is not None and (not isinstance(val, int) or val < 1995 or val > 2025):
            raise ValidationError('year', 'Must be 1995–2025')
    
    # Validate string fields (length, character set)
    TEXT_FIELDS = {
        'main_use': 500,
        'driving_style': 100,
        'insurance_history': 300,
        'excluded_colors': 100,
        'make': 100,
        'model': 100,
        'sub_model': 100,
        'mileage_range': 100,
        'fuel_type': 100,
        'transmission': 100,
        'body_style': 100,
        'trim_level': 100,
        'family_size': 50,
        'cargo_need': 100,
        'safety_required': 50,
        'driver_gender': 50,
        'consider_supply': 50,
        'violations': 300,
        'seats': 50,
        'seats_choice': 50,
        'turbo': 50,
        'turbo_choice_he': 50,
    }
    
    for field, max_len in TEXT_FIELDS.items():
        if field in data:
            val = data[field]
            if val is None:
                continue
            if not isinstance(val, str):
                raise ValidationError(field, f'Must be string')
            if len(val) > max_len:
                raise ValidationError(field, f'Exceeds {max_len} characters')
            # Check for injection tokens
            if _contains_injection_tokens(val):
                raise ValidationError(field, 'Contains control tokens (SYSTEM:, ASSISTANT:, backticks). Not allowed.')
    
    # Validate list fields
    LIST_FIELDS = ['fuels', 'gears', 'fuels_he', 'gears_he', 'excluded_colors']
    for field in LIST_FIELDS:
        if field in data:
            val = data[field]
            if not isinstance(val, list):
                raise ValidationError(field, 'Must be list')
            if len(val) > 20:  # Reasonable limit for list items
                raise ValidationError(field, 'Too many items in list (max 20)')
            for item in val:
                if not isinstance(item, str):
                    raise ValidationError(field, 'List items must be strings')
                if len(item) > 100:
                    raise ValidationError(field, 'List item exceeds 100 characters')
                if _contains_injection_tokens(item):
                    raise ValidationError(field, 'List item contains control tokens. Not allowed.')
    
    # Validate dict fields (weights)
    if 'weights' in data:
        val = data['weights']
        if not isinstance(val, dict):
            raise ValidationError('weights', 'Must be dictionary')
        if len(val) > 20:  # Reasonable limit
            raise ValidationError('weights', 'Too many keys in dictionary')
        for key, value in val.items():
            if not isinstance(key, str) or len(key) > 50:
                raise ValidationError('weights', 'Dictionary keys must be strings under 50 chars')
            if not isinstance(value, (int, float)):
                raise ValidationError('weights', 'Dictionary values must be numbers')
    
    return data


def _contains_injection_tokens(text):
    """
    Detect common prompt injection patterns.
    
    Args:
        text: String to check
        
    Returns:
        bool: True if injection patterns detected
    """
    injection_patterns = [
        'SYSTEM:', 'ASSISTANT:', 'USER:',
        '```', '`', '~~~',
        'ignore previous', 'disregard', 'forget',
        'override', 'bypass', 'jailbreak',
        '\x00', '\r\n\r\n'  # null bytes, double newlines
    ]
    text_lower = text.lower()
    return any(pattern.lower() in text_lower for pattern in injection_patterns)


def strip_injection_tokens(text):
    """
    Remove known control tokens from user input (defense-in-depth).
    
    Args:
        text: String to clean
        
    Returns:
        str: Cleaned text
    """
    # Remove role tokens
    text = text.replace('SYSTEM:', '').replace('ASSISTANT:', '').replace('USER:', '')
    text = text.replace('system:', '').replace('assistant:', '').replace('user:', '')
    
    # Remove code fences
    text = text.replace('```', '').replace('~~~', '')
    
    # Remove excessive newlines (replace 3+ newlines with 2)
    while '\n\n\n' in text:
        text = text.replace('\n\n\n', '\n\n')
    
    return text.strip()
