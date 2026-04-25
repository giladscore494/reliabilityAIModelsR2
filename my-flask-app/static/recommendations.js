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
        fitFallback: 'Fit Score גבוה כאן משקף התאמה לתקציב, לשימוש ולהעדפות שסימנת בשאלון.',
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
            alert(`${message}${suffix}`);
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
        reliability_method: "שיטת חישוב אמינות",
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
            <div class="flex flex-wrap gap-2 mb-2">
                <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-800 text-[11px] text-slate-100 border border-slate-700">
                    תקציב: ${safe(budgetMin)} – ${safe(budgetMax)}
                </span>
                <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-800 text-[11px] text-slate-100 border border-slate-700">
                    שנים: ${safe(yearMin)}–${safe(yearMax)}
                </span>
                <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-800 text-[11px] text-slate-100 border border-slate-700">
                    ק״מ שנתי: ${safe(annualKm)}
                </span>
            </div>

            <div class="flex flex-wrap gap-2 mb-2">
                <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-900 text-[11px] text-slate-100 border border-slate-700">
                    גיל נהג: ${safe(driverAge)}
                </span>
                <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-900 text-[11px] text-slate-100 border border-slate-700">
                    ותק רישיון: ${safe(licenseYears)} שנים
                </span>
                <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-900 text-[11px] text-slate-100 border border-slate-700">
                    משפחה: ${safe(familySize)}, ${safe(seats)} מושבים
                </span>
            </div>

            <div class="flex flex-wrap gap-2 mb-2">
                <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-900 text-[11px] text-slate-100 border border-slate-700">
                    שימוש: ${safe(mainUse)}
                </span>
                <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-900 text-[11px] text-slate-100 border border-slate-700">
                    סגנון נהיגה: ${safe(drivingStyle)}
                </span>
                <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-900 text-[11px] text-slate-100 border border-slate-700">
                    מרכב מועדף: ${safe(bodyStyle)}
                </span>
            </div>

            <div class="flex flex-wrap gap-2 mt-1">
                <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-primary/10 text-[11px] text-primary border border-primary/40">
                    משקל אמינות: ${safe(wReliability)}/5
                </span>
                <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-primary/10 text-[11px] text-primary border border-primary/40">
                    חיסכון בדלק: ${safe(wFuel)}/5
                </span>
                <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-primary/10 text-[11px] text-primary border border-primary/40">
                    שמירת ערך: ${safe(wResale)}/5
                </span>
                <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-primary/10 text-[11px] text-primary border border-primary/40">
                    ביצועים: ${safe(wPerf)}/5
                </span>
                <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-primary/10 text-[11px] text-primary border border-primary/40">
                    נוחות: ${safe(wComfort)}/5
                </span>
            </div>

            <div class="mt-2 text-[11px] text-slate-400">
                העדפות דלק: ${fuelsText} · גיר: ${gearsText}
            </div>
        `;
    }

    // --- כרטיסי Highlight (התאמה כללית, הכי זול, הכי אמין אם קיים) ---
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
            return { label: 'לא ידוע', className: 'bg-slate-500/20 text-slate-200 border-slate-500/40' };
        }
        if (score >= 7) {
            return { label: 'גבוה', className: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/40' };
        }
        if (score >= 4) {
            return { label: 'בינוני', className: 'bg-amber-500/20 text-amber-200 border-amber-500/40' };
        }
        return { label: 'נמוך', className: 'bg-red-500/20 text-red-200 border-red-500/40' };
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
                label: 'התאמה כללית הכי גבוהה',
                badge: 'Fit Score מוביל',
                car: bestFit,
                chip: bestFit.fit_score != null ? `${Math.round(bestFit.fit_score)}% Fit` : '',
                text: 'זה הדגם שהכי תואם למה שביקשת בשאלון. הציון משקף התאמת העדפות בלבד, ולא קובע שזה בהכרח הרכב הכי אמין או הכי כדאי לקנייה.'
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
                label: 'הכי חזק באמינות',
                badge: 'אמינות',
                car: mostReliable,
                chip: relScore != null ? `אמינות: ${relGrade.label}` : '',
                grade: relGrade,
                text: 'דגש על מינימום תקלות לאור נתוני אמינות והיסטוריית תקלות ביחס לשאר הדגמים שהוצגו.'
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
                <article class="bg-slate-900/60 border border-slate-800 rounded-xl p-3 md:p-4 flex flex-col justify-between">
                    <div class="flex items-center justify-between mb-2">
                        <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-800 text-[10px] font-semibold text-slate-100 border border-slate-700">
                            ${badge}
                        </span>
                        <span class="text-[11px] text-slate-400">${label}</span>
                    </div>
                    <div class="mb-2">
                        <div class="text-sm md:text-base font-bold text-slate-100">
                            ${title} ${year ? '· ' + year : ''}
                        </div>
                        ${chipText ? `
                            <div class="mt-1 inline-flex items-center px-2 py-0.5 rounded-full bg-primary/15 text-[11px] text-primary border border-primary/40">
                                ${chipText}
                            </div>
                        ` : ''}
                        ${grade ? `
                            <div class="mt-1 inline-flex items-center px-2 py-0.5 rounded-full border text-[10px] font-semibold ${gradeClass}">
                                רמת אמינות: ${gradeLabel}
                            </div>
                        ` : ''}
                    </div>
                    <p class="mt-1 text-[11px] md:text-xs text-slate-300 leading-relaxed">
                        ${cardText}
                    </p>
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
        let fitClass = 'bg-slate-800 text-slate-100';
        if (fit !== null) {
            if (fit >= 85) fitClass = 'bg-emerald-500/90 text-white';
            else if (fit >= 70) fitClass = 'bg-amber-500/90 text-slate-900';
            else fitClass = 'bg-slate-700 text-slate-100';
        }

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

        return `
            <article class="bg-slate-900/70 border border-slate-800 rounded-2xl p-4 md:p-5 space-y-3">
                <div class="flex items-start justify-between gap-3">
                    <div>
                        <div class="text-sm md:text-base font-bold text-slate-100">
                            ${safeTitle} ${safeYear ? '· ' + safeYear : ''}
                        </div>
                        <div class="text-[11px] md:text-xs text-slate-400 mt-0.5">
                            דלק: ${safeFuel} · גיר: ${safeGear}${safeTurbo}
                        </div>
                    </div>
                    <div class="flex flex-col items-end gap-1">
                        <span class="inline-flex items-center justify-center min-w-[52px] px-2 py-1 rounded-full text-[11px] font-bold ${fitClass}">
                            ${fit !== null ? fit + '% Fit' : '?'}
                        </span>
                        <span class="text-[11px] text-slate-400">התאמת העדפות בלבד</span>
                        ${reliabilityValue != null ? `
                            <span class="inline-flex items-center px-2 py-0.5 rounded-full border text-[10px] font-semibold ${reliabilityGrade.className}">
                                רמת אמינות: ${escapeHtml(reliabilityGrade.label)}
                            </span>
                        ` : ''}
                        ${marketSupply ? `
                            <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-800 text-[10px] text-slate-100 border border-slate-700">
                                היצע בשוק: ${safeMarketSupply}
                            </span>
                        ` : ''}
                    </div>
                </div>

                <div class="overflow-x-auto mt-2">
                    <table class="min-w-full text-right text-[11px] md:text-xs border-separate border-spacing-y-1">
                        <tbody>
                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300 w-40">מותג / דגם</th>
                                <td class="px-2 py-1 text-slate-100">${safeTitle || '-'}</td>
                            </tr>
                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">שנה</th>
                                <td class="px-2 py-1 text-slate-100">${safeYear || '-'}</td>
                            </tr>
                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">נפח מנוע</th>
                                <td class="px-2 py-1 text-slate-100">${safeEngineCc}</td>
                            </tr>
                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">טווח מחיר משוער (₪)</th>
                                <td class="px-2 py-1 text-slate-100">${safePriceRange}</td>
                            </tr>

                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">צריכת דלק/חשמל ממוצעת</th>
                                <td class="px-2 py-1 text-slate-100">${safeAvgFuel}</td>
                            </tr>
                            ${fuelMethod ? `
                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.fuel_method}</th>
                                <td class="px-2 py-1 text-slate-200">${safeFuelMethod}</td>
                            </tr>` : ''}

                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">אגרת רישוי שנתית (₪)</th>
                                <td class="px-2 py-1 text-slate-100">${safeAnnualFee}</td>
                            </tr>
                            ${feeMethod ? `
                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.fee_method}</th>
                                <td class="px-2 py-1 text-slate-200">${safeFeeMethod}</td>
                            </tr>` : ''}

                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">רמת אמינות</th>
                                <td class="px-2 py-1 text-slate-100">${safeReliabilityGrade}</td>
                            </tr>
                            ${reliabilityMethod ? `
                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.reliability_method}</th>
                                <td class="px-2 py-1 text-slate-200">${safeReliabilityMethod}</td>
                            </tr>` : ''}

                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">עלות אחזקה שנתית (₪)</th>
                                <td class="px-2 py-1 text-slate-100">${safeMaintenanceCost}</td>
                            </tr>
                            ${maintenanceMethod ? `
                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.maintenance_method}</th>
                                <td class="px-2 py-1 text-slate-200">${safeMaintenanceMethod}</td>
                            </tr>` : ''}

                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">ציון בטיחות (1–10)</th>
                                <td class="px-2 py-1 text-slate-100">${safeSafetyRating}</td>
                            </tr>
                            ${safetyMethod ? `
                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.safety_method}</th>
                                <td class="px-2 py-1 text-slate-200">${safeSafetyMethod}</td>
                            </tr>` : ''}

                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">עלות ביטוח שנתית (₪)</th>
                                <td class="px-2 py-1 text-slate-100">${safeInsuranceCost}</td>
                            </tr>
                            ${insuranceMethod ? `
                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.insurance_method}</th>
                                <td class="px-2 py-1 text-slate-200">${safeInsuranceMethod}</td>
                            </tr>` : ''}

                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">שמירת ערך (1–10)</th>
                                <td class="px-2 py-1 text-slate-100">${safeResaleValue}</td>
                            </tr>
                            ${resaleMethod ? `
                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.resale_method}</th>
                                <td class="px-2 py-1 text-slate-200">${safeResaleMethod}</td>
                            </tr>` : ''}

                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">ביצועים (1–10)</th>
                                <td class="px-2 py-1 text-slate-100">${safePerformanceScore}</td>
                            </tr>
                            ${performanceMethod ? `
                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.performance_method}</th>
                                <td class="px-2 py-1 text-slate-200">${safePerformanceMethod}</td>
                            </tr>` : ''}

                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">נוחות ואבזור (1–10)</th>
                                <td class="px-2 py-1 text-slate-100">${safeComfortFeatures}</td>
                            </tr>
                            ${comfortMethod ? `
                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.comfort_method}</th>
                                <td class="px-2 py-1 text-slate-200">${safeComfortMethod}</td>
                            </tr>` : ''}

                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">התאמה לנהג (1–10)</th>
                                <td class="px-2 py-1 text-slate-100">${safeSuitability}</td>
                            </tr>
                            ${suitabilityMethod ? `
                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.suitability_method}</th>
                                <td class="px-2 py-1 text-slate-200">${safeSuitabilityMethod}</td>
                            </tr>` : ''}

                            ${supplyMethod ? `
                            <tr>
                                <th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.supply_method}</th>
                                <td class="px-2 py-1 text-slate-200">${safeSupplyMethod}</td>
                            </tr>` : ''}
                        </tbody>
                    </table>
                </div>

                <div class="mt-2 rounded-xl border border-primary/25 bg-primary/8 px-3 py-2 text-[11px] md:text-xs text-slate-200 leading-relaxed">
                    <span class="font-semibold text-white">למה זה מתאים למה שביקשת:</span>
                    <br>${safeComparisonComment}
                </div>

                <div class="mt-2 text-[11px] md:text-xs text-amber-200 leading-relaxed border border-amber-500/30 bg-amber-950/20 rounded-xl px-3 py-2">
                    <span class="font-semibold text-amber-100">סיכונים / הסתייגויות שכדאי לבדוק:</span>
                    <br>${safeNotRecommendedReason}
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
                // Safe innerHTML: search queries come sanitized from backend and escaped here.
                queriesEl.innerHTML = `
                    <div class="text-[11px] text-slate-400">
                        <span class="font-semibold text-slate-300">שאילתות חיפוש שבוצעו:</span>
                        <ul class="mt-1 space-y-0.5">
                            ${queries.map(q => `<li>• ${escapeHtml(q)}</li>`).join('')}
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

        // מיון לפי Fit Score, גדול לקטן
        cars.sort((a, b) => (b.fit_score || 0) - (a.fit_score || 0));

        const cardsHtml = cars.map((car, idx) => renderCarCard(car, idx)).join('');

        // Safe innerHTML: renderCarCard escapes all dynamic values.
        tableWrapper.innerHTML = `
            <div class="mb-2 text-[11px] text-slate-400">
                לכל רכב מוצגת כרטיסייה נפרדת עם התאמת העדפות לצד סיכונים והסתייגויות נפרדים. Fit Score אינו ציון אמינות ואינו אישור קנייה.
            </div>
            <div class="space-y-4">
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

    // --- Submit ---
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

        resetAdvisorResultFlowState();
        isLoading = true;
        const payload = { ...buildPayload(), legal_confirm: true };

        if (!payload.budget_max || payload.budget_max <= 0 || payload.budget_min > payload.budget_max) {
            if (errorEl) {
                errorEl.textContent =
                    'בדוק שהתקציב המינימלי קטן מהתקציב המקסימלי ושערכי התקציב תקינים.';
                errorEl.classList.remove('hidden');
            }
            return;
        }

        trackAnalytics('result_requested', {
            flow_type: 'advisor',
            budget_min: payload.budget_min,
            budget_max: payload.budget_max,
            preferred_fuels_count: Array.isArray(payload.fuels_he) ? payload.fuels_he.length : 0
        });
        setSubmitting(true);
        showTimingBanner('advisor');
        
        try {
            const res = await safeFetchJson('/advisor_api', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                credentials: 'include',  // Send session cookies
                body: JSON.stringify(payload)
            });

            if (!res || res.ok === false) {
                const code = res && res.error && res.error.code;
                if (code === 'unauthenticated') {
                    showRequestAwareError('אנא התחבר כדי להשתמש במנוע ההמלצות.', res && res.request_id);
                    setTimeout(() => { window.location.href = '/login'; }, 1200);
                    return;
                }
                const field = res && res.error && res.error.details && res.error.details.field;
                const baseMsg = (res && res.error && res.error.message) || 'שגיאת שרת בעת הפעלת מנוע ההמלצות.';
                const msg = field ? `${baseMsg} (שדה: ${field})` : baseMsg;
                showRequestAwareError(msg, res && res.request_id);
                return;
            }

            const payloadFromApi = res.data || {};
            currentHistoryId = payloadFromApi.history_id || null;
            renderResults(payloadFromApi);
        } catch (err) {
            console.error(err);
            showRequestAwareError(err.message || 'שגיאה כללית בחיבור לשרת. נסה שוב מאוחר יותר.', err.requestId || null);
        } finally {
            if (!isResultReady) {
                hideAdvisorResearchCard();
                resultReadyPanel?.classList.add('hidden');
            }
            isLoading = false;
            hideTimingBanner(true);
            setSubmitting(false);
        }
    }

    form.addEventListener('submit', handleSubmit);

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
    }
})();
