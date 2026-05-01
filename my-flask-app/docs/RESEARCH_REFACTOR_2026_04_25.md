# Research Refactor 2026-04-25

## Implementation Summary

This refactor implements a three-layer data collection architecture for the yedaarechevAI platform:

1. **Product History Layer**: Basic usage logs (searches, comparisons, advisor sessions)
2. **AI Reasoning Context Layer**: Sanitized, PII-free user context to improve recommendations
3. **Research Dataset Layer**: Consent-gated, anonymized dataset for reliability research

### Key Principles

- **Separation of concerns**: Three distinct data layers with different privacy levels
- **Explicit consent**: Research data collection requires active, informed, revocable consent
- **PII elimination**: Multiple sanitization layers prevent identifier leakage
- **Transparency**: Clear Hebrew UX disclaimers at every data collection point

## Changed Files

### Core Application Files

1. **app/legal.py** - Updated PRIVACY_VERSION and TERMS_VERSION to "2026-04-25"
2. **app/research.py** - Added RESEARCH_CONSENT_VERSION, OWNER_PROFILE_FLOW constants and enums
3. **app/models.py** - Added new columns to ResearchConsent, ResearchResponseSession, ResearchResponse
4. **app/factory.py** - Registered owner_profile_bp blueprint

### Migration Files

5. **migrations/versions/bb03_research_260425.py** - Alembic migration adding:
   - ResearchConsent: consent_given, source_page, ip_hash, user_agent_hash, revoked_at
   - ResearchResponseSession: question_version, related_*_history_id foreign keys
   - ResearchResponse: answer_type

### New Service Files

6. **app/services/research_aggregation_service.py** - Aggregate statistics service (MIN_AGGREGATION_SAMPLE_SIZE=10)
7. **app/utils/ai_context.py** - AI context builder (sanitizes user data for prompts)
8. **app/utils/sanitization.py** - Added three new functions:
   - sanitize_profile_for_storage()
   - sanitize_context_for_ai()
   - sanitize_research_answer()

### New Route Files

9. **app/routes/owner_profile_routes.py** - Owner profile submission endpoint
10. **app/routes/legal_routes.py** - Added POST /api/research_consent/revoke endpoint

### Test Files

11. **tests/test_research_refactor.py** - 12 comprehensive tests covering:
    - Advisor without research fields
    - Profile sanitization
    - Consent requirements
    - Validation
    - Revocation

### Templates (Stub - Full Implementation Needed)

12. **templates/privacy.html** - ⚠️ Needs Hebrew rewrite with 12 sections + TODO comments
13. **templates/terms.html** - ⚠️ Needs Hebrew rewrite with 12 sections + TODO comments
14. **templates/_research_advisor_fields.html** - ⚠️ Needs consent checkbox update
15. **templates/_research_reliability_panel.html** - ⚠️ Needs consent checkbox update
16. **templates/_research_compare_panel.html** - ⚠️ Needs consent checkbox update
17. **templates/reliability_app.html** - ⚠️ Needs disclaimer text
18. **templates/recommendations.html** - ⚠️ Needs disclaimer text
19. **templates/compare.html** - ⚠️ Needs disclaimer text

### Service Integration (Stub - Full Implementation Needed)

20. **app/services/advisor_service.py** - ⚠️ Needs sanitize_profile_for_storage() integration + AI context
21. **app/services/analyze_service.py** - ⚠️ Needs AI context integration
22. **app/services/comparison_service.py** - ⚠️ Needs AI context integration

## Migration Details

**Revision ID**: bb03_research_260425  
**Parent Revision**: aa02_feedback_table  
**Strategy**: Nullable columns with server defaults for backward compatibility

All new columns are nullable to allow existing rows to survive. New rows should populate:
- `ip_hash` instead of plaintext `accepted_ip` (use sha256)
- `consent_given` defaults to `true`
- Foreign keys use `ondelete='SET NULL'` for soft references

## Deployment Checklist

### Pre-Deployment

1. ⚠️ **Legal Review**: Have an Israeli privacy/consumer-law attorney review:
   - Privacy Policy Hebrew text (12 sections)
   - Terms of Use Hebrew text (12 sections)
   - Research consent notice wording
   - Jurisdiction clause in Terms

2. **Environment Variables**: Set in production:
   ```bash
   LEGAL_IP_HASH_SALT="[generate-strong-random-salt]"
   PRIVACY_VERSION="2026-04-25"
   TERMS_VERSION="2026-04-25"
   RESEARCH_CONSENT_VERSION="2026-04-25"
   ```

3. **Database Backup**: Full backup before migration

### Deployment Steps

4. **Run Migration**:
   ```bash
   cd my-flask-app
   flask --app main:create_app db current
   # Confirm current revision is still aa02_feedback_table
   # If current is aa02_feedback_table and the new columns do not exist:
   flask --app main:create_app db upgrade
   ```

   If the new columns already exist while `alembic_version` is still `aa02_feedback_table`,
   stop and report a partial migration state instead of rerunning blindly.

5. **Verify Migration**:
   ```sql
   -- Check new columns exist
   PRAGMA table_info(research_consent);
   PRAGMA table_info(research_response_session);
   PRAGMA table_info(research_response);
   ```

### Post-Deployment Verification

6. **Test Core Flows**:
   - [ ] Reliability check works without consent
   - [ ] Advisor works without research fields
   - [ ] Comparison works without consent
   - [ ] Owner profile saves with consent
   - [ ] Owner profile rejected without consent

7. **Test Consent Flow**:
   - [ ] User can grant research consent
   - [ ] User can revoke research consent
   - [ ] Revoked consent prevents new research data saves
   - [ ] Revoked consent doesn't break core product

8. **Monitor Logs**:
   - Check for LEGAL_IP_HASH_SALT warnings
   - Check for migration errors
   - Check for sanitization validation errors

## Known Limitations & TODOs

### Critical - Must Complete Before Production

- [ ] **Privacy.html rewrite**: Complete Hebrew text with 12 sections
- [ ] **Terms.html rewrite**: Complete Hebrew text with 12 sections + jurisdiction
- [ ] **Template consent checkboxes**: Update all _research_*.html templates
- [ ] **Result disclaimers**: Add Hebrew disclaimers to reliability_app.html, recommendations.html, compare.html
- [ ] **Advisor service integration**: Call sanitize_profile_for_storage() before saving AdvisorHistory
- [ ] **AI context integration**: Inject build_user_context_for_reasoning() into analyze/advisor/comparison prompts

### Medium Priority

- [ ] **IP hashing helper**: Create hash_ip_for_consent() utility in legal.py
- [ ] **Owner profile validator refinement**: Add more robust enum validation
- [ ] **Research aggregation optimization**: Add caching for aggregate queries
- [ ] **Admin dashboard**: Add research consent metrics/export tools

### Nice-to-Have

- [ ] **Frontend JS**: Wire owner_profile.js form submission
- [ ] **Email notifications**: Notify users when research consent is used
- [ ] **Data export**: User-facing research data export endpoint
- [ ] **Aggregate API**: Public endpoint for aggregate reliability stats

## Testing Status

**Migration Revision**: bb03_research_260425  
**Parent Revision**: aa02_feedback_table

**Tests Created**: 12 tests in tests/test_research_refactor.py  
**Test Coverage**:
- ✅ Sanitization functions (3 tests)
- ✅ Consent requirements (3 tests)
- ✅ Validation (2 tests)
- ✅ Revocation (1 test)
- ✅ Core product compatibility (3 tests)

**Run Tests**:
```bash
cd my-flask-app
python -m pytest tests/test_research_refactor.py -v
```

## Rollback Plan

If issues arise post-deployment:

1. **Emergency disable**: Set environment variable:
   ```bash
   RESEARCH_CONSENT_REQUIRED="false"
   ```

2. **Database rollback**:
   ```bash
   flask db downgrade bb03_research_260425
   ```

3. **Code rollback**: Revert to commit before this refactor

## Contact

For questions about this implementation:
- Technical: [Technical lead contact]
- Legal: [Israeli privacy attorney contact]
- Product: [Product owner contact]

---

**Disclaimer**: This implementation provides technical infrastructure for privacy-compliant research data collection. It is NOT legal advice. All legal text sections must be reviewed by an Israeli privacy/consumer-law attorney before production deployment.
