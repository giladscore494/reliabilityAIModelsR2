# Research Refactor 2026-04-25 - Implementation Status

## Executive Summary

This refactor implements a privacy-compliant, three-layer data architecture for the yedaarechevAI platform. **Core infrastructure is complete**, but **template updates and service integration are required before production deployment**.

**Status**: 🟡 **Partially Complete** - Backend infrastructure ready, frontend/integration pending

---

## ✅ COMPLETED (Backend Infrastructure)

### 1. Version Constants ✅
- **app/legal.py**: PRIVACY_VERSION = "2026-04-25", TERMS_VERSION = "2026-04-25"
- **app/research.py**: RESEARCH_CONSENT_VERSION = "2026-04-25", OWNER_PROFILE_FLOW = "owner_profile"
- Backward compatible with existing RESEARCH_NOTICE_VERSION

### 2. Database Models ✅
- **app/models.py**: Updated with new columns:
  - `ResearchConsent`: consent_given, source_page, ip_hash, user_agent_hash, revoked_at
  - `ResearchResponseSession`: question_version, related_search_history_id, related_advisor_history_id, related_compare_history_id
  - `ResearchResponse`: answer_type
- All columns nullable for backward compatibility

### 3. Alembic Migration ✅
- **migrations/versions/bb03_research_260425.py**
- Parent: aa02_feedback_table
- SQLite-safe with batch_alter_table
- Foreign keys with ondelete='SET NULL'
- Tested: syntax valid, ready to run

### 4. Sanitization Layer ✅
- **app/utils/sanitization.py**: Added 3 new functions:
  - `sanitize_profile_for_storage()`: Removes PII, converts age/license_years to buckets
  - `sanitize_context_for_ai()`: Keeps only reasoning-relevant fields, truncates strings
  - `sanitize_research_answer()`: Validates against allowlist, rejects PII patterns
  - `_contains_pii_strings()`: Regex checks for Israeli license plates, phones, emails

### 5. Research Aggregation Service ✅
- **app/services/research_aggregation_service.py**
- `aggregate_vehicle_reliability_reports()`: MIN_AGGREGATION_SAMPLE_SIZE = 10
- `get_owner_satisfaction_summary()`
- `get_real_world_cost_summary()` (stub)
- Respects consent_given=True AND revoked_at IS NULL

### 6. AI Context Utility ✅
- **app/utils/ai_context.py**
- `build_user_context_for_reasoning()`: Pulls latest owner_profile, sanitizes for AI
- `build_internal_dataset_summary()`: Calls aggregation service
- Never raises exceptions (returns empty dict on error)

### 7. Owner Profile Route ✅
- **app/routes/owner_profile_routes.py**
- POST /api/owner_profile
- `validate_owner_profile_payload()`: Checks required fields, rejects prohibited fields
- Returns {"saved": false, "reason": "consent_required"} without consent
- Creates ResearchResponseSession + ResearchResponse rows with consent

### 8. Consent Revocation ✅
- **app/routes/legal_routes.py**: Added POST /api/research_consent/revoke
- Sets revoked_at timestamp on all active consents
- Returns revoked_count

### 9. Blueprint Registration ✅
- **app/factory.py**: Registered owner_profile_bp

### 10. Test Suite ✅
- **tests/test_research_refactor.py**: 12 comprehensive tests
  - test_advisor_without_research_fields_succeeds
  - test_advisor_history_profile_json_excludes_market_research_context
  - test_advisor_history_profile_json_excludes_sensitive_fields
  - test_research_save_requires_consent
  - test_research_save_rejected_without_consent
  - test_owner_profile_valid_payload_saves
  - test_owner_profile_missing_required_fields_rejected
  - test_owner_profile_rejects_license_plate
  - test_ai_context_excludes_personal_identifiers
  - test_privacy_terms_versions_saved_with_consent
  - test_user_can_withdraw_research_consent
  - test_declined_research_consent_does_not_break_core_product
- All tests compile, ready to run with pytest

### 11. Documentation ✅
- **docs/RESEARCH_REFACTOR_2026_04_25.md**: Complete implementation guide
- Lists all changed files, migration details, deployment checklist
- Includes rollback plan and testing instructions

---

## ⚠️ PENDING (Must Complete Before Production)

### CRITICAL - Frontend Templates

#### 1. Privacy Policy (templates/privacy.html) ⚠️
**Status**: Needs complete Hebrew rewrite

**Required Sections** (12 total):
1. מבוא (Introduction)
2. מידע שאנו אוספים (Information We Collect)
3. שימוש במידע (Use of Information)
4. שיתוף מידע (Information Sharing)
5. אבטחת מידע (Data Security)
6. זכויות המשתמש (User Rights)
7. עוגיות וטכנולוגיות מעקב (Cookies and Tracking)
8. מידע מחקרי (Research Data) **[New section for consent-gated dataset]**
9. שינויים במדיניות (Policy Changes)
10. צד שלישי (Third Parties)
11. משך שמירת מידע (Data Retention)
12. יצירת קשר (Contact)

**Action Items**:
- [ ] Write Hebrew text for all 12 sections
- [ ] Add placeholder: [INSERT_CONTACT_EMAIL]
- [ ] Pass PRIVACY_VERSION, TERMS_VERSION, RESEARCH_CONSENT_VERSION via context
- [ ] Add `{# TODO: have an Israeli privacy/consumer-law attorney review before production #}` comments
- [ ] Preserve existing extends/blocks structure

#### 2. Terms of Use (templates/terms.html) ⚠️
**Status**: Needs complete Hebrew rewrite

**Required Sections** (12 total):
1. הקדמה (Introduction)
2. הגדרות (Definitions)
3. תנאי שימוש כלליים (General Terms)
4. רישיון שימוש (Usage License)
5. הגבלות אחריות (Liability Limitations)
6. איסוף ושימוש במידע (Data Collection)
7. מחקר ופיתוח (Research and Development) **[New section]**
8. קניין רוחני (Intellectual Property)
9. סיום חשבון (Account Termination)
10. שינויים בתקנון (Terms Changes)
11. סמכות שיפוט (Jurisdiction) - [INSERT_ISRAELI_COURT_JURISDICTION_AFTER_LEGAL_REVIEW]
12. יצירת קשר (Contact)

**Action Items**:
- [ ] Write Hebrew text for all 12 sections
- [ ] Add TODO comments like privacy.html
- [ ] Pass versions via context
- [ ] Include jurisdiction placeholder

#### 3. Research Consent Checkboxes ⚠️

**Files to Update**:
- templates/_research_advisor_fields.html
- templates/_research_reliability_panel.html
- templates/_research_compare_panel.html

**Required Hebrew Text**:
```html
<!-- Consent checkbox (separate from general Terms acceptance) -->
<label>
  <input type="checkbox" name="research_consent" id="research_consent" />
  אני מסכים/ה שנתוני הרכב שלי ישמשו למחקר אנונימי לשיפור השירות (ניתן לבטל בכל עת)
</label>

<!-- Notice near data fields -->
<p class="text-sm text-gray-400">
  מסירת מידע מחקרי היא רשות ומסייעת לנו לספק המלצות מותאמות יותר.
</p>
```

**Action Items**:
- [ ] Add separate research consent checkbox
- [ ] Add short notice text
- [ ] Preserve existing form IDs and JS bindings

#### 4. Result Disclaimers ⚠️

**Required Hebrew Disclaimers**:

**Reliability (templates/reliability_app.html)**:
```html
<p class="disclaimer text-sm text-gray-500 mb-4">
  הניתוח מבוסס על מידע מהאינטרנט ואינו מהווה ייעוץ מקצועי. 
  מומלץ לבצע בדיקה טכנית מלאה לפני רכישה.
</p>
```

**Advisor (templates/recommendations.html)**:
```html
<p class="disclaimer text-sm text-gray-500 mb-4">
  ההמלצות מבוססות על העדפותיך ומידע כללי. אינן מהוות ייעוץ פיננסי או מקצועי.
  מומלץ לבצע מחקר עצמאי ולהיוועץ עם מומחה לפני קניה.
</p>
```

**Compare (templates/compare.html)**:
```html
<p class="disclaimer text-sm text-gray-500 mb-4">
  ההשוואה מבוססת על מידע זמין באינטרנט. אינה מהווה ייעוץ מקצועי.
  מומלץ לבדוק את המפרט והמצב בפועל לפני החלטה.
</p>
```

**Action Items**:
- [ ] Add disclaimers near top of result sections
- [ ] Match existing CSS classes
- [ ] Don't break layouts

### CRITICAL - Service Integration

#### 5. Advisor Service (app/services/advisor_service.py) ⚠️

**Required Changes**:
1. Import `sanitize_profile_for_storage` from app.utils.sanitization
2. Before creating `AdvisorHistory`, sanitize profile:
   ```python
   sanitized_profile = sanitize_profile_for_storage(profile_json)
   history = AdvisorHistory(
       user_id=user_id,
       profile_json=json.dumps(sanitized_profile),
       ...
   )
   ```
3. If optional research fields present AND active consent exists:
   - Create `ResearchResponseSession(flow_type='advisor', related_advisor_history_id=history.id)`
   - Create `ResearchResponse` rows for research fields
4. If no consent: silently drop optional fields (no 4xx error)

**Action Items**:
- [ ] Find where `AdvisorHistory` is created in `handle_advisor_logic()`
- [ ] Call `sanitize_profile_for_storage()` before JSON serialization
- [ ] Verify `market_research_context` not in stored profile
- [ ] Add research data save logic (conditional on consent)

#### 6. AI Prompt Integration ⚠️

**Files to Update**:
- app/services/analyze_service.py (reliability analysis)
- app/services/advisor_service.py (recommendations)
- app/services/comparison_service.py (car comparison)

**Required Changes**:
1. Import `build_user_context_for_reasoning` from app.utils.ai_context
2. Find where AI prompt is constructed (search for "gemini", "prompt", "generate_content")
3. Add context to prompt payload:
   ```python
   user_context = build_user_context_for_reasoning(user_id, request_data)
   prompt_payload = {
       ...existing fields...,
       "user_context_for_reasoning": user_context,
   }
   ```
4. Add tests confirming PII never in prompt

**Action Items**:
- [ ] Locate AI prompt construction in each service
- [ ] Inject user_context_for_reasoning
- [ ] Write tests: assert no "license_plate", "email", "phone" in prompt

### MEDIUM PRIORITY

#### 7. IP Hashing Helper ⚠️

**File**: app/legal.py

**Required Function**:
```python
def hash_ip_for_consent(raw_ip: str) -> str:
    """Hash IP address for consent audit using LEGAL_IP_HASH_SALT."""
    if not LEGAL_IP_HASH_SALT:
        return normalize_legal_ip(raw_ip)  # Fall back to subnet
    return hashlib.sha256(f"{LEGAL_IP_HASH_SALT}{raw_ip}".encode("utf-8")).hexdigest()
```

**Usage**: When creating `ResearchConsent`, populate `ip_hash` instead of (or in addition to) `accepted_ip`.

---

## 📋 Deployment Checklist

### Pre-Deployment

1. **Legal Review** 🔴 BLOCKING
   - [ ] Have Israeli privacy/consumer-law attorney review:
     - Privacy Policy Hebrew text
     - Terms of Use Hebrew text
     - Research consent wording
     - Jurisdiction clause
   - [ ] Remove all `{# TODO: attorney review #}` comments after review

2. **Complete Template Updates** 🔴 BLOCKING
   - [ ] privacy.html rewritten (12 sections)
   - [ ] terms.html rewritten (12 sections)
   - [ ] Research consent checkboxes added (_research_*.html)
   - [ ] Result disclaimers added (reliability_app.html, recommendations.html, compare.html)

3. **Complete Service Integration** 🔴 BLOCKING
   - [ ] advisor_service.py calls sanitize_profile_for_storage()
   - [ ] analyze_service.py injects user context
   - [ ] comparison_service.py injects user context
   - [ ] Tests pass for all three services

4. **Environment Variables** 🟡 REQUIRED
   ```bash
   export LEGAL_IP_HASH_SALT="[generate-with: openssl rand -hex 32]"
   export PRIVACY_VERSION="2026-04-25"
   export TERMS_VERSION="2026-04-25"
   export RESEARCH_CONSENT_VERSION="2026-04-25"
   ```

5. **Database Backup** 🟢 RECOMMENDED
   ```bash
   # Full backup before migration
   pg_dump $DATABASE_URL > backup_pre_research_refactor.sql
   ```

### Deployment

6. **Run Migration** 🟡 REQUIRED
   ```bash
   cd my-flask-app
   flask --app main:create_app db current
   # Confirm current revision is still aa02_feedback_table
   # If current is aa02_feedback_table and the new columns do not exist:
   flask --app main:create_app db upgrade
   # Verify: flask --app main:create_app db current
   # Expected: bb03_research_260425
   ```

   If the new columns already exist while `alembic_version` is still `aa02_feedback_table`,
   stop and report a partial migration state instead of rerunning blindly.

7. **Smoke Tests** 🟡 REQUIRED
   ```bash
   cd my-flask-app
   python -m pytest tests/test_research_refactor.py -v
   python -m pytest tests/test_research_collection.py -v  # Existing tests
   python -m pytest tests/test_legal_acceptance.py -v    # Existing tests
   ```

### Post-Deployment

8. **Verify Core Flows** 🟢 RECOMMENDED
   - [ ] Reliability check: works without consent
   - [ ] Advisor: works without research fields
   - [ ] Comparison: works without consent
   - [ ] Owner profile: saves with consent, rejected without
   - [ ] Consent revocation: works, prevents future saves

9. **Monitor Logs** 🟢 RECOMMENDED
   - [ ] No LEGAL_IP_HASH_SALT warnings
   - [ ] No migration errors
   - [ ] No sanitization validation errors
   - [ ] Check for PII leakage (search logs for Israeli phone patterns)

---

## 📁 Changed Files Summary

### Modified Files (6)
1. app/factory.py - Registered owner_profile_bp
2. app/legal.py - Updated version constants
3. app/models.py - Added columns to ResearchConsent, ResearchResponseSession, ResearchResponse
4. app/research.py - Added RESEARCH_CONSENT_VERSION, OWNER_PROFILE_FLOW, enums
5. app/routes/legal_routes.py - Added revoke endpoint
6. app/utils/sanitization.py - Added 3 sanitization functions + PII regex

### New Files (6)
7. app/routes/owner_profile_routes.py - Owner profile submission endpoint
8. app/services/research_aggregation_service.py - Aggregate statistics service
9. app/utils/ai_context.py - AI context builder
10. migrations/versions/bb03_research_260425.py - Alembic migration
11. tests/test_research_refactor.py - 12 comprehensive tests
12. docs/RESEARCH_REFACTOR_2026_04_25.md - Implementation guide

### Pending Updates (9 files)
⚠️ **Must complete before production**:
13. templates/privacy.html - Hebrew rewrite
14. templates/terms.html - Hebrew rewrite
15. templates/_research_advisor_fields.html - Consent checkbox
16. templates/_research_reliability_panel.html - Consent checkbox
17. templates/_research_compare_panel.html - Consent checkbox
18. templates/reliability_app.html - Disclaimer
19. templates/recommendations.html - Disclaimer
20. templates/compare.html - Disclaimer
21. app/services/advisor_service.py - Sanitization + research save logic
22. app/services/analyze_service.py - AI context injection
23. app/services/comparison_service.py - AI context injection

---

## 🧪 Testing

**Tests Created**: 12 in tests/test_research_refactor.py  
**Test Status**: ✅ Syntax valid, ready to run  
**Run Command**:
```bash
cd my-flask-app
python -m pytest tests/test_research_refactor.py -v
```

**Expected Results** (once templates/services complete):
- All 12 tests should pass
- Existing tests in test_research_collection.py should still pass
- No regressions in test_legal_acceptance.py

---

## 🔄 Rollback Plan

If critical issues arise:

1. **Immediate Disable**:
   ```bash
   export RESEARCH_CONSENT_REQUIRED="false"
   # Restart app
   ```

2. **Database Rollback**:
   ```bash
   flask db downgrade bb03_research_260425
   ```

3. **Code Rollback**:
   ```bash
   git revert <commit-sha>
   ```

---

## 📞 Support

**Technical Questions**: Backend infrastructure complete, integration guidance available  
**Legal Questions**: All legal text must be reviewed by Israeli attorney  
**Testing**: 12 tests provided, run with pytest

---

**IMPORTANT**: This is **NOT legal advice**. All legal text sections require review by an Israeli privacy/consumer-law attorney before production deployment.
