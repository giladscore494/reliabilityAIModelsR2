# Stage 3: Legacy Code Candidates

## Assessment Methodology
1. Grep for references across codebase (templates, static JS, route files, tests)
2. Check if endpoint appears in navbar or any active template
3. Verify model usage in active services
4. Categorize by risk: LOW (no references), MEDIUM (isolated), HIGH (uncertain dependencies)

---

## 🔴 HIGH CONFIDENCE REMOVAL CANDIDATES

### 1. Leasing Advisor Feature
**Risk Level**: LOW  
**Blueprint**: `app/routes/leasing_routes.py`  
**Service**: `app/services/leasing_advisor_service.py`  
**Template**: `templates/leasing_advisor.html`  
**Static**: `static/leasing_advisor.js`  
**Model**: `LeasingAdvisorHistory` (used only by leasing routes/service)

**Evidence of Non-Use**:
- ❌ NOT in `_navbar.html` navigation
- ❌ No `url_for('leasing.*')` in any active template
- ❌ No `/api/leasing*` or `/leasing*` fetch calls in `static/script.js`, `static/recommendations.js`, or other active JS
- ✅ Only referenced in:
  - `app/factory.py` (blueprint registration)
  - `app/services/history_service.py` (isolated history fetch function)
  - `tests/test_leasing_*.py` (feature-specific tests)

**Files to Remove**:
- `app/routes/leasing_routes.py`
- `app/services/leasing_advisor_service.py`
- `templates/leasing_advisor.html`
- `static/leasing_advisor.js`
- `tests/test_leasing_advisor.py`
- `tests/test_leasing_guardrails.py`

**Model Decision**: Remove `LeasingAdvisorHistory` class from `app/models.py` (no active code uses it). Keep migrations.

**Factory Changes**:
- Remove import: `from app.routes.leasing_routes import bp as leasing_bp`
- Remove registration: `app.register_blueprint(leasing_bp)`
- Remove model import: `LeasingAdvisorHistory` from line ~83

---

### 2. Service Prices / Invoice Analysis Feature
**Risk Level**: LOW  
**Blueprint**: `app/routes/service_prices_routes.py`  
**Service**: `app/services/service_prices_service.py`  
**Templates**: `templates/service_prices.html`, `templates/service_prices_report.html`  
**Models**: `ServiceInvoice`, `ServiceInvoiceItem`

**Evidence of Non-Use**:
- ❌ NOT in `_navbar.html` navigation
- ❌ No `url_for('service_prices.*')` in any active template
- ❌ No `/api/service-prices` fetch calls in active static JS files
- ✅ Only referenced in:
  - `app/factory.py` (blueprint registration)
  - `tests/test_service_prices*.py` (feature-specific tests)
  - `tests/test_production_hardening_legal_disclaimers.py` (legacy disclaimer tests)

**Files to Remove**:
- `app/routes/service_prices_routes.py`
- `app/services/service_prices_service.py`
- `templates/service_prices.html`
- `templates/service_prices_report.html`
- `tests/test_service_prices.py`
- `tests/test_service_prices_canonicalize.py`
- `tests/test_service_price_guardrails.py`
- `tests/test_invoice_guardrails.py`

**Model Decision**: Remove `ServiceInvoice` and `ServiceInvoiceItem` classes from `app/models.py` (no active code uses them). Keep migrations.

**Factory Changes**:
- Remove import: `from app.routes.service_prices_routes import bp as service_prices_bp`
- Remove registration: `app.register_blueprint(service_prices_bp)`
- Remove model imports: `ServiceInvoice`, `ServiceInvoiceItem` from line ~85

---

## ⚠️ MEDIUM CONFIDENCE - VERIFY CAREFULLY

### 3. Owner Profile API
**Risk Level**: MEDIUM  
**Blueprint**: `app/routes/owner_profile_routes.py`  
**Endpoint**: `POST /api/owner-profile`

**Evidence**:
- ✅ Part of research flow (`app/research.py` defines `OWNER_PROFILE_FLOW`)
- ✅ Used for collecting owner vehicle history data (research enrichment)
- ⚠️ NOT visible in UI but MAY be called from active templates/research modals

**Decision**: **KEEP** - Part of research data collection system. The `OWNER_PROFILE_FLOW` is defined in `app/research.py` and is part of the active research framework.

---

### 4. Public Examples System
**Risk Level**: MEDIUM  
**Blueprint**: `app/routes/public_examples_routes.py`  
**Template**: `templates/example.html`  
**Endpoints**: 
- `GET /example/<slug>` - Public example detail page
- `GET /api/examples` - List public examples

**Evidence**:
- ✅ Used by `templates/landing.html` line 159: `fetch('/api/examples')` to display example cards on landing page
- ✅ Landing page links to `/example/<slug>` (line 170)
- ✅ Owner dashboard (`owner_examples.html`) manages these public examples
- ✅ Model fields `is_public_example` + `example_slug` in `SearchHistory` are actively used

**Decision**: **KEEP** - Active feature. Public examples are displayed on landing page and managed by owner users.

---

## 📊 Summary

### TO REMOVE (LOW RISK)
1. ✅ Leasing Advisor (routes, service, template, static JS, tests, model)
2. ✅ Service Prices (routes, service, templates, tests, models)

### TO KEEP (ACTIVE)
1. ✅ Owner Profile API (research flow)
2. ✅ Public Examples (landing page feature + owner management)
3. ✅ All other core features (analyze, compare, advisor, dashboard, legal, feedback, owner dashboard)

### Models Summary
- **Remove**: `LeasingAdvisorHistory`, `ServiceInvoice`, `ServiceInvoiceItem`
- **Keep**: All other models (User, SearchHistory, AdvisorHistory, Legal, Research, Feedback, Quota)
- **Migrations**: Keep all (never delete)
