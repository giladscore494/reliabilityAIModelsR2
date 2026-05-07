# Stage 2: Current User-Visible Surface

## Live UI Endpoints (Confirmed from `_navbar.html` + Active Templates)

### Primary Navigation (Visible to All Users)
From `templates/_navbar.html` lines 6-12:
1. **`public.index`** → `/` - Landing page
2. **`public.app_page`** → `/app` - Reliability analysis UI
3. **`comparison.compare_page`** → `/compare` - Vehicle comparison UI  
4. **`advisor.recommendations`** → `/advisor` - Recommendations UI
5. **`dashboard.dashboard`** → `/dashboard` - Search history dashboard

### Authentication Endpoints
6. **`public.login`** → `/login` - Login page
7. **`public.logout`** → `/logout` - Logout action

### Legal Pages
8. **`public.terms`** → `/terms` - Terms of service
9. **`public.privacy`** → `/privacy` - Privacy policy

### Owner-Only Navigation
From `templates/_navbar.html` line 44:
10. **`owner.owner_examples`** → `/owner/examples` - Manage public examples dashboard (owner-only, visible in navbar)

### API Endpoints (Used by Active Templates/Static JS)

#### Reliability Analysis APIs (`static/script.js`)
- **POST `/api/analyze`** - Analyze single vehicle
- **GET `/api/analyze/<request_id>`** - Poll analysis status

#### Comparison APIs (`templates/compare.html`)
- **POST `/api/compare/analyze`** - Compare multiple vehicles
- **GET `/api/compare/poll/<request_id>`** - Poll comparison status

#### Advisor APIs (`templates/recommendations.html`)
- **POST `/api/advisor/analyze`** - Get recommendations
- **GET `/api/advisor/poll/<request_id>`** - Poll recommendations status

#### Legal APIs (Used by all result templates)
- **POST `/api/legal/accept`** - Accept legal terms
- **GET `/api/legal/status`** - Check legal acceptance status

#### Feedback API (`static/script.js` line ~1850+)
- **POST `/api/feedback`** - Submit thumbs up/down feedback

#### Owner APIs (`templates/owner_examples.html`)
- **POST `/owner/examples/update`** - Update public example selections

#### Public Examples APIs (`templates/landing.html` line 159)
- **GET `/api/examples`** - List all public examples for landing page cards
- **GET `/example/<slug>`** - View public example detail page

### Research Data Collection
Research consent modals and fields are embedded in:
- `templates/reliability_app.html` (via `_research_reliability_panel.html`)
- `templates/compare.html` (via `_research_compare_panel.html`)
- `templates/recommendations.html` (via `_research_advisor_fields.html`)

Research APIs:
- Research response submission integrated into main analyze/compare/advisor endpoints
- Owner profile API: **POST `/api/owner-profile`** (separate research flow)

## Summary: Active Blueprints
✅ **KEEP**: public, analyze, advisor, comparison, dashboard, legal, feedback, owner, public_examples, owner_profile (research)

⚠️ **CANDIDATES FOR REMOVAL**: leasing, service_prices
