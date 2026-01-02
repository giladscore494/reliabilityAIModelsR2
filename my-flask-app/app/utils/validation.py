from datetime import datetime
from app.exceptions import ValidationError

# Constants
MIN_YEAR = 1995


# Custom validation functions
def validate_email(email):
    """Validate email format"""
    if not isinstance(email, str):
        return False
    # Basic email validation
    return '@' in email and '.' in email.split('@')[-1]


def validate_url(url):
    """Validate URL format"""
    if not isinstance(url, str):
        return False
    return url.startswith(('http://', 'https://'))


def validate_positive_float(val):
    """Validate that value is a positive float or int"""
    if isinstance(val, (int, float)):
        return val > 0
    return False


# Allowed fields for data validation
ALLOWED_FIELDS = {
    'name',
    'email',
    'phone',
    'company',
    'industry',
    'safety_required',
    'safety_required_radio',
    'year_min',
    'year_max',
    'year',
    'budget_min',
    'budget_max',
    'comments',
}


def validate_form_data(data):
    """
    Validate form data against allowed fields and type constraints.
    
    Args:
        data (dict): Form data to validate
        
    Raises:
        ValidationError: If validation fails
    """
    if not isinstance(data, dict):
        raise ValidationError('data', 'Must be a dictionary')
    
    # Check for unknown fields
    unknown_fields = set(data.keys()) - ALLOWED_FIELDS
    if unknown_fields:
        raise ValidationError('fields', f'Unknown fields: {", ".join(unknown_fields)}')
    
    # Validate individual fields
    if 'name' in data:
        val = data['name']
        if not isinstance(val, str) or len(val) < 2 or len(val) > 100:
            raise ValidationError('name', 'Must be a string between 2–100 characters')
    
    if 'email' in data:
        val = data['email']
        if not isinstance(val, str) or not validate_email(val):
            raise ValidationError('email', 'Must be a valid email')
    
    if 'phone' in data:
        val = data['phone']
        if val is not None and (not isinstance(val, str) or len(val) < 10):
            raise ValidationError('phone', 'Must be a valid phone number')
    
    if 'company' in data:
        val = data['company']
        if val is not None and (not isinstance(val, str) or len(val) < 2):
            raise ValidationError('company', 'Must be a string of at least 2 characters')
    
    if 'industry' in data:
        val = data['industry']
        if val is not None and (not isinstance(val, str) or len(val) < 2):
            raise ValidationError('industry', 'Must be a string of at least 2 characters')
    
    if 'safety_required' in data:
        val = data['safety_required']
        if not isinstance(val, bool):
            raise ValidationError('safety_required', 'Must be a boolean')
    
    if 'safety_required_radio' in data:
        val = data['safety_required_radio']
        if not isinstance(val, bool):
            raise ValidationError('safety_required_radio', 'Must be a boolean')
    
    if 'year_min' in data:
        val = data['year_min']
        current_year = datetime.now().year
        if not isinstance(val, int) or val < 1995 or val > current_year:
            raise ValidationError('year_min', f'Must be 1995–{current_year}')

    if 'year_max' in data:
        val = data['year_max']
        current_year = datetime.now().year
        if not isinstance(val, int) or val < 1995 or val > current_year:
            raise ValidationError('year_max', f'Must be 1995–{current_year}')
    
    if 'year' in data:
        val = data['year']
        current_year = datetime.now().year
        if val is not None and (not isinstance(val, int) or val < 1995 or val > current_year):
            raise ValidationError('year', f'Must be 1995–{current_year}')
    
    if 'budget_min' in data:
        val = data['budget_min']
        if val is not None and not validate_positive_float(val):
            raise ValidationError('budget_min', 'Must be a positive number')
    
    if 'budget_max' in data:
        val = data['budget_max']
        if val is not None and not validate_positive_float(val):
            raise ValidationError('budget_max', 'Must be a positive number')
    
    if 'comments' in data:
        val = data['comments']
        if val is not None and (not isinstance(val, str) or len(val) > 5000):
            raise ValidationError('comments', 'Must be a string of at most 5000 characters')
