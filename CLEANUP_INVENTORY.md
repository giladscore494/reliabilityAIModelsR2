# Stage 1: Cleanup Inventory

## Repository Structure Analysis
**Repository**: giladscore494/reliabilityAIModelsR2  
**Branch**: copilot/remove-legacy-product-code  
**Date**: 2025-01-03

### Flask App Structure (`my-flask-app/`)

#### Routes Blueprints (13 total)
1. **public_routes.py** - Landing page, login, logout, app page, terms, privacy
2. **analyze_routes.py** - `/api/analyze` - reliability analysis API
3. **advisor_routes.py** - `/advisor/recommendations` - recommendations UI + API
4. **comparison_routes.py** - `/compare` - comparison UI + API
5. **dashboard_routes.py** - `/dashboard` - search history dashboard
6. **legal_routes.py** - Legal acceptance API endpoints
7. **feedback_routes.py** - `/api/feedback` - thumbs up/down feedback API
8. **owner_routes.py** - `/owner/examples` - owner dashboard for managing public examples
9. **leasing_routes.py** ⚠️ - `/leasing-advisor` - leasing advisor feature
10. **service_prices_routes.py** ⚠️ - `/api/service-prices/*` - service invoice analysis
11. **owner_profile_routes.py** ⚠️ - `/api/owner-profile` - owner profile research API
12. **public_examples_routes.py** ⚠️ - `/example/<slug>` + `/api/examples` - public examples

#### Services (7 total)
1. **advisor_service.py** - Recommendations logic
2. **analyze_service.py** - Reliability analysis logic
3. **comparison_service.py** - Comparison logic
4. **history_service.py** - History fetching (SearchHistory, AdvisorHistory, LeasingAdvisorHistory)
5. **research_aggregation_service.py** - Research data aggregation
6. **leasing_advisor_service.py** ⚠️ - Leasing advisor logic
7. **service_prices_service.py** ⚠️ - Service price analysis logic

#### Templates (24 total)
**Active Core Templates**:
- `landing.html`, `reliability_app.html`, `compare.html`, `recommendations.html`, `dashboard.html`
- `terms.html`, `privacy.html`
- `owner_examples.html` (owner-only management UI)
- Partials: `_navbar.html`, `_footer.html`, `_login_modal.html`, `_posthog_snippet.html`
- Legal partials: `_legal_consent_banner.html`, `_legal_submit_consent.html`
- Research partials: `_research_*.html`
- Coming soon: `coming_soon.html`, `coming_soon_fullscreen.html`

**Legacy Templates** ⚠️:
- `leasing_advisor.html`
- `service_prices.html`, `service_prices_report.html`
- `example.html` (public example detail page)

#### Static Assets (6 files)
- `script.js` - Main app logic (analyze, feedback API)
- `navbar.js` - Navbar drawer
- `recommendations.js` - Recommendations UI
- `research.js` - Research consent handling
- `style.css` - Styles
- `leasing_advisor.js` ⚠️ - Leasing advisor UI logic

#### Models (app/models.py)
**Active Models**:
- `User` - User accounts
- `SearchHistory` - Reliability searches (includes `is_public_example` + `example_slug` fields)
- `AdvisorHistory` - Recommendation searches
- `LegalAcceptance` - Legal consent records
- `ResearchConsent`, `ResearchResponseSession`, `ResearchResponse` - Research data
- `Feedback` - User feedback (thumbs up/down)
- `QuotaReservation` - Quota management

**Legacy Models** ⚠️:
- `LeasingAdvisorHistory` - Leasing searches
- `ServiceInvoice`, `ServiceInvoiceItem` - Service invoice analysis records

#### Tests (36 files)
Tests covering all features including legacy ones. Need to identify tests scoped to removed features.

### Migrations Status
- **migrations/** directory contains ~30+ migration files
- **Policy**: Do NOT modify or delete migrations
- Model classes can be removed if no active code uses them (migrations remain for historical data)
