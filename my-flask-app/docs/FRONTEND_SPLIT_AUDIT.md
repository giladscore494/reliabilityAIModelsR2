# Frontend split audit — Phase 1

## Scope audited

- `static/script.js` (~2003 lines)
- `static/recommendations.js` (~1444 lines)
- shared helper `static/research.js`
- templates that load those files directly or depend on globals they expose

## Templates loading `static/script.js`

1. `templates/reliability_app.html`
   - Loads `research.js` first, then `script.js`
   - Inline script depends on `window.loadHistoryList`, `window.compareHistory`, and `window.openTab`
2. `templates/example.html`
   - Loads `script.js`
   - Injects `#example-data` and stub `#auth-data` for script bootstrap

## Templates loading `static/recommendations.js`

1. `templates/recommendations.html`
   - Loads `research.js` first, then `recommendations.js`
   - Injects `window.advisorHistoryProfile`, `window.advisorHistoryResult`, and `window.advisorHistoryId`

## Global functions and globals currently relied on

### Inline HTML handlers

- No inline HTML handler currently calls a function from `script.js` or `recommendations.js`
- Shared modal partial `_login_modal.html` uses inline `onclick="window.hideLoginModal()"`, but that global is defined in the partial itself, not in the large frontend files

### Inline scripts / cross-template consumers

- `window.loadHistoryList` — exported by `script.js`, called from inline script in `reliability_app.html`
- `window.compareHistory` — exported by `script.js`, called from inline script in `reliability_app.html`
- `window.openTab` — exported by `script.js`, called from inline script in `reliability_app.html`
- `window.renderFeedbackCTA` — exported by `script.js`, consumed by `templates/compare.html`
- `window.renderResults` / `window.renderAnalyzeResult` — exported by `script.js` for compatibility
- `window.YedaResearch.createClient(...)` — exported by `research.js`, consumed by `script.js`, `recommendations.js`, and the inline compare-page script
- `window.advisorHistoryProfile`, `window.advisorHistoryResult`, `window.advisorHistoryId` — bootstrapped in `recommendations.html` and consumed by `recommendations.js`

## DOM IDs/classes referenced by `static/script.js`

### IDs

- Data/bootstrap: `car-data`, `auth-data`, `reliability-ack-data`, `example-data`
- Reliability form: `make`, `model`, `year`, `car-form`, `submit-button`
- Reliability form field IDs: `mileage_range`, `fuel_type`, `transmission`, `sub_model`, `annual_km`, `city_pct`, `terrain`, `climate`, `parking`, `driver_style`, `load`
- Reliability result shell: `results-container`, `report`, `faults`, `costs`, `competitors`, `vehicle-profile-container`
- Reliability summary/result details: `summary-simple-text`, `summary-detailed-text`, `summary-toggle-btn`, `summary-detailed-block`, `reliability-score-container`, `sources-list`, `sources-block`
- Reliability error panel: `analyze-error-panel`, `analyze-error-title`, `analyze-error-message`, `analyze-error-meta`, `analyze-error-debug`
- Legal gating: `legal-confirm`, `legal-error`
- Result-ready / acknowledgement: `reliabilityResultReadyPanel`, `reliabilityOpenResultButton`, `reliabilityResultAckModal`, `reliabilityResultAckCheckbox`, `reliabilityResultAckConfirm`, `reliabilityResultAckCancel`, `reliabilityResultAckError`
- Timing UI: `timingBanner`, `elapsedTime`, `etaText`, `statusText`, `progressRing`
- Research UI: `reliabilityResearchSection`, `reliabilityResearchFormWrap`, `reliabilityResearchForm`, `reliabilityResearchMessage`, `reliabilityResearchAnswerNow`, `reliabilityResearchSkip`, `reliabilityResearchClose`, `reliabilityResearchDismiss`, `reliabilityOpenResultNow`, `reliabilityGarageType`, `reliabilityLastServiceCost`
- Shared research modal hook: `researchConsentModal`
- History compare UI inside reliability page: `history-select-1`, `history-select-2`, `comparison-result`

### Classes

- `.tab-btn`
- `.tab-content`
- `.spinner`
- `.button-text`
- `.feedback-cta`
- `.feedback-btn`

### Name-based selectors that also matter

- `ownership_status`
- `first_test_pass`
- `out_of_warranty_repairs`

## DOM IDs/classes referenced by `static/recommendations.js`

### IDs

- Advisor form shell: `advisor-form`, `advisor-submit`, `advisor-consent`, `advisor-error`
- Advisor result shell: `advisor-results`, `advisorResultReadyPanel`, `advisorOpenResultButton`, `advisor-search-queries`, `advisor-table-wrapper`
- Advisor profile/result support: `advisor-profile-summary`, `advisor-highlight-cards`
- Timing UI: `advisorTimingBanner`, `advisorElapsedTime`, `advisorEtaText`, `advisorStatusText`, `advisorProgressRing`
- Research UI: `advisorResearchSection`, `advisorResearchFormWrap`, `advisorResearchForm`, `advisorResearchAnswerNow`, `advisorResearchSkip`, `advisorResearchClose`, `advisorResearchDismiss`, `advisorOpenResultNow`, `advisorResearchMessage`
- Advisor research field IDs: `advisorResearchCurrentVehicle`, `advisorResearchOwnershipDuration`, `advisorResearchMileageBucket`, `advisorResearchFaultTypeWrap`, `advisorResearchMajorFaultType`, `advisorResearchMaintenanceCostBucket`, `advisorResearchActualConsumption`, `advisorResearchSatisfactionScore`, `advisorResearchWouldBuyAgain`
- Weights: `w_reliability`, `w_resale`, `w_fuel`, `w_performance`, `w_comfort`
- Shared research modal hook: `researchConsentModal`
- History bootstrap JSON: `advisor-history-profile`, `advisor-history-result`, `advisor-history-id`

### Classes

- `.spinner`
- `.button-text`

### Name-based selectors that also matter

- `fuels_he`
- `gears_he`
- `turbo_choice_he`
- `safety_required_radio`
- `consider_supply`
- `advisorResearchMajorFaults`

## API endpoints called by current frontend JS

### `static/script.js`

- `GET /api/timing/estimate?kind=analyze`
- `POST /api/legal/accept`
- `POST /analyze`
- `GET /api/history/list`
- `GET /api/history/item/<id>`
- `POST /api/feedback`

### `static/recommendations.js`

- `GET /api/timing/estimate?kind=advisor`
- `POST /api/legal/accept`
- `POST /advisor_api`

### `static/research.js`

- `POST /api/research/consent`
- `POST /api/research/responses`

### Owner-profile note

- No current call to `/api/owner-profile` exists in `script.js`, `recommendations.js`, or `research.js`
- Owner-profile flow is not part of the two large files being split in this phase

## Legal modal / gating functions

### Reliability (`script.js`)

- `validateLegal()` — blocks submit until `#legal-confirm` is checked
- `ensureLegalAcceptance()` — persists legal acceptance via `POST /api/legal/accept`
- `ensureReliabilityResultAcknowledgement()` — blocks opening results for authenticated users until the result-ack modal is satisfied
- `confirmReliabilityResultAcknowledgement()` — persists feature consent through `POST /api/legal/accept` with `feature_consents`
- `closeReliabilityResultAckModal()` — hides the result acknowledgement modal

### Advisor (`recommendations.js`)

- submit path requires `#advisor-consent` to be checked before request dispatch
- `ensureLegalAcceptance()` — persists legal acceptance via `POST /api/legal/accept`
- no separate advisor result-ack modal exists today

### Shared research consent modal (`research.js`)

- `window.YedaResearch.createClient(...)`
- client methods: `ensureConsent(source)` and `saveResponses(payload)`
- modal helpers inside the shared client: `openModal()`, `closeModal()`, `acceptConsent(source)`, `showError(message)`, `hideError()`

## Research / consent flow functions

### Reliability (`script.js`)

- `researchPromptSeenKey()`
- `hasSeenResearchPrompt()`
- `markResearchPromptSeen()`
- `setReliabilityResearchMessage()`
- `hideReliabilityResearchCard()`
- `resetReliabilityResearchCard()`
- `showReliabilityReadyPanel()`
- `showReliabilityResearchCard()`
- `closeReliabilityResearch()`
- `openReliabilityResearchForm()`
- `resetResultFlowState()`
- reliability research submit handler saves responses through `window.YedaResearch`

### Advisor (`recommendations.js`)

- `researchPromptSeenKey()`
- `hasSeenResearchPrompt()`
- `markResearchPromptSeen()`
- `setResearchMessage()`
- `hideAdvisorResearchCard()`
- `syncAdvisorResearchFaultTypeVisibility()`
- `resetAdvisorResearchCard()`
- `showAdvisorReadyPanel()`
- `showAdvisorResearchCard()`
- `closeAdvisorResearch()`
- `openAdvisorResearchForm()`
- `resetAdvisorResultFlowState()`
- `buildAdvisorResearchResponses()`
- advisor research submit handler saves responses through `window.YedaResearch`

## Structural split observations for later phases

- `script.js` currently mixes:
  - fetch/error helpers
  - timing banner logic
  - legal gating + result acknowledgement
  - reliability form state
  - reliability result rendering
  - research flow
  - history compare helpers
  - feedback CTA export
  - example-page bootstrap
- `recommendations.js` currently mixes:
  - fetch/error helpers
  - timing banner logic
  - legal gating
  - advisor form payload building
  - history bootstrap application
  - result rendering
  - research flow
- Lowest-risk shared extraction candidates for Phase 2 are confirmed to be:
  - fetch/API helpers
  - request-aware error display helpers
  - timing banner helpers
  - legal acceptance helpers
  - loading state helpers

## Constraints confirmed by audit

- `research.js` must remain loaded before split reliability/advisor modules that call `window.YedaResearch`
- `script.js` must preserve the globals consumed by `reliability_app.html` and `compare.html`
- `example.html` depends on `script.js` bootstrap behavior for injected `#example-data`
- current live endpoint contracts in the large JS files are `/analyze` and `/advisor_api`, not the `/api/analyze` / `/api/advisor/analyze` names listed in older docs
