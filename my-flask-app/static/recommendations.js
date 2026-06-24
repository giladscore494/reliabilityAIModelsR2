// /static/recommendations.js
// לוגיקת צד לקוח למנוע ההמלצות (Car Advisor / Gemini 3)
// XSS Protection: All AI-generated content is HTML-escaped on the backend via sanitization.py
// before being sent to the frontend. Template literals are safe to use with innerHTML.

(function () {
    const form = document.getElementById('advisor-form');
    const submitBtn = document.getElementById('advisor-submit');
    const resultsSection = document.getElementById('advisor-results');
    const resultReadyPanel = document.getElementById('advisorResultReadyPanel');
    const openResultButton = document.getElementById('advisorOpenResultButton');
    const queriesEl = document.getElementById('advisor-search-queries');
    const tableWrapper = document.getElementById('advisor-table-wrapper');
    const errorEl = document.getElementById('advisor-error');
    const consentCheckbox = document.getElementById('advisor-consent');
    const researchSectionEl = document.getElementById('advisorResearchSection');
    const researchFormWrapEl = document.getElementById('advisorResearchFormWrap');
    const researchFormEl = document.getElementById('advisorResearchForm');
    const researchAnswerNowBtn = document.getElementById('advisorResearchAnswerNow');
    const researchSkipBtn = document.getElementById('advisorResearchSkip');
    const researchCloseBtn = document.getElementById('advisorResearchClose');
    const researchDismissBtn = document.getElementById('advisorResearchDismiss');
    const openResultNowBtn = document.getElementById('advisorOpenResultNow');
    const researchMessageEl = document.getElementById('advisorResearchMessage');
    const researchCurrentVehicleEl = document.getElementById('advisorResearchCurrentVehicle');
    const researchOwnershipDurationEl = document.getElementById('advisorResearchOwnershipDuration');
    const researchMileageBucketEl = document.getElementById('advisorResearchMileageBucket');
    const researchMajorFaultTypeWrapEl = document.getElementById('advisorResearchFaultTypeWrap');
    const researchMajorFaultTypeEl = document.getElementById('advisorResearchMajorFaultType');
    const researchMaintenanceCostBucketEl = document.getElementById('advisorResearchMaintenanceCostBucket');
    const researchActualConsumptionEl = document.getElementById('advisorResearchActualConsumption');
    const researchSatisfactionScoreEl = document.getElementById('advisorResearchSatisfactionScore');
    const researchWouldBuyAgainEl = document.getElementById('advisorResearchWouldBuyAgain');
    const researchClient = window.YedaResearch
        ? window.YedaResearch.createClient({
            accepted: document.getElementById('researchConsentModal')?.dataset.accepted === 'true',
            defaultSource: 'advisor_after_result',
            onConsentOpen: function () {
                trackAnalytics('research_consent_opened', { flow_type: 'advisor' });
            },
            onConsentAccepted: function () {
                trackAnalytics('research_consented', { flow_type: 'advisor' });
            }
        })
        : null;
    const advisorCopy = {
        fitFallback: 'התאמת העדפות גבוהה כאן משקפת התאמה לתקציב, לשימוש ולהעדפות שסימנת בשאלון בלבד.',
        caveatFallback: 'אין כאן אישור קנייה אוטומטי: לפני החלטה בדוק היסטוריית טיפולים, היסטוריית ביטוח, מצב בפועל ובדיקת קנייה מקצועית.'
    };
    let isLoading = false;
    let isResultReady = false;
    let isResultOpen = false;
    let currentHistoryId = null;
    let researchCardVisible = false;
    let researchFormOpen = false;
    let legalAccepted = false;
    let advisorResearchCardTrackedForHistory = null;
    let resultReadyPanelTrackedForHistory = null;

    const profileSummaryEl = document.getElementById('advisor-profile-summary');
    const highlightCardsEl = document.getElementById('advisor-highlight-cards');

    // === Advisor flow shell + state machine (Run 1 prototype parity) ===
    const advisorFlowHeader = document.getElementById('advisorFlowHeader');
    const advisorStepNumEl = document.getElementById('advisorStepNum');
    const advisorStepLabelEl = document.getElementById('advisorStepLabel');
    const advisorProgressFill = document.getElementById('advisorProgressFill');
    const advisorFormScreen = document.getElementById('advisorFormScreen');
    const advisorPreferenceSummary = document.getElementById('advisorPreferenceSummary');
    const advisorPreferenceCards = document.getElementById('advisorPreferenceCards');
    const advisorSummaryError = document.getElementById('advisorSummaryError');
    const advisorAnalyzingScreen = document.getElementById('advisorAnalyzingScreen');
    const advisorAnalyzeBtn = document.getElementById('advisorAnalyzeBtn');
    const advisorBackToEditBtn = document.getElementById('advisorBackToEditBtn');
    const advisorRestartBtn = document.getElementById('advisorRestartBtn');

    // payload saved on summary, sent only after the user clicks "נתח את ההעדפות שלי"
    let pendingAdvisorPayload = null;
    let currentAdvisorStep = 'form';
    let analyzeTimer = null;

    const ADVISOR_STEP_META = {
        form:      { num: 1, label: 'פרופיל נהג' },
        summary:   { num: 2, label: 'סיכום העדפות' },
        analyzing: { num: 3, label: 'ניתוח AI' },
        results:   { num: 4, label: 'המלצות' },
        compare:   { num: 5, label: 'פירוט / השוואה' },
        details:   { num: 5, label: 'פירוט / השוואה' }
    };

    const advisorFlowScreens = {
        form: advisorFormScreen,
        summary: advisorPreferenceSummary,
        analyzing: advisorAnalyzingScreen
    };

    function syncAdvisorFlowHeaderTop() {
        if (!advisorFlowHeader) return;
        const nav = document.querySelector('.yr-header');
        advisorFlowHeader.style.top = (nav ? nav.offsetHeight : 0) + 'px';
    }

    function setAdvisorStep(stepName) {
        const meta = ADVISOR_STEP_META[stepName] || ADVISOR_STEP_META.form;
        currentAdvisorStep = stepName;
        if (advisorStepNumEl) advisorStepNumEl.textContent = String(meta.num);
        if (advisorStepLabelEl) advisorStepLabelEl.textContent = meta.label;
        if (advisorProgressFill) advisorProgressFill.style.width = (meta.num / 5 * 100).toFixed(1) + '%';
        Object.keys(advisorFlowScreens).forEach((key) => {
            const el = advisorFlowScreens[key];
            if (el) el.classList.toggle('yr-flow-hidden', key !== stepName);
        });
    }

    if (!form) return;

    function escapeHtml(value) {
        if (value === null || value === undefined) return '';
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function sanitizeUrl(url) {
        if (!url) return '';
        var trimmed = url.replace(/^\s+/, '');
        if (/^https?:\/\//i.test(trimmed)) return trimmed;
        if (/^mailto:/i.test(trimmed)) return trimmed;
        return '';
    }

    function trackAnalytics(eventName, properties) {
        if (typeof window.posthog === 'undefined' || typeof window.posthog.capture !== 'function') {
            return;
        }
        try {
            window.posthog.capture(eventName, properties || {});
        } catch (err) {
            console.warn('analytics capture failed', err);
        }
    }

    const getCSRFToken = () => {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    };

    async function safeFetchJson(url, options = {}) {
        const headers = new Headers(options.headers || {});
        if (!headers.has('Accept')) {
            headers.set('Accept', 'application/json');
        }
        const hasBody = options.body !== undefined && options.body !== null;
        const isFormBody = options.body instanceof FormData || options.body instanceof URLSearchParams;
        if (hasBody && !isFormBody && !headers.has('Content-Type')) {
            headers.set('Content-Type', 'application/json');
        }
        const csrfToken = getCSRFToken();
        if (csrfToken && !headers.has('X-CSRF-Token')) {
            headers.set('X-CSRF-Token', csrfToken);
        }
        options.headers = headers;
        let response;
        try {
            response = await fetch(url, options);
        } catch (err) {
            return {
                ok: false,
                error: { code: 'NETWORK_ERROR', message: 'שגיאת רשת או חיבור.', details: { message: err.message } },
                request_id: null
            };
        }

        const contentType = response.headers.get('content-type') || '';
        let parsed = null;
        let textBody = '';
        if (contentType.includes('application/json')) {
            try {
                parsed = await response.json();
            } catch (e) {
                parsed = null;
            }
        }
        if (!parsed) {
            try {
                textBody = await response.text();
            } catch (e) {
                textBody = '';
            }
        }

        const requestId = (parsed && parsed.request_id) || response.headers.get('X-Request-ID');
        if (!response.ok) {
            const snippet = parsed ? JSON.stringify(parsed).slice(0, 300) : (textBody || '').slice(0, 300);
            const errObj = parsed && parsed.error ? parsed.error : null;
            const errorCode = (errObj && errObj.code) || (typeof errObj === 'string' ? errObj : null) || 'HTTP_ERROR';
            const errorMessage = (errObj && errObj.message) || (parsed && parsed.message) || response.statusText || 'שגיאה בבקשה';
            return {
                ok: false,
                error: {
                    code: errorCode,
                    message: errorMessage,
                    details: { status: response.status, body_snippet: snippet }
                },
                request_id: requestId
            };
        }

        if (parsed) {
            parsed.request_id = parsed.request_id || requestId;
            return parsed;
        }

        return { ok: true, data: textBody, request_id: requestId };
    }

    function showRequestAwareError(message, requestId) {
        const suffix = requestId ? ` (ID: ${escapeHtml(requestId)})` : '';
        if (errorEl) {
            errorEl.textContent = `${message}${suffix}`;
            errorEl.classList.remove('hidden');
        } else {
            console.error('[Advisor]', message, suffix);
        }
    }

    function researchPromptSeenKey(flowType, historyId) {
        return `research_prompt_seen_${flowType}_${historyId}`;
    }

    function hasSeenResearchPrompt(flowType, historyId) {
        if (!historyId) return false;
        try {
            return sessionStorage.getItem(researchPromptSeenKey(flowType, historyId)) === '1';
        } catch (err) {
            return false;
        }
    }

    function markResearchPromptSeen(flowType, historyId) {
        if (!historyId) return;
        try {
            sessionStorage.setItem(researchPromptSeenKey(flowType, historyId), '1');
        } catch (err) {}
    }

    async function ensureLegalAcceptance() {
        if (legalAccepted) return true;
        const res = await safeFetchJson('/api/legal/accept', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ legal_confirm: true })
        });
        if (res && res.ok) {
            legalAccepted = true;
            return true;
        }
        const message = (res && res.error && res.error.message) || 'נדרש לאשר את תנאי השימוש והפרטיות.';
        showRequestAwareError(message, res && res.request_id);
        return false;
    }

    // Timing Banner Elements
    const timingBanner = document.getElementById('advisorTimingBanner');
    const elapsedTimeEl = document.getElementById('advisorElapsedTime');
    const etaTextEl = document.getElementById('advisorEtaText');
    const statusTextEl = document.getElementById('advisorStatusText');
    const progressRing = document.getElementById('advisorProgressRing');
    const RING_CIRCUMFERENCE = 339.292; // 2 * PI * 54

    let timingInterval = null;
    let timingStartTime = null;

    function showTimingBanner(kind = 'advisor') {
        if (!timingBanner) return;
        
        // Fetch ETA estimate
        safeFetchJson(`/api/timing/estimate?kind=${kind}`, {
            method: 'GET',
            credentials: 'include'
        }).then(res => {
            if (res && res.ok) {
                const data = res.data || {};
                const p75_ms = data.p75_ms || 15000;
                const count = data.sample_size || 0;
                
                if (etaTextEl) {
                    if (count > 0) {
                        etaTextEl.textContent = `זמן משוער: ~${Math.ceil(p75_ms / 1000)} שניות (מבוסס על ${count} שאלונים)`;
                    } else {
                        etaTextEl.textContent = `זמן משוער: ~${Math.ceil(p75_ms / 1000)} שניות`;
                    }
                }
                
                // Start timer
                timingStartTime = performance.now();
                timingBanner.classList.remove('hidden');
                
                // Update timer every 100ms
                timingInterval = setInterval(() => {
                    const elapsed = Math.floor((performance.now() - timingStartTime) / 1000);
                    if (elapsedTimeEl) elapsedTimeEl.textContent = elapsed;
                    
                    // Update progress ring (0 to 100% based on p75_ms)
                    const elapsedMs = performance.now() - timingStartTime;
                    const progress = Math.min(1, elapsedMs / p75_ms);
                    const offset = RING_CIRCUMFERENCE * (1 - progress);
                    if (progressRing) {
                        progressRing.style.strokeDashoffset = offset;
                        
                        // Rainbow hue cycling (0-360 degrees over p75_ms)
                        const hue = (elapsedMs / p75_ms) * 360;
                        progressRing.style.stroke = `hsl(${hue % 360}, 80%, 60%)`;
                    }
                }, 100);
            }
        }).catch(err => {
            console.warn('Failed to fetch timing estimate:', err);
            // Show banner anyway with default
            timingStartTime = performance.now();
            timingBanner.classList.remove('hidden');
            if (etaTextEl) etaTextEl.textContent = 'זמן משוער: ~15 שניות';
            
            timingInterval = setInterval(() => {
                const elapsed = Math.floor((performance.now() - timingStartTime) / 1000);
                if (elapsedTimeEl) elapsedTimeEl.textContent = elapsed;
                
                const elapsedMs = performance.now() - timingStartTime;
                const progress = Math.min(1, elapsedMs / 15000);
                const offset = RING_CIRCUMFERENCE * (1 - progress);
                if (progressRing) {
                    progressRing.style.strokeDashoffset = offset;
                    const hue = (elapsedMs / 15000) * 360;
                    progressRing.style.stroke = `hsl(${hue % 360}, 80%, 60%)`;
                }
            }, 100);
        });
    }

    function hideTimingBanner(showCompletionMessage = true) {
        if (timingInterval) {
            clearInterval(timingInterval);
            timingInterval = null;
        }
        
        if (showCompletionMessage && timingStartTime && timingBanner && !timingBanner.classList.contains('hidden')) {
            const finalElapsed = Math.floor((performance.now() - timingStartTime) / 1000);
            if (statusTextEl) statusTextEl.textContent = `הסתיים תוך ${finalElapsed} שניות`;
            
            // Hide after 1.5 seconds
            setTimeout(() => {
                if (timingBanner) timingBanner.classList.add('hidden');
                if (statusTextEl) statusTextEl.textContent = 'מעבד...';
                if (progressRing) {
                    progressRing.style.strokeDashoffset = RING_CIRCUMFERENCE;
                    progressRing.style.stroke = 'url(#advisorRainbowGradient)';
                }
            }, 1500);
        } else {
            if (timingBanner) timingBanner.classList.add('hidden');
            if (statusTextEl) statusTextEl.textContent = 'מעבד...';
            if (progressRing) {
                progressRing.style.strokeDashoffset = RING_CIRCUMFERENCE;
                progressRing.style.stroke = 'url(#advisorRainbowGradient)';
            }
        }
        
        timingStartTime = null;
    }

    // === מיפוי שם פרמטרי ה-method לעברית (כמו ב-Python) ===
    const methodLabelMap = {
        fuel_method: "שיטת חישוב צריכת דלק/חשמל",
        fee_method: "שיטת חישוב אגרה",
        reliability_method: "שיטת אינדיקציית תחזוקה",
        maintenance_method: "שיטת חישוב עלות אחזקה",
        safety_method: "שיטת חישוב בטיחות",
        insurance_method: "שיטת חישוב ביטוח",
        resale_method: "שיטת חישוב שמירת ערך",
        performance_method: "שיטת חישוב ביצועים",
        comfort_method: "שיטת חישוב נוחות",
        suitability_method: "שיטת חישוב התאמה",
        supply_method: "שיטת קביעת היצע"
    };

    function setSubmitting(isSubmitting) {
        if (!submitBtn) return;
        const spinner = submitBtn.querySelector('.spinner');
        const textSpan = submitBtn.querySelector('.button-text');
        submitBtn.disabled = isSubmitting;
        if (spinner) spinner.classList.toggle('hidden', !isSubmitting);
        if (textSpan) textSpan.classList.toggle('opacity-60', isSubmitting);
    }

    function getCheckedValues(name) {
        return Array.from(form.querySelectorAll(`input[name="${name}"]:checked`)).map(el => el.value);
    }

    function getRadioValue(name, fallback) {
        const el = form.querySelector(`input[name="${name}"]:checked`);
        return el ? el.value : fallback;
    }

    function setCheckboxGroup(name, values) {
        const set = new Set((Array.isArray(values) ? values : []).map(String));
        form.querySelectorAll(`input[name="${name}"]`).forEach((el) => {
            el.checked = set.has(el.value);
        });
    }

    function setRadioGroup(name, value) {
        if (value === undefined || value === null) return;
        const radio = form.querySelector(`input[name="${name}"][value="${String(value)}"]`);
        if (radio) radio.checked = true;
    }

    function applyHistoryProfile(profile) {
        if (!profile || typeof profile !== 'object') return;
        const fuelMap = { gasoline: 'בנזין', diesel: 'דיזל', hybrid: 'היברידי', electric: 'חשמלי', ev: 'חשמלי' };
        const gearMap = { automatic: 'אוטומטית', manual: 'ידנית' };
        const getVal = (key, fallback = '') => (profile[key] ?? fallback);

        if (Array.isArray(profile.budget_nis) && profile.budget_nis.length >= 2) {
            form.budget_min.value = profile.budget_nis[0];
            form.budget_max.value = profile.budget_nis[1];
        }
        if (Array.isArray(profile.years) && profile.years.length >= 2) {
            form.year_min.value = profile.years[0];
            form.year_max.value = profile.years[1];
        }
        setCheckboxGroup('fuels_he', (profile.fuel || []).map((f) => fuelMap[String(f).toLowerCase()] || String(f)));
        setCheckboxGroup('gears_he', (profile.gear || []).map((g) => gearMap[String(g).toLowerCase()] || String(g)));
        setRadioGroup('turbo_choice_he', profile.turbo_required === true ? 'כן' : profile.turbo_required === false ? 'לא' : 'לא משנה');

        form.main_use.value = getVal('main_use', form.main_use.value);
        form.annual_km.value = getVal('annual_km', form.annual_km.value);
        form.driver_age.value = getVal('driver_age', form.driver_age.value);
        form.license_years.value = getVal('license_years', form.license_years.value);
        form.driver_gender.value = getVal('driver_gender', form.driver_gender.value);
        form.body_style.value = getVal('body_style', form.body_style.value);
        form.driving_style.value = getVal('driving_style', form.driving_style.value);
        form.seats_choice.value = getVal('seats', form.seats_choice.value);
        form.family_size.value = getVal('family_size', form.family_size.value);
        form.cargo_need.value = getVal('cargo_need', form.cargo_need.value);
        form.insurance_history.value = getVal('insurance_history', form.insurance_history.value);
        form.violations.value = getVal('violations', form.violations.value);
        form.trim_level.value = getVal('trim_level', form.trim_level.value);
        form.excluded_colors.value = Array.isArray(profile.excluded_colors) ? profile.excluded_colors.join(', ') : getVal('excluded_colors', '');
        form.fuel_price.value = getVal('fuel_price_nis_per_liter', form.fuel_price.value);
        form.electricity_price.value = getVal('electricity_price_nis_per_kwh', form.electricity_price.value);
        setRadioGroup('safety_required_radio', getVal('safety_required', 'כן'));
        setRadioGroup('consider_supply', profile.consider_market_supply === false ? 'לא' : 'כן');

        const weights = profile.weights || {};
        if (typeof weights === 'object') {
            document.getElementById('w_reliability').value = weights.reliability ?? document.getElementById('w_reliability').value;
            document.getElementById('w_resale').value = weights.resale ?? document.getElementById('w_resale').value;
            document.getElementById('w_fuel').value = weights.fuel ?? document.getElementById('w_fuel').value;
            document.getElementById('w_performance').value = weights.performance ?? document.getElementById('w_performance').value;
            document.getElementById('w_comfort').value = weights.comfort ?? document.getElementById('w_comfort').value;
        }
    }

    function buildPayload() {
        const fuels_he = getCheckedValues('fuels_he');
        const gears_he = getCheckedValues('gears_he');
        const turbo_choice_he = getRadioValue('turbo_choice_he', 'לא משנה');
        const safety_required_radio = getRadioValue('safety_required_radio', 'כן');
        const consider_supply = getRadioValue('consider_supply', 'כן');

        const payload = {
            budget_min: parseFloat(form.budget_min.value || '0'),
            budget_max: parseFloat(form.budget_max.value || '0'),
            year_min: parseInt(form.year_min.value || '2000', 10),
            year_max: parseInt(form.year_max.value || '2025', 10),

            fuels_he,
            gears_he,
            turbo_choice_he,

            main_use: form.main_use.value || '',
            annual_km: parseInt(form.annual_km.value || '15000', 10),
            driver_age: parseInt(form.driver_age.value || '21', 10),
            license_years: parseInt(form.license_years.value || '0', 10),

            driver_gender: form.driver_gender.value || 'זכר',
            body_style: form.body_style.value || 'כללי',
            driving_style: form.driving_style.value || 'רגוע ונינוח',
            seats_choice: form.seats_choice.value || '5',

            family_size: form.family_size.value || '1-2',
            cargo_need: form.cargo_need.value || 'בינוני',

            insurance_history: form.insurance_history.value || '',
            violations: form.violations.value || 'אין',

            safety_required_radio,
            trim_level: form.trim_level.value || 'סטנדרטי',

            consider_supply,
            fuel_price: parseFloat(form.fuel_price.value || '7.0'),
            electricity_price: parseFloat(form.electricity_price.value || '0.65'),
            excluded_colors: form.excluded_colors.value || '',

            // משקלים
            weights: {
                reliability: parseInt(document.getElementById('w_reliability').value || '5', 10),
                resale: parseInt(document.getElementById('w_resale').value || '3', 10),
                fuel: parseInt(document.getElementById('w_fuel').value || '4', 10),
                performance: parseInt(document.getElementById('w_performance').value || '2', 10),
                comfort: parseInt(document.getElementById('w_comfort').value || '3', 10)
            }
        };

        return payload;
    }

    function setResearchMessage(message, tone) {
        if (!researchMessageEl) return;
        if (!message) {
            researchMessageEl.textContent = '';
            researchMessageEl.classList.add('hidden');
            researchMessageEl.classList.remove('text-emerald-300', 'text-amber-300', 'text-red-300');
            researchMessageEl.classList.add('text-emerald-300');
            return;
        }
        researchMessageEl.textContent = message;
        researchMessageEl.classList.remove('hidden', 'text-emerald-300', 'text-amber-300', 'text-red-300');
        researchMessageEl.classList.add(
            tone === 'error' ? 'text-red-300' : tone === 'warning' ? 'text-amber-300' : 'text-emerald-300'
        );
    }

    function scrollToAdvisorResult() {
        resultsSection?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function hideAdvisorResearchCard() {
        researchSectionEl?.classList.add('hidden');
        researchFormWrapEl?.classList.add('hidden');
        researchCardVisible = false;
        researchFormOpen = false;
    }

    function getSelectedMajorFaultValue() {
        return researchFormEl?.querySelector('input[name="advisorResearchMajorFaults"]:checked')?.value || '';
    }

    function syncAdvisorResearchFaultTypeVisibility() {
        if (!researchMajorFaultTypeWrapEl || !researchMajorFaultTypeEl) return;
        const shouldShow = getSelectedMajorFaultValue() === 'yes';
        researchMajorFaultTypeWrapEl.classList.toggle('hidden', !shouldShow);
        if (!shouldShow) {
            researchMajorFaultTypeEl.value = '';
        }
    }

    function resetAdvisorResearchCard() {
        hideAdvisorResearchCard();
        researchFormEl?.reset();
        syncAdvisorResearchFaultTypeVisibility();
        setResearchMessage('', 'success');
    }

    function showAdvisorReadyPanel() {
        if (!resultReadyPanel) return;
        resultReadyPanel.classList.remove('hidden');
        if (currentHistoryId && resultReadyPanelTrackedForHistory !== currentHistoryId) {
            trackAnalytics('result_ready_panel_shown', {
                flow_type: 'advisor',
                advisor_history_id: currentHistoryId
            });
            resultReadyPanelTrackedForHistory = currentHistoryId;
        }
    }

    function openAdvisorResult(options = {}) {
        if (!resultsSection || !isResultReady) return;
        const userInitiated = options.userInitiated !== false;
        const alreadyOpen = isResultOpen;
        isResultOpen = true;
        resultsSection.classList.remove('hidden');
        if (resultReadyPanel) {
            resultReadyPanel.classList.remove('hidden');
        }
        scrollToAdvisorResult();
        if (userInitiated && !alreadyOpen) {
            trackAnalytics('result_opened', {
                flow_type: 'advisor',
                advisor_history_id: currentHistoryId
            });
        }
    }

    function closeAdvisorResult() {
        isResultOpen = false;
        resultsSection?.classList.add('hidden');
    }

    function showAdvisorResearchCard() {
        if (!researchSectionEl || !currentHistoryId || hasSeenResearchPrompt('advisor', currentHistoryId)) {
            hideAdvisorResearchCard();
            return;
        }
        researchSectionEl.classList.remove('hidden');
        researchCardVisible = true;
        if (advisorResearchCardTrackedForHistory !== currentHistoryId) {
            trackAnalytics('research_card_shown', {
                flow_type: 'advisor',
                advisor_history_id: currentHistoryId
            });
            advisorResearchCardTrackedForHistory = currentHistoryId;
        }
    }

    function closeAdvisorResearch(options = {}) {
        const reason = options.reason || 'closed';
        const trackSkipped = options.trackSkipped === true;
        if (currentHistoryId) {
            markResearchPromptSeen('advisor', currentHistoryId);
            trackAnalytics('research_card_closed', {
                flow_type: 'advisor',
                advisor_history_id: currentHistoryId,
                reason
            });
            if (trackSkipped) {
                trackAnalytics('research_skipped', {
                    flow_type: 'advisor',
                    advisor_history_id: currentHistoryId
                });
            }
        }
        hideAdvisorResearchCard();
        if (options.openResult === true) {
            openAdvisorResult({ userInitiated: true });
        }
    }

    function openAdvisorResearchForm() {
        if (!currentHistoryId || !researchFormWrapEl) return;
        researchFormWrapEl.classList.remove('hidden');
        researchFormOpen = true;
        setResearchMessage('', 'success');
        trackAnalytics('research_started', {
            flow_type: 'advisor',
            advisor_history_id: currentHistoryId
        });
        researchCurrentVehicleEl?.focus();
    }

    function resetAdvisorResultFlowState() {
        isLoading = false;
        isResultReady = false;
        isResultOpen = false;
        currentHistoryId = null;
        researchCardVisible = false;
        researchFormOpen = false;
        resultReadyPanel?.classList.add('hidden');
        closeAdvisorResult();
        resetAdvisorResearchCard();
    }

    function buildAdvisorResearchResponses() {
        const responses = [];
        const currentVehicle = researchCurrentVehicleEl?.value?.trim() || '';
        const ownershipDuration = researchOwnershipDurationEl?.value || '';
        const mileageBucket = researchMileageBucketEl?.value || '';
        const majorFaults = getSelectedMajorFaultValue();
        const majorFaultType = researchMajorFaultTypeEl?.value || '';
        const maintenanceCostBucket = researchMaintenanceCostBucketEl?.value || '';
        const actualConsumption = researchActualConsumptionEl?.value || '';
        const satisfactionScore = researchSatisfactionScoreEl?.value || '';
        const wouldBuyAgain = researchWouldBuyAgainEl?.value || '';

        if (currentVehicle) {
            responses.push({
                question_code: 'current_vehicle',
                response: { current_vehicle: currentVehicle }
            });
        }
        if (ownershipDuration) {
            responses.push({
                question_code: 'ownership_duration',
                response: { ownership_duration: ownershipDuration }
            });
        }
        if (mileageBucket) {
            responses.push({
                question_code: 'mileage_bucket',
                response: { mileage_bucket: mileageBucket }
            });
        }
        if (majorFaults) {
            responses.push({
                question_code: 'had_major_faults',
                response: { had_major_faults: majorFaults === 'yes' }
            });
        }
        if (majorFaultType) {
            responses.push({
                question_code: 'major_fault_type',
                response: { major_fault_type: majorFaultType }
            });
        }
        if (maintenanceCostBucket) {
            responses.push({
                question_code: 'maintenance_cost_bucket',
                response: { maintenance_cost_bucket: maintenanceCostBucket }
            });
        }
        if (actualConsumption !== '') {
            responses.push({
                question_code: 'actual_fuel_consumption',
                response: { actual_consumption: actualConsumption }
            });
        }
        if (satisfactionScore) {
            responses.push({
                question_code: 'satisfaction_score',
                response: { satisfaction_score: satisfactionScore }
            });
        }
        if (wouldBuyAgain) {
            responses.push({
                question_code: 'would_buy_again',
                response: { would_buy_again: wouldBuyAgain }
            });
        }

        return responses;
    }

    function formatPriceRange(range) {
        if (!range) return '';
        if (Array.isArray(range)) {
            if (range.length === 2) return `${range[0]}–${range[1]} ₪`;
            return range.join(' / ');
        }
        return String(range);
    }

    function safeNum(val, decimals = 0) {
        const n = Number(val);
        if (Number.isNaN(n)) return '';
        return n.toFixed(decimals);
    }

    function isEVFuel(fuelStr) {
        if (!fuelStr) return false;
        const f = String(fuelStr).toLowerCase();
        return f.includes('חשמל') || f.includes('electric') || f.includes('ev');
    }

    // --- סיכום פרופיל משתמש אחרי קבלת התוצאות ---
    function renderProfileSummary() {
        if (!profileSummaryEl) return;

        const budgetMin = form.budget_min.value ? parseInt(form.budget_min.value, 10).toLocaleString('he-IL') + ' ₪' : 'לא צוין';
        const budgetMax = form.budget_max.value ? parseInt(form.budget_max.value, 10).toLocaleString('he-IL') + ' ₪' : 'לא צוין';
        const yearMin = form.year_min.value || 'לא צוין';
        const yearMax = form.year_max.value || 'לא צוין';

        const driverAge = form.driver_age.value || 'לא צוין';
        const licenseYears = form.license_years.value || 'לא צוין';
        const annualKm = form.annual_km.value ? parseInt(form.annual_km.value, 10).toLocaleString('he-IL') + ' ק״מ' : 'לא צוין';

        const mainUse = form.main_use.value || 'לא צוין שימוש עיקרי';
        const familySize = form.family_size.value || 'לא צוין';
        const seats = form.seats_choice.value || 'לא צוין';

        const fuels = getCheckedValues('fuels_he');
        const gears = getCheckedValues('gears_he');
        const drivingStyle = form.driving_style.value || 'לא צוין';
        const bodyStyle = form.body_style.value || 'כללי';

        const fuelsText = fuels.length ? escapeHtml(fuels.join(', ')) : 'לא צוין';
        const gearsText = gears.length ? escapeHtml(gears.join(', ')) : 'לא צוין';

        const wReliability = document.getElementById('w_reliability')?.value || '5';
        const wFuel = document.getElementById('w_fuel')?.value || '4';
        const wResale = document.getElementById('w_resale')?.value || '3';
        const wPerf = document.getElementById('w_performance')?.value || '2';
        const wComfort = document.getElementById('w_comfort')?.value || '3';

        const safe = (v) => escapeHtml(v);

        // Safe innerHTML: values are escaped before interpolation.
        profileSummaryEl.innerHTML = `
            <div style="font-size:12px;font-weight:600;color:var(--yr-muted);margin-bottom:10px">פרופיל הנהג שהוזן</div>
            <div class="flex flex-wrap gap-2 mb-2">
                <span class="yr-profile-chip">תקציב: ${safe(budgetMin)} – ${safe(budgetMax)}</span>
                <span class="yr-profile-chip">שנים: ${safe(yearMin)}–${safe(yearMax)}</span>
                <span class="yr-profile-chip">ק״מ שנתי: ${safe(annualKm)}</span>
            </div>
            <div class="flex flex-wrap gap-2 mb-2">
                <span class="yr-profile-chip">גיל נהג: ${safe(driverAge)}</span>
                <span class="yr-profile-chip">ותק רישיון: ${safe(licenseYears)} שנים</span>
                <span class="yr-profile-chip">משפחה: ${safe(familySize)}, ${safe(seats)} מושבים</span>
            </div>
            <div class="flex flex-wrap gap-2 mb-2">
                <span class="yr-profile-chip">שימוש: ${safe(mainUse)}</span>
                <span class="yr-profile-chip">סגנון נהיגה: ${safe(drivingStyle)}</span>
                <span class="yr-profile-chip">מרכב מועדף: ${safe(bodyStyle)}</span>
            </div>
            <div class="flex flex-wrap gap-2 mt-1">
                <span class="yr-profile-chip--accent yr-profile-chip">משקל תחזוקה: ${safe(wReliability)}/5</span>
                <span class="yr-profile-chip--accent yr-profile-chip">חיסכון בדלק: ${safe(wFuel)}/5</span>
                <span class="yr-profile-chip--accent yr-profile-chip">שמירת ערך: ${safe(wResale)}/5</span>
                <span class="yr-profile-chip--accent yr-profile-chip">ביצועים: ${safe(wPerf)}/5</span>
                <span class="yr-profile-chip--accent yr-profile-chip">נוחות: ${safe(wComfort)}/5</span>
            </div>
            <div style="margin-top:8px;font-size:11px;color:var(--yr-muted-2)">
                העדפות דלק: ${fuelsText} · גיר: ${gearsText}
            </div>
        `;
    }

    // --- כרטיסי Highlight (התאמת העדפות, עלות אחזקה, סיכון תחזוקה אם קיים) ---
    function getReliabilityScore(car) {
        const candidates = ['reliability_score', 'reliability_index', 'reliability'];
        for (const key of candidates) {
            if (car[key] != null) {
                const n = Number(car[key]);
                if (!Number.isNaN(n)) return n;
            }
        }
        return null;
    }

    function getReliabilityGrade(score) {
        if (score == null || Number.isNaN(Number(score))) {
            return { label: 'לא ידוע', className: 'yr-grade yr-grade--unknown' };
        }
        if (score >= 7) {
            return { label: 'נמוכה', className: 'yr-grade yr-grade--low' };
        }
        if (score >= 4) {
            return { label: 'בינונית', className: 'yr-grade yr-grade--mid' };
        }
        return { label: 'גבוהה', className: 'yr-grade yr-grade--high' };
    }

    function buildScoreGaugeSvg(value, size, labelText) {
        var s = size || 120;
        var r = 54;
        var circ = 2 * Math.PI * r;
        var clamped = Math.max(0, Math.min(100, value || 0));
        var offset = circ * (1 - clamped / 100);
        var numFont = s >= 120 ? 38 : s >= 80 ? 28 : 22;
        var labelEl = labelText
            ? '<text x="' + (s/2) + '" y="' + (s/2 + 18) + '" text-anchor="middle" dominant-baseline="central" class="yr-score-gauge__label" font-size="11">' + escapeHtml(labelText) + '</text>'
            : '';
        return '<svg class="yr-score-gauge__ring" width="' + s + '" height="' + s + '" viewBox="0 0 ' + s + ' ' + s + '">'
            + '<circle cx="' + (s/2) + '" cy="' + (s/2) + '" r="' + r + '" class="yr-score-gauge__track" stroke-width="10"/>'
            + '<circle cx="' + (s/2) + '" cy="' + (s/2) + '" r="' + r + '" class="yr-score-gauge__progress" stroke-width="10"'
            + ' stroke-dasharray="' + circ.toFixed(2) + '" stroke-dashoffset="' + offset.toFixed(2) + '"'
            + ' transform="rotate(-90 ' + (s/2) + ' ' + (s/2) + ')"/>'
            + '<text x="' + (s/2) + '" y="' + (s/2 - (labelText ? 4 : 0)) + '" text-anchor="middle" dominant-baseline="central" class="yr-score-gauge__value" font-size="' + numFont + '">' + Math.round(clamped) + '</text>'
            + labelEl
            + '</svg>';
    }

    function fitScoreColorClass(fit) {
        if (fit == null) return '';
        if (fit >= 85) return 'yr-fit--high';
        if (fit >= 70) return 'yr-fit--mid';
        return 'yr-fit--low';
    }
    function fitScoreBgClass(fit) {
        if (fit == null) return '';
        if (fit >= 85) return 'yr-fit-bg--high';
        if (fit >= 70) return 'yr-fit-bg--mid';
        return 'yr-fit-bg--low';
    }

    function renderHighlightCards(cars) {
        if (!highlightCardsEl) return;

        if (!cars || !cars.length) {
            highlightCardsEl.innerHTML = '';
            return;
        }

        const byFit = [...cars].filter(c => c.fit_score != null).sort((a, b) => (b.fit_score || 0) - (a.fit_score || 0));
        const byAnnualCost = [...cars].filter(c => c.total_annual_cost != null).sort((a, b) => (a.total_annual_cost || Infinity) - (b.total_annual_cost || Infinity));
        const byReliability = [...cars].filter(c => getReliabilityScore(c) != null).sort((a, b) => (getReliabilityScore(b) || 0) - (getReliabilityScore(a) || 0));

        const bestFit = byFit[0] || null;
        const cheapest = byAnnualCost[0] || null;
        const mostReliable = byReliability[0] || null;

        const cards = [];

        if (bestFit) {
            cards.push({
                label: 'התאמת העדפות גבוהה',
                badge: 'התאמת העדפות',
                car: bestFit,
                chip: bestFit.fit_score != null ? `${Math.round(bestFit.fit_score)}% התאמה` : '',
                text: 'זה הדגם שתואם היטב למה שביקשת בשאלון. המדד משקף התאמת העדפות בלבד, ולא קובע אמינות בפועל או כדאיות קנייה.'
            });
        }

        if (cheapest) {
            cards.push({
                label: 'הכי זול להחזקה שנתי',
                badge: 'עלות שנתית',
                car: cheapest,
                chip: cheapest.total_annual_cost != null ? `${safeNum(cheapest.total_annual_cost)} ₪ בשנה` : '',
                text: 'מתוך כל הדגמים שהוצגו – זה הדגם עם העלות השנתית המוערכת הנמוכה ביותר (דלק/חשמל + תחזוקה בסיסית).'
            });
        }

        if (mostReliable && mostReliable !== bestFit) {
            const relScore = getReliabilityScore(mostReliable);
            const relGrade = getReliabilityGrade(relScore);
            cards.push({
                label: 'סיכון תחזוקה נמוך יותר',
                badge: 'סיכון אמינות',
                car: mostReliable,
                chip: relScore != null ? `סיכון אמינות: ${relGrade.label}` : '',
                grade: relGrade,
                text: 'האינדיקציה מבוססת על מידע כללי ודגמי לגבי תחזוקה ותקלות, ולא קובעת את מצב הרכב הספציפי.'
            });
        }

        if (!cards.length) {
            highlightCardsEl.innerHTML = '';
            return;
        }

        // Safe innerHTML: card fields are escaped via escapeHtml() before interpolation.
        highlightCardsEl.innerHTML = cards.map((card) => {
            const title = escapeHtml(`${card.car.brand || ''} ${card.car.model || ''}`.trim());
            const year = escapeHtml(card.car.year || '');
            const badge = escapeHtml(card.badge || '');
            const label = escapeHtml(card.label || '');
            const chipText = card.chip ? escapeHtml(card.chip) : '';
            const cardText = escapeHtml(card.text || '');
            const grade = card.grade || null;
            const gradeLabel = grade ? escapeHtml(grade.label) : '';
            const gradeClass = grade ? grade.className : '';
            return `
                <article class="yr-tile" style="display:flex;flex-direction:column;justify-content:space-between">
                    <div class="flex items-center justify-between" style="margin-bottom:10px">
                        <span class="yr-hero__badge" style="font-size:10px;padding:4px 10px">${badge}</span>
                        <span style="font-size:11px;color:var(--yr-muted-2)">${label}</span>
                    </div>
                    <div style="margin-bottom:8px">
                        <div style="font-family:'Rubik',sans-serif;font-weight:700;font-size:15px;color:var(--yr-ink)">
                            ${title} ${year ? '· ' + year : ''}
                        </div>
                        ${chipText ? `<div class="yr-chip" style="margin-top:6px;font-size:11px;padding:4px 10px">${chipText}</div>` : ''}
                        ${grade ? `<div class="${gradeClass}" style="margin-top:6px">סיכון אמינות: ${gradeLabel}</div>` : ''}
                    </div>
                    <p style="font-size:11px;line-height:1.6;color:var(--yr-muted);margin:0">${cardText}</p>
                </article>
            `;
        }).join('');
    }

    // --- רנדר כרטיסיית רכב אחת כולל טבלה עם כל הפרמטרים ---
    function renderCarCard(car, index) {
        const title = `${car.brand || ''} ${car.model || ''}`.trim();
        const year = car.year || '';
        const fuel = car.fuel || '';
        const gear = car.gear || '';
        const turbo = car.turbo != null ? String(car.turbo) : '';

        const engineCc = car.engine_cc != null ? `${safeNum(car.engine_cc)} סמ״ק` : '';
        const priceRange = formatPriceRange(car.price_range_nis);

        const isEv = isEVFuel(fuel);
        const avgFuel = car.avg_fuel_consumption != null
            ? (isEv
                ? `${safeNum(car.avg_fuel_consumption, 1)} קוט״ש ל-100 ק״מ`
                : `${safeNum(car.avg_fuel_consumption, 1)} ק״מ לליטר`)
            : '';

        const annualFee = car.annual_fee != null ? `${safeNum(car.annual_fee)} ₪` : '';
        let reliabilityValue = null;
        if (car.reliability_score != null) {
            const parsedReliability = Number(car.reliability_score);
            reliabilityValue = Number.isNaN(parsedReliability) ? null : parsedReliability;
        }
        const reliabilityGrade = getReliabilityGrade(reliabilityValue);
        const maintenanceCost = car.maintenance_cost != null ? `${safeNum(car.maintenance_cost)} ₪` : '';
        const safetyRating = car.safety_rating != null ? safeNum(car.safety_rating, 1) : '';
        const insuranceCost = car.insurance_cost != null ? `${safeNum(car.insurance_cost)} ₪` : '';
        const resaleValue = car.resale_value != null ? safeNum(car.resale_value, 1) : '';
        const performanceScore = car.performance_score != null ? safeNum(car.performance_score, 1) : '';
        const comfortFeatures = car.comfort_features != null ? safeNum(car.comfort_features, 1) : '';
        const suitability = car.suitability != null ? safeNum(car.suitability, 1) : '';
        const marketSupply = car.market_supply || '';

        const fit = car.fit_score != null ? Math.round(car.fit_score) : null;

        const comparisonComment = car.comparison_comment || advisorCopy.fitFallback;
        const notRecommendedReason = car.not_recommended_reason || advisorCopy.caveatFallback;

        // שדות method – טקסט כבר בעברית, רק שם שדה בעברית לפי המפה
        const fuelMethod = car.fuel_method || '';
        const feeMethod = car.fee_method || '';
        const reliabilityMethod = car.reliability_method || '';
        const maintenanceMethod = car.maintenance_method || '';
        const safetyMethod = car.safety_method || '';
        const insuranceMethod = car.insurance_method || '';
        const resaleMethod = car.resale_method || '';
        const performanceMethod = car.performance_method || '';
        const comfortMethod = car.comfort_method || '';
        const suitabilityMethod = car.suitability_method || '';
        const supplyMethod = car.supply_method || '';

        const h = (v, fallback = '') => escapeHtml(v != null && v !== '' ? v : fallback);
        const safeTitle = h(title || 'דגם לא ידוע');
        const safeYear = h(year);
        const safeFuel = h(fuel || 'לא צוין');
        const safeGear = h(gear || 'לא צוין');
        const safeTurbo = turbo ? ` · טורבו: ${h(turbo)}` : '';
        const safeEngineCc = h(engineCc || '-');
        const safePriceRange = h(priceRange || '-');
        const safeAvgFuel = h(avgFuel || '-');
        const safeAnnualFee = h(annualFee || '-');
        const safeReliabilityGrade = h(reliabilityGrade.label || 'לא ידוע');
        const safeMaintenanceCost = h(maintenanceCost || '-');
        const safeSafetyRating = h(safetyRating || '-');
        const safeInsuranceCost = h(insuranceCost || '-');
        const safeResaleValue = h(resaleValue || '-');
        const safePerformanceScore = h(performanceScore || '-');
        const safeComfortFeatures = h(comfortFeatures || '-');
        const safeSuitability = h(suitability || '-');
        const safeMarketSupply = h(marketSupply);
        const safeComparisonComment = h(comparisonComment);
        const safeNotRecommendedReason = h(notRecommendedReason);
        const safeFuelMethod = h(fuelMethod);
        const safeFeeMethod = h(feeMethod);
        const safeReliabilityMethod = h(reliabilityMethod);
        const safeMaintenanceMethod = h(maintenanceMethod);
        const safeSafetyMethod = h(safetyMethod);
        const safeInsuranceMethod = h(insuranceMethod);
        const safeResaleMethod = h(resaleMethod);
        const safePerformanceMethod = h(performanceMethod);
        const safeComfortMethod = h(comfortMethod);
        const safeSuitabilityMethod = h(suitabilityMethod);
        const safeSupplyMethod = h(supplyMethod);

        const safeAnnualEnergyCost = h(car.annual_energy_cost != null ? `${safeNum(car.annual_energy_cost)} ₪` : '', 'לא זמין');
        const safeAnnualFuelCost = h(car.annual_fuel_cost != null ? `${safeNum(car.annual_fuel_cost)} ₪` : '', 'לא זמין');
        const safeTotalAnnualCost = h(car.total_annual_cost != null ? `${safeNum(car.total_annual_cost)} ₪` : '', 'לא זמין');

        const gaugeSvg = fit !== null ? buildScoreGaugeSvg(fit, 80, 'התאמת AI') : '';
        const fitColorClass = fitScoreColorClass(fit);

        function dataRow(label, value) {
            return `<div class="yr-data-row"><div class="yr-data-row__label">${escapeHtml(label)}</div><div class="yr-data-row__value">${value}</div></div>`;
        }
        function methodRow(methodKey, value) {
            if (!value) return '';
            return `<div class="yr-method-row"><div class="yr-method-row__label">${escapeHtml(methodLabelMap[methodKey] || methodKey)}</div><div class="yr-method-row__value">${value}</div></div>`;
        }

        return `
            <article class="yr-car-card yr-rise" style="animation-delay:${index * 80}ms">
                <div class="yr-car-card__header">
                    <div style="flex:1;min-width:0">
                        <div style="font-family:'Rubik',sans-serif;font-weight:700;font-size:17px;color:var(--yr-ink);line-height:1.3">
                            ${safeTitle} ${safeYear ? '<span style="color:var(--yr-muted-2)">· ' + safeYear + '</span>' : ''}
                        </div>
                        <div class="yr-spec-chips">
                            <span class="yr-spec-chip">${safeFuel}</span>
                            <span class="yr-spec-chip">${safeGear}</span>
                            ${turbo ? '<span class="yr-spec-chip">טורבו: ' + h(turbo) + '</span>' : ''}
                            ${engineCc ? '<span class="yr-spec-chip">' + safeEngineCc + '</span>' : ''}
                        </div>
                    </div>
                    <div style="display:flex;flex-direction:column;align-items:center;gap:4px;flex-shrink:0">
                        ${gaugeSvg ? '<div class="yr-score-gauge">' + gaugeSvg + '</div>' : '<div style="font-size:13px;color:var(--yr-muted)">?</div>'}
                        <span style="font-size:10px;color:var(--yr-muted-2)">התאמת העדפות בלבד</span>
                    </div>
                </div>

                <div class="yr-car-card__body">
                    <!-- Key metrics strip -->
                    <div class="yr-metric-strip">
                        <div class="yr-metric-item">
                            <div class="yr-metric-item__label">טווח מחיר</div>
                            <div class="yr-metric-item__value" style="font-size:.95rem">${safePriceRange}</div>
                        </div>
                        <div class="yr-metric-item">
                            <div class="yr-metric-item__label">צריכת דלק/חשמל</div>
                            <div class="yr-metric-item__value" style="font-size:.95rem">${safeAvgFuel}</div>
                        </div>
                        <div class="yr-metric-item">
                            <div class="yr-metric-item__label">סיכון תחזוקה</div>
                            <div style="margin-top:4px"><span class="${reliabilityGrade.className}">${escapeHtml(reliabilityGrade.label)}</span></div>
                        </div>
                        <div class="yr-metric-item">
                            <div class="yr-metric-item__label">עלות שנתית כוללת</div>
                            <div class="yr-metric-item__value" style="font-size:.95rem">${safeTotalAnnualCost}</div>
                        </div>
                        ${marketSupply ? `
                        <div class="yr-metric-item">
                            <div class="yr-metric-item__label">היצע בשוק</div>
                            <div class="yr-metric-item__value" style="font-size:.95rem">${safeMarketSupply}</div>
                        </div>` : ''}
                    </div>

                    <!-- Full data grid -->
                    <div class="yr-data-grid">
                        ${dataRow('מותג / דגם', safeTitle || 'לא זמין')}
                        ${dataRow('שנה', safeYear || 'לא זמין')}
                        ${dataRow('נפח מנוע', safeEngineCc || 'לא זמין')}
                        ${dataRow('טווח מחיר משוער (₪)', safePriceRange || 'לא זמין')}
                        ${dataRow('צריכת דלק/חשמל ממוצעת', safeAvgFuel || 'לא זמין')}
                        ${methodRow('fuel_method', safeFuelMethod)}
                        ${dataRow('אגרת רישוי שנתית (₪)', safeAnnualFee || 'לא זמין')}
                        ${methodRow('fee_method', safeFeeMethod)}
                        ${dataRow('אינדיקציית תחזוקה כללית', safeReliabilityGrade || 'לא זמין')}
                        ${methodRow('reliability_method', safeReliabilityMethod)}
                        ${dataRow('עלות אחזקה שנתית (₪)', safeMaintenanceCost || 'לא זמין')}
                        ${methodRow('maintenance_method', safeMaintenanceMethod)}
                        ${dataRow('בטיחות: מקור רשמי / לא נמצא מקור רשמי', safeSafetyRating || 'לא זמין')}
                        ${methodRow('safety_method', safeSafetyMethod)}
                        ${dataRow('עלות ביטוח שנתית (₪)', safeInsuranceCost || 'לא זמין')}
                        ${methodRow('insurance_method', safeInsuranceMethod)}
                        ${dataRow('שמירת ערך – אינדיקציה כללית', safeResaleValue || 'לא זמין')}
                        ${methodRow('resale_method', safeResaleMethod)}
                        ${dataRow('ביצועים: חלש/סביר/חזק ביחס לקטגוריה', safePerformanceScore || 'לא זמין')}
                        ${methodRow('performance_method', safePerformanceMethod)}
                        ${dataRow('נוחות ואבזור (1–10)', safeComfortFeatures || 'לא זמין')}
                        ${methodRow('comfort_method', safeComfortMethod)}
                        ${dataRow('התאמת העדפות לנהג', safeSuitability || 'לא זמין')}
                        ${methodRow('suitability_method', safeSuitabilityMethod)}
                        ${dataRow('היצע בשוק', safeMarketSupply || 'לא זמין')}
                        ${methodRow('supply_method', safeSupplyMethod)}
                        ${dataRow('עלות אנרגיה שנתית (₪)', safeAnnualEnergyCost)}
                        ${dataRow('עלות דלק שנתית (₪)', safeAnnualFuelCost)}
                        ${dataRow('עלות שנתית כוללת (₪)', safeTotalAnnualCost)}
                    </div>

                    <!-- Comparison comment -->
                    <div class="yr-comment-block">
                        <div class="yr-comment-block__title">למה זה מתאים למה שביקשת:</div>
                        ${safeComparisonComment}
                    </div>

                    <!-- Risk notes -->
                    <div class="yr-risk-note yr-risk-note--warn" style="margin-top:14px">
                        <div>
                            <div style="font-weight:700;margin-bottom:3px;font-size:13px">סיכונים / הסתייגויות שכדאי לבדוק:</div>
                            ${safeNotRecommendedReason}
                        </div>
                    </div>
                </div>
            </article>
        `;
    }

    // --- תצוגת תוצאות מלאה (כרטיסיות + טבלאות) ---
    function renderResults(data, options = {}) {
        if (!resultsSection || !tableWrapper) return;

        const queries = Array.isArray(data.search_queries) ? data.search_queries : [];
        if (queriesEl) {
            if (queries.length) {
                var qId = 'yr-queries-' + Date.now();
                // Safe innerHTML: search queries come sanitized from backend and escaped here.
                queriesEl.innerHTML = `
                    <div>
                        <button type="button" class="yr-queries-toggle" aria-expanded="false" aria-controls="${qId}"
                                onclick="var p=document.getElementById('${qId}');var ex=this.getAttribute('aria-expanded')==='true';this.setAttribute('aria-expanded',String(!ex));p.style.display=ex?'none':'block';">
                            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 5l3 3 3-3"/></svg>
                            שאילתות חיפוש שבוצעו (${queries.length})
                        </button>
                        <ul id="${qId}" class="yr-queries-list" style="display:none">
                            ${queries.map(q => `<li>${escapeHtml(q)}</li>`).join('')}
                        </ul>
                    </div>
                `;
            } else {
                queriesEl.textContent = '';
            }
        }

        const cars = Array.isArray(data.recommended_cars) ? data.recommended_cars : [];
        if (!cars.length) {
            if (profileSummaryEl) profileSummaryEl.innerHTML = '';
            if (highlightCardsEl) highlightCardsEl.innerHTML = '';
            tableWrapper.innerHTML =
                '<p class="text-sm text-slate-400">לא התקבלו המלצות. ייתכן שהגבלות התקציב/שנים קשיחות מדי.</p>';
            isLoading = false;
            isResultReady = true;
            isResultOpen = false;
            closeAdvisorResult();
            showAdvisorReadyPanel();
            showAdvisorResearchCard();
            if (options.openImmediately) {
                resultReadyPanel?.classList.add('hidden');
                openAdvisorResult({ userInitiated: false });
            }
            trackAnalytics('result_rendered', {
                flow_type: 'advisor',
                advisor_history_id: currentHistoryId,
                recommended_count: 0
            });
            return;
        }

        // סיכום פרופיל לפי מה שהוזן בטופס
        renderProfileSummary();

        // כרטיסי Highlight לפי התוצאות
        renderHighlightCards(cars);

        // מיון לפי התאמת העדפות, גדול לקטן
        cars.sort((a, b) => (b.fit_score || 0) - (a.fit_score || 0));

        // Build hero card for best match
        const bestCar = cars[0];
        const bestFit = bestCar.fit_score != null ? Math.round(bestCar.fit_score) : null;
        const bestTitle = escapeHtml(`${bestCar.brand || ''} ${bestCar.model || ''}`.trim() || 'דגם לא ידוע');
        const bestYear = escapeHtml(bestCar.year || '');
        const bestComment = escapeHtml(bestCar.comparison_comment || advisorCopy.fitFallback);
        const bestPrice = escapeHtml(formatPriceRange(bestCar.price_range_nis) || 'לא זמין');
        const bestFuelVal = bestCar.avg_fuel_consumption != null
            ? escapeHtml(isEVFuel(bestCar.fuel)
                ? `${safeNum(bestCar.avg_fuel_consumption, 1)} קוט״ש ל-100 ק״מ`
                : `${safeNum(bestCar.avg_fuel_consumption, 1)} ק״מ לליטר`)
            : 'לא זמין';
        const bestRelScore = getReliabilityScore(bestCar);
        const bestRelGrade = getReliabilityGrade(bestRelScore);
        const bestTotalCost = bestCar.total_annual_cost != null ? escapeHtml(`${safeNum(bestCar.total_annual_cost)} ₪`) : 'לא זמין';
        const bestSafety = bestCar.safety_rating != null ? escapeHtml(safeNum(bestCar.safety_rating, 1)) : 'לא זמין';
        const heroGauge = bestFit !== null ? buildScoreGaugeSvg(bestFit, 130, 'התאמת AI') : '';

        const heroHtml = `
            <div class="yr-result-hero yr-rise" style="margin-bottom:24px">
                <div class="yr-hero__badge" style="margin-bottom:16px">ההתאמה הטובה ביותר</div>
                <div style="display:flex;flex-wrap:wrap;align-items:center;gap:24px">
                    ${heroGauge ? '<div class="yr-score-gauge" style="flex-shrink:0">' + heroGauge + '</div>' : ''}
                    <div style="flex:1;min-width:200px">
                        <div style="font-family:'Rubik',sans-serif;font-weight:800;font-size:1.5rem;color:var(--yr-ink);letter-spacing:-.5px;line-height:1.2">
                            ${bestTitle}
                        </div>
                        <div style="font-size:14px;color:var(--yr-muted);margin-top:4px">${bestYear ? bestYear + ' · ' : ''}${escapeHtml(bestCar.fuel || '')} · ${escapeHtml(bestCar.gear || '')}</div>
                        <p style="margin-top:12px;font-size:13px;line-height:1.6;color:var(--yr-ink-2)">${bestComment}</p>
                    </div>
                </div>
                <div class="yr-metric-strip" style="margin-top:20px">
                    <div class="yr-metric-item">
                        <div class="yr-metric-item__label">טווח מחיר</div>
                        <div class="yr-metric-item__value" style="font-size:1rem">${bestPrice}</div>
                    </div>
                    <div class="yr-metric-item">
                        <div class="yr-metric-item__label">צריכת דלק/חשמל</div>
                        <div class="yr-metric-item__value" style="font-size:1rem">${bestFuelVal}</div>
                    </div>
                    <div class="yr-metric-item">
                        <div class="yr-metric-item__label">סיכון תחזוקה</div>
                        <div style="margin-top:4px"><span class="${bestRelGrade.className}">${escapeHtml(bestRelGrade.label)}</span></div>
                    </div>
                    <div class="yr-metric-item">
                        <div class="yr-metric-item__label">בטיחות</div>
                        <div class="yr-metric-item__value" style="font-size:1rem">${bestSafety}</div>
                    </div>
                    <div class="yr-metric-item">
                        <div class="yr-metric-item__label">עלות שנתית</div>
                        <div class="yr-metric-item__value" style="font-size:1rem">${bestTotalCost}</div>
                    </div>
                </div>
            </div>
        `;

        const cardsHtml = cars.map((car, idx) => renderCarCard(car, idx)).join('');

        // Safe innerHTML: renderCarCard escapes all dynamic values.
        tableWrapper.innerHTML = `
            ${heroHtml}
            <div style="font-size:12px;color:var(--yr-muted);margin-bottom:12px;line-height:1.5">
                לכל רכב מוצגת כרטיסייה נפרדת עם התאמת העדפות לצד סיכונים והסתייגויות נפרדים. התאמת העדפות אינה מדד לאמינות ואינה אישור קנייה.
            </div>
            <div style="display:flex;flex-direction:column;gap:20px">
                ${cardsHtml}
            </div>
        `;

        isLoading = false;
        isResultReady = true;
        isResultOpen = false;
        closeAdvisorResult();
        showAdvisorReadyPanel();
        showAdvisorResearchCard();
        if (options.openImmediately) {
            resultReadyPanel?.classList.add('hidden');
            openAdvisorResult({ userInitiated: false });
        }
        trackAnalytics('result_rendered', {
            flow_type: 'advisor',
            advisor_history_id: currentHistoryId,
            recommended_count: cars.length
        });
    }

    // --- שלב 2: סיכום העדפות ---
    function renderPreferenceSummary() {
        if (!advisorPreferenceCards) return;
        const num = (v) => (v ? parseInt(v, 10).toLocaleString('he-IL') : '');
        const budgetMin = form.budget_min.value ? num(form.budget_min.value) + ' ₪' : 'לא צוין';
        const budgetMax = form.budget_max.value ? num(form.budget_max.value) + ' ₪' : 'לא צוין';
        const yearMin = form.year_min.value || 'לא צוין';
        const yearMax = form.year_max.value || 'לא צוין';
        const bodyStyle = form.body_style.value || 'כללי';
        const mainUse = form.main_use.value || 'לא צוין';
        const fuels = getCheckedValues('fuels_he');
        const gears = getCheckedValues('gears_he');
        const seats = form.seats_choice.value || 'לא צוין';
        const familySize = form.family_size.value || 'לא צוין';
        const drivingStyle = form.driving_style.value || 'לא צוין';
        const w = (id, d) => (document.getElementById(id) ? document.getElementById(id).value : d) || d;
        const weights = [
            { label: 'אמינות', val: w('w_reliability', '5') },
            { label: 'דלק', val: w('w_fuel', '4') },
            { label: 'שמירת ערך', val: w('w_resale', '3') },
            { label: 'ביצועים', val: w('w_performance', '2') },
            { label: 'נוחות', val: w('w_comfort', '3') }
        ];
        const safe = (v) => escapeHtml(v);
        // Safe innerHTML: every interpolated value is escaped via escapeHtml().
        const card = (icon, label, value) => `
            <article class="yr-pref-card">
                <div class="yr-pref-card__icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="${icon}"></path></svg></div>
                <div class="yr-pref-card__body">
                    <div class="yr-pref-card__label">${safe(label)}</div>
                    <div class="yr-pref-card__value">${safe(value)}</div>
                </div>
            </article>`;
        const weightsCard = `
            <article class="yr-pref-card yr-pref-card--weights">
                <div class="yr-pref-card__label" style="margin-bottom:8px">משקלי העדפות</div>
                <div class="yr-pref-weights">
                    ${weights.map((wt) => {
                        const pct = Math.max(0, Math.min(100, (parseInt(wt.val, 10) || 0) / 5 * 100));
                        return `
                        <div class="yr-pref-weight">
                            <div class="yr-pref-weight__head"><span>${safe(wt.label)}</span><span>${safe(wt.val)}/5</span></div>
                            <div class="yr-pref-weight__bar"><div class="yr-pref-weight__fill" style="width:${pct}%"></div></div>
                        </div>`;
                    }).join('')}
                </div>
            </article>`;
        advisorPreferenceCards.innerHTML = [
            card('M3 8.5A2.5 2.5 0 0 1 5.5 6H18a1 1 0 0 1 1 1H6 M3 8.5V18a2 2 0 0 0 2 2h13a1 1 0 0 0 1-1v-3 M20 11h-3a2 2 0 0 0 0 4h3z', 'תקציב', `${budgetMin} – ${budgetMax}`),
            card('M8 2v4 M16 2v4 M3 9h18 M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z', 'שנתון', `${yearMin}–${yearMax}`),
            card('M5 11l1.5-4.2A2 2 0 0 1 8.4 5.4h7.2a2 2 0 0 1 1.9 1.4L19 11 M5 11h14v5H5z M5 16v2 M19 16v2', 'סוג רכב', bodyStyle),
            card('M4 21V8l6-3v16 M10 21V3l8 3v15 M3 21h18', 'שימוש עיקרי', mainUse),
            card('M13 3 4 14h6l-1 7 9-11h-6z', 'דלק / גיר', `${fuels.length ? fuels.join(', ') : 'לא צוין'} · ${gears.length ? gears.join(', ') : 'לא צוין'}`),
            card('M16 19a4 4 0 0 0-8 0 M12 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6 M20.5 19a3 3 0 0 0-3.5-3', 'מושבים / משפחה', `${seats} מושבים · ${familySize}`),
            card('M5 19c0-7 5-12 14-13 1 9-4 15-13 14 M5 19c2-4 5-6 8-7', 'סגנון נהיגה', drivingStyle),
            weightsCard
        ].join('');
    }

    function showPreferenceSummary() {
        renderPreferenceSummary();
        if (advisorSummaryError) {
            advisorSummaryError.textContent = '';
            advisorSummaryError.classList.add('hidden');
        }
        setAdvisorStep('summary');
        window.scrollTo({ top: 0, behavior: 'smooth' });
        trackAnalytics('advisor_summary_viewed', { flow_type: 'advisor' });
    }

    // --- שלב 3: מסך ניתוח AI ---
    const ADVISOR_ANALYZE_R = 80;
    const ADVISOR_ANALYZE_CIRC = 2 * Math.PI * ADVISOR_ANALYZE_R;

    function setAnalyzeProgress(p) {
        const ring = document.getElementById('advisorAnalyzeRing');
        const pctEl = document.getElementById('advisorAnalyzePct');
        const clamped = Math.max(0, Math.min(100, p));
        if (ring) {
            ring.style.strokeDasharray = ADVISOR_ANALYZE_CIRC.toFixed(2);
            ring.style.strokeDashoffset = (ADVISOR_ANALYZE_CIRC * (1 - clamped / 100)).toFixed(2);
        }
        if (pctEl) pctEl.textContent = String(Math.round(clamped));
        const steps = advisorAnalyzingScreen ? advisorAnalyzingScreen.querySelectorAll('.yr-analysis-step') : [];
        const activeCount = Math.min(steps.length, Math.ceil(clamped / 100 * steps.length));
        steps.forEach((el, i) => {
            el.classList.toggle('is-active', i < activeCount);
            el.classList.toggle('is-done', (clamped >= 100 && i < activeCount) || i < activeCount - 1);
        });
    }

    function stopAnalyzeTimer() {
        if (analyzeTimer) { clearInterval(analyzeTimer); analyzeTimer = null; }
    }

    function showAnalyzingScreen(estimatedMs) {
        setAdvisorStep('analyzing');
        window.scrollTo({ top: 0, behavior: 'smooth' });
        stopAnalyzeTimer();
        setAnalyzeProgress(0);
        // ease toward a soft cap; the response completion fills the rest
        const dur = Math.max(2600, Math.min(estimatedMs || 12000, 30000));
        const t0 = performance.now();
        analyzeTimer = setInterval(() => {
            const k = Math.min(1, (performance.now() - t0) / dur);
            const eased = 1 - Math.pow(1 - k, 2);
            setAnalyzeProgress(Math.min(92, eased * 100));
        }, 60);
    }

    function finishAnalyzingScreen() {
        stopAnalyzeTimer();
        setAnalyzeProgress(100);
        return new Promise((resolve) => setTimeout(resolve, 480));
    }

    function setAnalyzeButtonLoading(loading) {
        if (!advisorAnalyzeBtn) return;
        advisorAnalyzeBtn.disabled = loading;
        const spinner = advisorAnalyzeBtn.querySelector('.spinner');
        const text = advisorAnalyzeBtn.querySelector('.button-text');
        if (spinner) spinner.classList.toggle('hidden', !loading);
        if (text) text.classList.toggle('opacity-60', loading);
    }

    function showAnalyzeError(message, requestId) {
        stopAnalyzeTimer();
        setAdvisorStep('summary');
        if (advisorSummaryError) {
            const suffix = requestId ? ` (ID: ${requestId})` : '';
            advisorSummaryError.textContent = (message || 'שגיאה כללית בחיבור לשרת.') + suffix;
            advisorSummaryError.classList.remove('hidden');
        } else {
            showRequestAwareError(message, requestId);
        }
    }

    function showResultsScreen() {
        setAdvisorStep('results');
        openAdvisorResult({ userInitiated: true });
    }

    function resetAdvisorFlow() {
        stopAnalyzeTimer();
        pendingAdvisorPayload = null;
        resetAdvisorResultFlowState();
        if (advisorSummaryError) {
            advisorSummaryError.textContent = '';
            advisorSummaryError.classList.add('hidden');
        }
        if (errorEl) {
            errorEl.textContent = '';
            errorEl.classList.add('hidden');
        }
        setAdvisorStep('form');
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    // --- שלב 3→4: קריאה לשרת + רינדור תוצאות (אחרי לחיצה על "נתח") ---
    async function runAnalysis() {
        if (!pendingAdvisorPayload) {
            setAdvisorStep('form');
            return;
        }
        if (advisorSummaryError) {
            advisorSummaryError.textContent = '';
            advisorSummaryError.classList.add('hidden');
        }
        isLoading = true;
        setAnalyzeButtonLoading(true);

        // Integrate the existing timing estimate visually into the analyzing screen
        let etaMs = 12000;
        try {
            const eta = await safeFetchJson('/api/timing/estimate?kind=advisor', { method: 'GET', credentials: 'include' });
            if (eta && eta.ok && eta.data && eta.data.p75_ms) {
                etaMs = eta.data.p75_ms;
                const secs = Math.ceil(etaMs / 1000);
                const count = eta.data.sample_size || 0;
                const etaEl = document.getElementById('advisorAnalyzeEta');
                if (etaEl) {
                    etaEl.textContent = count > 0
                        ? `זמן משוער: ~${secs} שניות · מבוסס על ${count} ניתוחים`
                        : `זמן משוער: ~${secs} שניות`;
                }
            }
        } catch (e) { /* fall back to default ETA */ }

        showAnalyzingScreen(etaMs);
        trackAnalytics('advisor_analysis_started', { flow_type: 'advisor' });
        trackAnalytics('result_requested', {
            flow_type: 'advisor',
            budget_min: pendingAdvisorPayload.budget_min,
            budget_max: pendingAdvisorPayload.budget_max,
            preferred_fuels_count: Array.isArray(pendingAdvisorPayload.fuels_he) ? pendingAdvisorPayload.fuels_he.length : 0
        });

        try {
            const res = await safeFetchJson('/advisor_api', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',  // Send session cookies
                body: JSON.stringify(pendingAdvisorPayload)
            });

            if (!res || res.ok === false) {
                const code = res && res.error && res.error.code;
                if (code === 'unauthenticated') {
                    showAnalyzeError('אנא התחבר כדי להשתמש במנוע ההמלצות.', res && res.request_id);
                    setTimeout(() => { window.location.href = '/login'; }, 1200);
                    return;
                }
                const field = res && res.error && res.error.details && res.error.details.field;
                const baseMsg = (res && res.error && res.error.message) || 'שגיאת שרת בעת הפעלת מנוע ההמלצות.';
                const msg = field ? `${baseMsg} (שדה: ${field})` : baseMsg;
                showAnalyzeError(msg, res && res.request_id);
                return;
            }

            const payloadFromApi = res.data || {};
            currentHistoryId = payloadFromApi.history_id || null;
            await finishAnalyzingScreen();
            setAdvisorStep('results');
            renderResults(payloadFromApi, { openImmediately: true });
        } catch (err) {
            console.error(err);
            showAnalyzeError(err.message || 'שגיאה כללית בחיבור לשרת. נסה שוב מאוחר יותר.', err.requestId || null);
        } finally {
            isLoading = false;
            setAnalyzeButtonLoading(false);
        }
    }

    // --- Submit (שלב 1 → שלב 2): מאמת ובונה payload, ואז מציג סיכום העדפות ---
    async function handleSubmit(e) {
        e.preventDefault();

        if (errorEl) {
            errorEl.textContent = '';
            errorEl.classList.add('hidden');
        }

        // בדיקת הסכמה מעל גיל 18 + תנאים
        if (consentCheckbox && !consentCheckbox.checked) {
            if (errorEl) {
                errorEl.textContent =
                    'יש לאשר שאתה מעל גיל 18 ומסכים לתקנון ולמדיניות הפרטיות לפני הפעלת מנוע ההמלצות.';
                errorEl.classList.remove('hidden');
            }
            return;
        }

        if (!(await ensureLegalAcceptance())) {
            return;
        }

        const payload = { ...buildPayload(), legal_confirm: true };

        if (!payload.budget_max || payload.budget_max <= 0 || payload.budget_min > payload.budget_max) {
            if (errorEl) {
                errorEl.textContent =
                    'בדוק שהתקציב המינימלי קטן מהתקציב המקסימלי ושערכי התקציב תקינים.';
                errorEl.classList.remove('hidden');
            }
            return;
        }

        // Reset any previous result state, store the exact payload, then show the
        // preference summary. The backend is called only on "נתח את ההעדפות שלי".
        resetAdvisorResultFlowState();
        pendingAdvisorPayload = payload;
        showPreferenceSummary();
    }

    form.addEventListener('submit', handleSubmit);
    advisorAnalyzeBtn?.addEventListener('click', runAnalysis);
    advisorBackToEditBtn?.addEventListener('click', function () {
        setAdvisorStep('form');
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    advisorRestartBtn?.addEventListener('click', resetAdvisorFlow);
    syncAdvisorFlowHeaderTop();
    window.addEventListener('resize', syncAdvisorFlowHeaderTop);
    setAdvisorStep('form');

    (researchFormEl?.querySelectorAll('input[name="advisorResearchMajorFaults"]') || []).forEach((radio) => {
        radio.addEventListener('change', syncAdvisorResearchFaultTypeVisibility);
    });
    syncAdvisorResearchFaultTypeVisibility();

    openResultButton?.addEventListener('click', function () {
        openAdvisorResult({ userInitiated: true });
    });

    researchSkipBtn?.addEventListener('click', function () {
        closeAdvisorResearch({ reason: 'skip', trackSkipped: true });
    });

    researchCloseBtn?.addEventListener('click', function () {
        closeAdvisorResearch({ reason: 'close_button' });
    });

    researchDismissBtn?.addEventListener('click', function () {
        closeAdvisorResearch({ reason: 'dismiss_form' });
    });

    researchAnswerNowBtn?.addEventListener('click', function () {
        openAdvisorResearchForm();
    });

    openResultNowBtn?.addEventListener('click', function () {
        closeAdvisorResearch({ reason: 'open_result_now', openResult: true });
    });

    researchFormEl?.addEventListener('submit', async function (event) {
        event.preventDefault();
        if (!currentHistoryId) {
            setResearchMessage('קודם צריך להפיק תוצאה כדי לשמור תרומה אופציונלית למאגר.', 'error');
            return;
        }

        const responses = buildAdvisorResearchResponses();
        if (!responses.length) {
            setResearchMessage('צריך למלא לפחות תשובת מחקר אחת כדי לשמור.', 'warning');
            return;
        }

        if (getSelectedMajorFaultValue() === 'yes' && !researchMajorFaultTypeEl?.value) {
            setResearchMessage('אם היו תקלות משמעותיות, צריך לבחור גם את סוג התקלה.', 'warning');
            return;
        }

        if (!researchClient || !(await researchClient.ensureConsent('advisor_after_result'))) {
            return;
        }

        const servicePayload = buildPayload();
        try {
            await researchClient.saveResponses({
                flow_type: 'advisor',
                source_analysis_type: 'advisor_history',
                source_record_id: currentHistoryId,
                vehicle_context: {
                    advisor_history_id: currentHistoryId,
                    budget_min: servicePayload.budget_min,
                    budget_max: servicePayload.budget_max,
                    preferred_fuels: servicePayload.fuels_he,
                    main_use: servicePayload.main_use,
                    annual_km: servicePayload.annual_km
                },
                responses
            });
            setResearchMessage('תודה — התשובות נשמרו למחקר בלבד.', 'success');
            markResearchPromptSeen('advisor', currentHistoryId);
            trackAnalytics('research_completed', {
                flow_type: 'advisor',
                advisor_history_id: currentHistoryId,
                saved_count: responses.length
            });
        } catch (err) {
            trackAnalytics('research_save_failed', {
                flow_type: 'advisor',
                advisor_history_id: currentHistoryId,
                message: err.message || 'save_failed'
            });
            setResearchMessage('לא הצלחנו לשמור את התשובות כרגע. התוצאה שלך עדיין זמינה.', 'error');
        }
    });

    if (window.advisorHistoryProfile && window.advisorHistoryResult) {
        applyHistoryProfile(window.advisorHistoryProfile);
        currentHistoryId = window.advisorHistoryId || null;
        renderResults(window.advisorHistoryResult, { openImmediately: true });
        setAdvisorStep('results');
    }
})();
