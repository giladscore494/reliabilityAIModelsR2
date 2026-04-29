// /static/script.js
// לוגיקת צד לקוח לטופס בדיקת אמינות + הצגת תוצאות
// XSS Protection: All AI-generated content is HTML-escaped on the backend via sanitization.py
// before being sent to the frontend. Template literals are safe to use with innerHTML.

(function () {
    const carDataScript = document.getElementById('car-data');
    let CAR_DATA = {};
    if (carDataScript) {
        try {
            CAR_DATA = JSON.parse(carDataScript.textContent || carDataScript.innerHTML || '{}');
        } catch (e) {
            console.error('[CAR-DATA] JSON parse error', e);
            CAR_DATA = {};
        }
    }

    const escapeHtml = (value) => {
        if (value === null || value === undefined) return '';
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    };

    const sanitizeUrl = (url) => {
        if (!url) return '';
        const trimmed = url.replace(/^\s+/, '');
        if (/^https?:\/\//i.test(trimmed)) return trimmed;
        if (/^mailto:/i.test(trimmed)) return trimmed;
        return '';
    };

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
                request_id: null,
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
                    details: { status: response.status, body_snippet: snippet },
                },
                request_id: requestId,
            };
        }

        if (parsed) {
            parsed.request_id = parsed.request_id || requestId;
            return parsed;
        }

        return { ok: true, data: textBody, request_id: requestId };
    }

    function showRequestAwareError(message, requestId) {
        const suffix = requestId ? ` (ID: ${requestId})` : '';
        alert(`${message}${suffix}`);
    }

    function normalizeInfoReview(data) {
        const asObject = (value) => (value && typeof value === 'object' ? value : {});
        const report = asObject(data?.reliability_report);
        const checklist = asObject(data?.what_must_be_checked_before_a_decision || report.what_must_be_checked_before_a_decision);
        const missingInfo = Array.isArray(data?.missing_critical_info) ? data.missing_critical_info.filter(Boolean) : [];
        const verificationFocus = Array.isArray(data?.verification_focus) ? data.verification_focus.filter(Boolean) : [];
        const riskAreas = Array.isArray(data?.key_risk_areas_to_examine || report.key_risk_areas_to_examine)
            ? (data?.key_risk_areas_to_examine || report.key_risk_areas_to_examine).filter(Boolean)
            : [];
        const knownUncertainties = Array.isArray(data?.known_uncertainties || report.known_uncertainties)
            ? (data?.known_uncertainties || report.known_uncertainties).filter(Boolean)
            : [];
        const estimatedCostSensitivity = Array.isArray(data?.estimated_cost_sensitivity || report.estimated_cost_sensitivity)
            ? (data?.estimated_cost_sensitivity || report.estimated_cost_sensitivity).filter(Boolean)
            : [];
        const basedOnAvailableInformation =
            data?.based_on_available_information || report.based_on_available_information || '';
        const fallbackChecks = []
            .concat(Array.isArray(checklist.mechanical_inspection_points) ? checklist.mechanical_inspection_points : [])
            .concat(Array.isArray(checklist.documents_to_verify) ? checklist.documents_to_verify : [])
            .concat(Array.isArray(checklist.questions_to_ask_seller) ? checklist.questions_to_ask_seller : [])
            .concat(Array.isArray(checklist.red_flags_to_look_for) ? checklist.red_flags_to_look_for : []);
        const checksToVerify = (missingInfo.length ? missingInfo : verificationFocus.length ? verificationFocus : fallbackChecks)
            .filter(Boolean)
            .slice(0, 5);
        return {
            report,
            checklist,
            dataQualityLabel: data?.data_quality_label || 'חלקית',
            decisionReadiness: data?.decision_readiness || 'נדרש אימות נוסף',
            missingInfo,
            verificationFocus,
            riskAreas,
            knownUncertainties,
            estimatedCostSensitivity,
            basedOnAvailableInformation,
            checksToVerify,
            sources: Array.isArray(data?.sources) ? data.sources.filter(Boolean) : [],
        };
    }

    function normalizeAnalyzeResponse(payload) {
        const result = payload?.result || payload?.report || payload?.analysis || payload?.data || payload;
        return {
            requestId: payload?.request_id || result?.request_id || null,
            result,
        };
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

    function setReliabilityResearchMessage(message, tone) {
        if (!reliabilityResearchMessage) return;
        if (!message) {
            reliabilityResearchMessage.textContent = '';
            reliabilityResearchMessage.classList.add('hidden');
            reliabilityResearchMessage.classList.remove('text-emerald-300', 'text-amber-300', 'text-red-300');
            reliabilityResearchMessage.classList.add('text-emerald-300');
            return;
        }
        reliabilityResearchMessage.textContent = message;
        reliabilityResearchMessage.classList.remove('hidden', 'text-emerald-300', 'text-amber-300', 'text-red-300');
        reliabilityResearchMessage.classList.add(
            tone === 'error' ? 'text-red-300' : tone === 'warning' ? 'text-amber-300' : 'text-emerald-300'
        );
    }

    function scrollToReliabilityResult() {
        if (!resultsContainer) return;
        resultsContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function hideReliabilityResearchCard() {
        reliabilityResearchSection?.classList.add('hidden');
        reliabilityResearchFormWrap?.classList.add('hidden');
        researchCardVisible = false;
        researchFormOpen = false;
    }

    function resetReliabilityResearchCard() {
        hideReliabilityResearchCard();
        reliabilityResearchForm?.reset();
        setReliabilityResearchMessage('', 'success');
    }

    function openReliabilityResult(options = {}) {
        if (!resultsContainer || !isResultReady) return;
        const userInitiated = options.userInitiated !== false;
        const alreadyOpen = isResultOpen;
        isResultOpen = true;
        resultsContainer.classList.remove('hidden');
        if (resultReadyPanel) {
            resultReadyPanel.classList.remove('hidden');
        }
        scrollToReliabilityResult();
        if (userInitiated && !alreadyOpen) {
            trackAnalytics('result_opened', {
                flow_type: 'reliability',
                search_history_id: currentHistoryId,
            });
        }
    }

    function closeReliabilityResult() {
        isResultOpen = false;
        resultsContainer?.classList.add('hidden');
    }

    function showReliabilityReadyPanel() {
        if (!resultReadyPanel) return;
        resultReadyPanel.classList.remove('hidden');
        isResultReady = true;
        if (currentHistoryId && resultReadyPanelTrackedForHistory !== currentHistoryId) {
            trackAnalytics('result_ready_panel_shown', {
                flow_type: 'reliability',
                search_history_id: currentHistoryId,
            });
            resultReadyPanelTrackedForHistory = currentHistoryId;
        }
    }

    function showReliabilityResearchCard() {
        if (!reliabilityResearchSection || !currentHistoryId || hasSeenResearchPrompt('reliability', currentHistoryId)) {
            hideReliabilityResearchCard();
            return;
        }
        reliabilityResearchSection.classList.remove('hidden');
        researchCardVisible = true;
        if (reliabilityResearchTrackedForHistory !== currentHistoryId) {
            trackAnalytics('research_card_shown', {
                flow_type: 'reliability',
                search_history_id: currentHistoryId,
            });
            reliabilityResearchTrackedForHistory = currentHistoryId;
        }
    }

    function closeReliabilityResearch(options = {}) {
        const reason = options.reason || 'closed';
        const trackSkipped = options.trackSkipped === true;
        if (currentHistoryId) {
            markResearchPromptSeen('reliability', currentHistoryId);
            trackAnalytics('research_card_closed', {
                flow_type: 'reliability',
                search_history_id: currentHistoryId,
                reason,
            });
            if (trackSkipped) {
                trackAnalytics('research_skipped', {
                    flow_type: 'reliability',
                    search_history_id: currentHistoryId,
                });
            }
        }
        hideReliabilityResearchCard();
        if (options.openResult === true) {
            openReliabilityResult({ userInitiated: true });
        }
    }

    function openReliabilityResearchForm() {
        if (!currentHistoryId || !reliabilityResearchFormWrap) return;
        reliabilityResearchFormWrap.classList.remove('hidden');
        researchFormOpen = true;
        setReliabilityResearchMessage('', 'success');
        trackAnalytics('research_started', {
            flow_type: 'reliability',
            search_history_id: currentHistoryId,
        });
        const firstInput = reliabilityResearchForm?.querySelector('input, select, textarea');
        firstInput?.focus();
    }

    function resetResultFlowState() {
        isLoading = false;
        isResultReady = false;
        isResultOpen = false;
        currentHistoryId = null;
        researchCardVisible = false;
        researchFormOpen = false;
        lastAnalyzePayload = null;
        resultReadyPanel?.classList.add('hidden');
        closeReliabilityResult();
        resetReliabilityResearchCard();
        clearAnalyzeError();
    }

    function clearAnalyzeError() {
        analyzeErrorPanel?.classList.add('hidden');
        if (analyzeErrorTitle) analyzeErrorTitle.textContent = 'אירעה שגיאה';
        if (analyzeErrorMessage) analyzeErrorMessage.textContent = '';
        if (analyzeErrorMeta) analyzeErrorMeta.innerHTML = '';
        if (analyzeErrorDebug) {
            analyzeErrorDebug.textContent = '';
            analyzeErrorDebug.classList.add('hidden');
        }
    }

    function showAnalyzeError(message, meta = {}) {
        const items = [];
        if (meta.requestId) {
            items.push(['Request ID', meta.requestId]);
        }
        if (meta.status !== undefined && meta.status !== null && meta.status !== '') {
            items.push(['HTTP', String(meta.status)]);
        }
        if (meta.type) {
            items.push(['Type', meta.type]);
        }

        closeReliabilityResult();
        hideReliabilityResearchCard();
        resultReadyPanel?.classList.add('hidden');
        resultsContainer?.classList.add('hidden');

        if (analyzeErrorTitle) {
            analyzeErrorTitle.textContent = meta.title || 'אירעה שגיאה בניתוח';
        }
        if (analyzeErrorMessage) {
            analyzeErrorMessage.textContent = message;
        }
        if (analyzeErrorMeta) {
            analyzeErrorMeta.innerHTML = items
                .map(([label, value]) => `
                    <div class="rounded-2xl border border-red-500/20 bg-slate-950/40 p-3">
                        <dt class="text-xs uppercase tracking-wide text-red-200/70">${escapeHtml(label)}</dt>
                        <dd class="mt-1 font-semibold text-white">${escapeHtml(value)}</dd>
                    </div>
                `)
                .join('');
        }

        const debugLines = [];
        if (meta.details) {
            debugLines.push(`details: ${meta.details}`);
        }
        if (ANALYZE_DEBUG_MODE && meta.raw_preview) {
            debugLines.push(`raw_preview: ${meta.raw_preview}`);
        }
        if (analyzeErrorDebug) {
            if (debugLines.length) {
                analyzeErrorDebug.textContent = debugLines.join('\n');
                analyzeErrorDebug.classList.remove('hidden');
            } else {
                analyzeErrorDebug.textContent = '';
                analyzeErrorDebug.classList.add('hidden');
            }
        }

        analyzeErrorPanel?.classList.remove('hidden');
        analyzeErrorPanel?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    const makeSelect = document.getElementById('make');
    const modelSelect = document.getElementById('model');
    const yearSelect = document.getElementById('year');
    const form = document.getElementById('car-form');
    const submitBtn = document.getElementById('submit-button');
    const resultsContainer = document.getElementById('results-container');
    const resultReadyPanel = document.getElementById('reliabilityResultReadyPanel');
    const analyzeErrorPanel = document.getElementById('analyze-error-panel');
    const analyzeErrorTitle = document.getElementById('analyze-error-title');
    const analyzeErrorMessage = document.getElementById('analyze-error-message');
    const analyzeErrorMeta = document.getElementById('analyze-error-meta');
    const analyzeErrorDebug = document.getElementById('analyze-error-debug');
    const openResultButton = document.getElementById('reliabilityOpenResultButton');
    const legalCheckbox = document.getElementById('legal-confirm');
    const legalError = document.getElementById('legal-error');
    let legalAccepted = false;
    const reliabilityResearchSection = document.getElementById('reliabilityResearchSection');
    const reliabilityResearchFormWrap = document.getElementById('reliabilityResearchFormWrap');
    const reliabilityResearchForm = document.getElementById('reliabilityResearchForm');
    const reliabilityResearchMessage = document.getElementById('reliabilityResearchMessage');
    const reliabilityResearchAnswerNow = document.getElementById('reliabilityResearchAnswerNow');
    const reliabilityResearchSkip = document.getElementById('reliabilityResearchSkip');
    const reliabilityResearchClose = document.getElementById('reliabilityResearchClose');
    const reliabilityResearchDismiss = document.getElementById('reliabilityResearchDismiss');
    const reliabilityOpenResultNow = document.getElementById('reliabilityOpenResultNow');
    const researchClient = window.YedaResearch
        ? window.YedaResearch.createClient({
            accepted: document.getElementById('researchConsentModal')?.dataset.accepted === 'true',
            defaultSource: 'reliability_results',
            onConsentOpen: function () {
                trackAnalytics('research_consent_opened', { flow_type: 'reliability' });
            },
            onConsentAccepted: function () {
                trackAnalytics('research_consented', { flow_type: 'reliability' });
            }
        })
        : null;
    let isLoading = false;
    let isResultReady = false;
    let isResultOpen = false;
    let currentHistoryId = null;
    let researchCardVisible = false;
    let researchFormOpen = false;
    let lastAnalyzePayload = null;
    let reliabilityResearchTrackedForHistory = null;
    let resultReadyPanelTrackedForHistory = null;
    let analyzeInFlight = false;
    let currentAnalyzeToken = 0;
    const ANALYZE_DEBUG_MODE = (() => {
        try {
            const params = new URLSearchParams(window.location.search || '');
            return params.get('debug') === '1' || ['localhost', '127.0.0.1'].includes(window.location.hostname);
        } catch (err) {
            return false;
        }
    })();

    const summarySimpleEl = document.getElementById('summary-simple-text');
    const summaryDetailedEl = document.getElementById('summary-detailed-text');
    const summaryToggleBtn = document.getElementById('summary-toggle-btn');
    const summaryDetailedBlock = document.getElementById('summary-detailed-block');
    const scoreContainer = document.getElementById('reliability-score-container');
    const sourcesListEl = document.getElementById('sources-list');
    const sourcesBlockEl = document.getElementById('sources-block');
    const reportContainer = document.getElementById('report');

    const faultsContainer = document.getElementById('faults');
    const costsContainer = document.getElementById('costs');
    const competitorsContainer = document.getElementById('competitors');
    // All innerHTML below interpolates values passed through escapeHtml() to prevent XSS

    // Timing Banner Elements
    const timingBanner = document.getElementById('timingBanner');
    const elapsedTimeEl = document.getElementById('elapsedTime');
    const etaTextEl = document.getElementById('etaText');
    const statusTextEl = document.getElementById('statusText');
    const progressRing = document.getElementById('progressRing');
    const RING_CIRCUMFERENCE = 339.292; // 2 * PI * 54

    let timingInterval = null;
    let timingStartTime = null;

    function showTimingBanner(kind = 'analyze') {
        if (!timingBanner) return;
        
        // Fetch ETA estimate
        safeFetchJson(`/api/timing/estimate?kind=${kind}`, {
            method: 'GET',
            credentials: 'include'
        }).then(res => {
            if (res && res.ok) {
                const data = res.data || {};
                const p75_ms = data.p75_ms || 20000;
                const count = data.sample_size || 0;
                const source = data.source || 'default';
                
                if (etaTextEl) {
                    if (count > 0) {
                        etaTextEl.textContent = `זמן משוער: ~${Math.ceil(p75_ms / 1000)} שניות (מבוסס על ${count} בדיקות)`;
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
            if (etaTextEl) etaTextEl.textContent = 'זמן משוער: ~20 שניות';
            
            timingInterval = setInterval(() => {
                const elapsed = Math.floor((performance.now() - timingStartTime) / 1000);
                if (elapsedTimeEl) elapsedTimeEl.textContent = elapsed;
                
                const elapsedMs = performance.now() - timingStartTime;
                const progress = Math.min(1, elapsedMs / 20000);
                const offset = RING_CIRCUMFERENCE * (1 - progress);
                if (progressRing) {
                    progressRing.style.strokeDashoffset = offset;
                    const hue = (elapsedMs / 20000) * 360;
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
                    progressRing.style.stroke = 'url(#rainbowGradient)';
                }
            }, 1500);
        } else {
            if (timingBanner) timingBanner.classList.add('hidden');
            if (statusTextEl) statusTextEl.textContent = 'מעבד...';
            if (progressRing) {
                progressRing.style.strokeDashoffset = RING_CIRCUMFERENCE;
                progressRing.style.stroke = 'url(#rainbowGradient)';
            }
        }
        
        timingStartTime = null;
    }

    window.showTimingBanner = showTimingBanner;
    window.hideTimingBanner = hideTimingBanner;

    // טאבס
    window.openTab = function (evt, tabId) {
        const btns = document.querySelectorAll('.tab-btn');
        const tabs = document.querySelectorAll('.tab-content');
        btns.forEach(b => b.classList.remove('active'));
        tabs.forEach(t => t.classList.remove('active'));
        if (evt && evt.currentTarget) {
            evt.currentTarget.classList.add('active');
        }
        const tab = document.getElementById(tabId);
        if (tab) tab.classList.add('active');
    };

    // טוגל סיכום מפורט
    if (summaryToggleBtn && summaryDetailedBlock) {
        summaryToggleBtn.addEventListener('click', () => {
            const hidden = summaryDetailedBlock.classList.contains('hidden');
            if (hidden) {
                summaryDetailedBlock.classList.remove('hidden');
                summaryToggleBtn.textContent = 'להסתיר הסבר מקצועי';
            } else {
                summaryDetailedBlock.classList.add('hidden');
                summaryToggleBtn.textContent = 'להרחבה מקצועית';
            }
        });
    }

    // בניית מבנה מודלים -> טווח שנים
    const MODEL_MAP = {}; // { make: [ {name, years:[min,max]} ] }

    function buildModelMap() {
        Object.entries(CAR_DATA || {}).forEach(([make, models]) => {
            if (!Array.isArray(models)) return;
            MODEL_MAP[make] = models.map(str => {
                let name = String(str || '').trim();
                let years = null;
                const m = name.match(/\((\d{4})\s*-\s*(\d{2,4})\)/);
                if (m) {
                    const start = parseInt(m[1], 10);
                    let end = parseInt(m[2], 10);
                    if (end < 100) end = 2000 + end;
                    years = [start, end];
                    name = name.replace(m[0], '').trim();
                }
                return { name, years };
            });
        });
    }

    function populateModelsForMake(make) {
        modelSelect.innerHTML = '';
        yearSelect.innerHTML = '';
        yearSelect.disabled = true;

        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = '-- בחר דגם --';
        modelSelect.appendChild(placeholder);

        const items = MODEL_MAP[make] || [];
        items.forEach(m => {
            const opt = document.createElement('option');
            opt.value = m.name;
            opt.textContent = m.name;
            modelSelect.appendChild(opt);
        });

        modelSelect.disabled = items.length === 0;
        if (!items.length) {
            modelSelect.innerHTML = '';
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = '-- Select Make First --';
            modelSelect.appendChild(opt);
            modelSelect.disabled = true;
        }
    }

    function populateYearsForModel(make, modelName) {
        yearSelect.innerHTML = '';
        const items = MODEL_MAP[make] || [];
        const found = items.find(m => m.name === modelName);
        const nowYear = new Date().getFullYear();
        let from = nowYear - 20;
        let to = nowYear + 1;

        if (found && Array.isArray(found.years)) {
            from = found.years[0];
            to = found.years[1];
        }

        for (let y = to; y >= from; y--) {
            const opt = document.createElement('option');
            opt.value = String(y);
            opt.textContent = String(y);
            yearSelect.appendChild(opt);
        }
        yearSelect.disabled = false;
    }

    function setSubmitting(isSubmitting) {
        if (!submitBtn) return;
        const spinner = submitBtn.querySelector('.spinner');
        const textSpan = submitBtn.querySelector('.button-text');

        submitBtn.disabled = isSubmitting;
        if (spinner) spinner.classList.toggle('hidden', !isSubmitting);
        if (textSpan) textSpan.classList.toggle('opacity-60', isSubmitting);
    }


    function renderAnalyzeResult(data, options = {}) {
        if (!resultsContainer) return;
        const safe = (v) => escapeHtml(v);
        const infoReview = normalizeInfoReview(data);
        const checklist = infoReview.checklist || {};

        clearAnalyzeError();

        if (scoreContainer) {
            scoreContainer.innerHTML = '';
            const sourceTag = data.source_tag || '';
            const mileageNote = data.mileage_note || '';

            const wrapper = document.createElement('div');
            wrapper.className = 'w-full rounded-3xl border border-slate-700/70 bg-slate-900/40 p-5 md:p-6 text-right';

            const headline = document.createElement('div');
            headline.className = 'text-2xl md:text-3xl font-black text-white';
            headline.textContent = 'לפי המידע הזמין';
            wrapper.appendChild(headline);

            const quality = document.createElement('div');
            quality.className = 'mt-3 text-sm md:text-base text-slate-200';
            quality.textContent = `איכות המידע לניתוח: ${infoReview.dataQualityLabel}`;
            wrapper.appendChild(quality);

            const readiness = document.createElement('div');
            readiness.className = 'mt-2 text-sm text-slate-300';
            readiness.textContent = `מצב הבדיקה: ${infoReview.decisionReadiness}`;
            wrapper.appendChild(readiness);

            const verify = document.createElement('div');
            verify.className = 'mt-4 text-sm text-slate-200';
            verify.textContent = `לפני החלטה יש לאמת: ${infoReview.checksToVerify.length ? infoReview.checksToVerify.join(' • ') : 'בדיקת מוסך, מסמכים ועדכוני יצרן.'}`;
            wrapper.appendChild(verify);

            const systemLine = document.createElement('div');
            systemLine.className = 'mt-3 text-sm text-slate-300';
            systemLine.textContent = 'המערכת לא קובעת אם לקנות את הרכב, אלא מציפה נקודות לבדיקה.';
            wrapper.appendChild(systemLine);

            if (infoReview.basedOnAvailableInformation) {
                const detail = document.createElement('p');
                detail.className = 'mt-3 text-sm text-slate-400 leading-relaxed';
                detail.textContent = infoReview.basedOnAvailableInformation;
                wrapper.appendChild(detail);
            }

            if (infoReview.verificationFocus.length) {
                const focusTitle = document.createElement('div');
                focusTitle.className = 'mt-4 text-xs font-semibold text-slate-400';
                focusTitle.textContent = 'מוקדי אימות מרכזיים';
                wrapper.appendChild(focusTitle);

                const focusList = document.createElement('ul');
                focusList.className = 'mt-2 list-disc list-inside space-y-1 text-sm text-slate-200';
                infoReview.verificationFocus.slice(0, 4).forEach(item => {
                    const li = document.createElement('li');
                    li.textContent = item;
                    focusList.appendChild(li);
                });
                wrapper.appendChild(focusList);
            }

            if (infoReview.knownUncertainties.length) {
                const unknownsBlock = document.createElement('div');
                unknownsBlock.className = 'mt-4 rounded-2xl border border-slate-700/70 bg-slate-950/40 p-4';
                unknownsBlock.innerHTML = `
                    <div class="font-semibold text-white">אי-ודאויות ידועות</div>
                    <ul class="mt-2 list-disc list-inside space-y-1 text-sm text-slate-200">
                        ${infoReview.knownUncertainties.slice(0, 4).map(item => `<li>${safe(item)}</li>`).join('')}
                    </ul>
                `;
                wrapper.appendChild(unknownsBlock);
            }

            if (sourceTag) {
                const p = document.createElement('p');
                p.textContent = sourceTag;
                p.className = 'mt-4 text-[11px] text-slate-500';
                wrapper.appendChild(p);
            }

            if (mileageNote) {
                const p = document.createElement('p');
                p.textContent = mileageNote;
                p.className = 'mt-3 text-xs bg-amber-950/40 text-amber-300 border border-amber-700/60 rounded-lg px-3 py-2';
                wrapper.appendChild(p);
            }

            scoreContainer.appendChild(wrapper);
        }

        if (summarySimpleEl) {
            summarySimpleEl.textContent = (infoReview.basedOnAvailableInformation || '').trim() || 'אין סיכום זמין.';
        }
        if (summaryDetailedEl) {
            const detailLines = [
                `מצב הבדיקה: ${infoReview.decisionReadiness}`,
                infoReview.missingInfo.length ? `מידע קריטי חסר: ${infoReview.missingInfo.join(' • ')}` : '',
                infoReview.verificationFocus.length ? `מוקדי אימות: ${infoReview.verificationFocus.join(' • ')}` : '',
            ].filter(Boolean);
            summaryDetailedEl.textContent = detailLines.join('\n') || 'אין סיכום מקצועי זמין.';
        }
        if (summaryDetailedBlock && !summaryDetailedBlock.classList.contains('hidden')) {
            // להשאיר פתוח אם המשתמש כבר פתח
        }

        if (faultsContainer) {
            const arr = infoReview.riskAreas;
            let html = '';
            if (arr.length) {
                html += '<h4 class="text-base font-semibold text-white mb-2">תחומי סיכון מרכזיים לבדיקה</h4>';
                html += '<ul class="list-disc list-inside space-y-1 text-sm text-slate-200">';
                html += arr.map(item => {
                    if (item && typeof item === 'object') {
                        return `<li><span class="font-semibold">${safe(item.risk_area || '')}</span>${item.why_to_check ? ` – ${safe(item.why_to_check)}` : ''}</li>`;
                    }
                    return `<li>${safe(item)}</li>`;
                }).join('');
                html += '</ul>';
            } else {
                html += '<p class="text-sm text-slate-400">לא התקבלו תחומי סיכון מפורטים.</p>';
            }

            // Legacy / fallback: common_issues
            const commonIssues = Array.isArray(data.common_issues) ? data.common_issues.filter(Boolean) : [];
            if (commonIssues.length) {
                html += '<h5 class="text-sm font-semibold text-slate-300 mt-4 mb-1">תקלות מתועדות בדגם</h5>';
                html += '<ul class="list-disc list-inside space-y-1 text-sm text-slate-300">';
                html += commonIssues.map(item => `<li>${safe(item)}</li>`).join('');
                html += '</ul>';
            }

            // Legacy / fallback: recommended_checks
            const recommendedChecks = Array.isArray(data.recommended_checks) ? data.recommended_checks.filter(Boolean) : [];
            if (recommendedChecks.length) {
                html += '<h5 class="text-sm font-semibold text-slate-300 mt-4 mb-1">בדיקות קונקרטיות מומלצות</h5>';
                html += '<ul class="list-disc list-inside space-y-1 text-sm text-slate-300">';
                html += recommendedChecks.map(item => `<li>${safe(item)}</li>`).join('');
                html += '</ul>';
            }

            faultsContainer.innerHTML = html;
        }

        if (costsContainer) {
            const list = infoReview.estimatedCostSensitivity;
            let html = '';
            if (list.length) {
                html += '<h4 class="text-base font-semibold text-white mb-2">רגישות עלויות משוערת</h4>';
                html += '<ul class="list-disc list-inside space-y-1 text-sm text-slate-200">';
                html += list.map(item => `<li>${safe(item)}</li>`).join('');
                html += '</ul>';
            } else {
                html += '<p class="text-sm text-slate-400">לא התקבל פירוט על רגישות העלויות.</p>';
            }

            // Legacy / fallback: avg_repair_cost_ILS and issues_with_costs
            const avgCost = data.avg_repair_cost_ILS;
            const issuesWithCosts = Array.isArray(data.issues_with_costs) ? data.issues_with_costs.filter(Boolean) : [];
            if (avgCost || issuesWithCosts.length) {
                html += '<h5 class="text-sm font-semibold text-slate-300 mt-4 mb-1">טווחי עלויות משוערים</h5>';
                if (avgCost) {
                    html += `<p class="text-sm text-slate-200 mb-1">עלות תיקון ממוצעת: <span class="font-semibold">${safe(String(avgCost))} ₪</span></p>`;
                }
                if (issuesWithCosts.length) {
                    html += '<ul class="list-disc list-inside space-y-1 text-sm text-slate-300">';
                    html += issuesWithCosts.map(item => {
                        if (item && typeof item === 'object') {
                            const label = safe(item.issue || item.name || '');
                            const cost = safe(item.cost_ILS || item.cost || '');
                            return `<li>${label}${cost ? ` – ${cost} ₪` : ''}</li>`;
                        }
                        return `<li>${safe(item)}</li>`;
                    }).join('');
                    html += '</ul>';
                }
            }

            costsContainer.innerHTML = html;
        }

        if (competitorsContainer) {
            const arr = infoReview.knownUncertainties;
            let html = '';
            if (arr.length) {
                html += '<p class="text-sm text-slate-300 mb-3">נקודות שעדיין חסר עליהם מידע ושכדאי לוודא מול המוכר/מוסך בדיקה:</p>';
                html += '<ul class="space-y-2 text-sm text-slate-200">';
                html += arr.map(item => `
                    <li class="bg-slate-900/40 border border-slate-700/70 rounded-xl px-3 py-2">${safe(item)}</li>
                `).join('');
                html += '</ul>';
            } else {
                html += '<p class="text-sm text-slate-400">לא דווחו נקודות פתוחות נוספות.</p>';
            }
            competitorsContainer.innerHTML = html;
        }

        if (sourcesListEl && sourcesBlockEl) {
            const sources = infoReview.sources;
            sourcesListEl.innerHTML = '';
            if (!sources.length) {
                sourcesBlockEl.classList.add('hidden');
            } else {
                sourcesBlockEl.classList.remove('hidden');
                sources.forEach((src) => {
                    const li = document.createElement('li');
                    if (src && typeof src === 'object') {
                        const title = safe(src.title || '');
                        const url = safe(src.url || '');
                        const safeHref = sanitizeUrl(url);
                        li.innerHTML = safeHref ? `<a class="text-primary hover:underline" href="${safeHref}" target="_blank" rel="noopener noreferrer">${title || url}</a>` : (title || url);
                    } else {
                        li.textContent = safe(src || '');
                    }
                    sourcesListEl.appendChild(li);
                });
            }
        }

        if (reportContainer) {
            const riskAreas = infoReview.riskAreas;
            const uncertainties = infoReview.knownUncertainties;
            const costSensitivity = infoReview.estimatedCostSensitivity;
            let html = '';
            if (
                !infoReview.basedOnAvailableInformation &&
                !riskAreas.length &&
                !uncertainties.length &&
                !costSensitivity.length &&
                !Object.keys(checklist).length
            ) {
                html = '<p class="text-sm text-slate-400">לא התקבלו פרטי ניתוח להצגה.</p>';
            } else {
                html += `
                    <div class="space-y-3">
                        <p class="text-slate-200 text-sm">${safe(infoReview.basedOnAvailableInformation || '')}</p>
                        <div>
                            <h4 class="text-sm font-semibold text-white mb-1">תחומי סיכון מרכזיים לבדיקה</h4>
                            <ul class="list-disc list-inside text-sm text-slate-200 space-y-1">
                                ${riskAreas.slice(0, 6).map(r => `<li><span class="font-semibold">${safe(r.risk_area || '')}</span>${r.why_to_check ? ` – ${safe(r.why_to_check)}` : ''}</li>`).join('') || '<li class="text-slate-400">אין תחומי סיכון מפורטים.</li>'}
                            </ul>
                        </div>
                        <div>
                            <h4 class="text-sm font-semibold text-white mb-1">מה חייבים לבדוק לפני החלטה</h4>
                            <div class="space-y-2 text-sm text-slate-200">
                                <div>
                                    <div class="font-semibold text-white mb-1">נקודות בדיקה מכניות</div>
                                    <ul class="list-disc list-inside space-y-1">
                                        ${(checklist.mechanical_inspection_points || []).map(x => `<li>${safe(x)}</li>`).join('') || '<li class="text-slate-400">אין נתונים</li>'}
                                    </ul>
                                </div>
                                <div>
                                    <div class="font-semibold text-white mb-1">מסמכים לאימות</div>
                                    <ul class="list-disc list-inside space-y-1">
                                        ${(checklist.documents_to_verify || []).map(x => `<li>${safe(x)}</li>`).join('') || '<li class="text-slate-400">אין נתונים</li>'}
                                    </ul>
                                </div>
                                <div>
                                    <div class="font-semibold text-white mb-1">שאלות למוכר</div>
                                    <ul class="list-disc list-inside space-y-1">
                                        ${(checklist.questions_to_ask_seller || []).map(x => `<li>${safe(x)}</li>`).join('') || '<li class="text-slate-400">אין נתונים</li>'}
                                    </ul>
                                </div>
                                <div>
                                    <div class="font-semibold text-white mb-1">דגלים אדומים</div>
                                    <ul class="list-disc list-inside space-y-1">
                                        ${(checklist.red_flags_to_look_for || []).map(x => `<li>${safe(x)}</li>`).join('') || '<li class="text-slate-400">אין נתונים</li>'}
                                    </ul>
                                </div>
                            </div>
                        </div>
                        <div>
                            <h4 class="text-sm font-semibold text-white mb-1">אי-ודאויות ידועות</h4>
                            <ul class="list-disc list-inside text-sm text-slate-200 space-y-1">
                                ${uncertainties.map(x => `<li>${safe(x)}</li>`).join('') || '<li class="text-slate-400">אין מידע</li>'}
                            </ul>
                        </div>
                        <div>
                            <h4 class="text-sm font-semibold text-white mb-1">רגישות עלויות משוערת</h4>
                            <ul class="list-disc list-inside text-sm text-slate-200 space-y-1">
                                ${costSensitivity.map(x => `<li>${safe(x)}</li>`).join('') || '<li class="text-slate-400">אין מידע</li>'}
                            </ul>
                        </div>
                    </div>
                `;
            }
            reportContainer.innerHTML = html;
        }

        currentHistoryId = data.history_id || null;
        isLoading = false;
        isResultReady = true;
        isResultOpen = false;
        researchCardVisible = false;
        researchFormOpen = false;

        trackAnalytics('result_rendered', {
            flow_type: 'reliability',
            search_history_id: currentHistoryId,
        });

        renderFeedbackCTA(resultsContainer, currentHistoryId);
        closeReliabilityResult();
        if (options.openImmediately) {
            resultReadyPanel?.classList.add('hidden');
            showReliabilityResearchCard();
            openReliabilityResult({ userInitiated: false });
            return;
        }
        showReliabilityReadyPanel();
        showReliabilityResearchCard();
    }

    function validateLegal() {
        if (!legalCheckbox || !legalError) return true;
        if (!legalCheckbox.checked) {
            legalError.classList.remove('hidden');
            legalError.scrollIntoView({ behavior: 'smooth', block: 'center' });
            return false;
        }
        legalError.classList.add('hidden');
        return true;
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

    function collectFormData() {
        const payload = {
            make: makeSelect ? makeSelect.value.trim() : '',
            model: modelSelect ? modelSelect.value.trim() : '',
            year: yearSelect ? yearSelect.value.trim() : '',
            mileage_range: (document.getElementById('mileage_range') || {}).value || '',
            fuel_type: (document.getElementById('fuel_type') || {}).value || '',
            transmission: (document.getElementById('transmission') || {}).value || '',
            sub_model: (document.getElementById('sub_model') || {}).value || '',
            annual_km: Number((document.getElementById('annual_km') || {}).value || 15000),
            city_pct: Number((document.getElementById('city_pct') || {}).value || 50),
            terrain: (document.getElementById('terrain') || {}).value || 'mixed',
            climate: (document.getElementById('climate') || {}).value || 'center',
            parking: (document.getElementById('parking') || {}).value || 'outdoor',
            driver_style: (document.getElementById('driver_style') || {}).value || 'normal',
            load: (document.getElementById('load') || {}).value || 'family',
        };
        return payload;
    }

    async function handleSubmit(e) {
        e.preventDefault();
        if (analyzeInFlight) {
            return;
        }
        if (!validateLegal()) return;

        analyzeInFlight = true;
        const token = ++currentAnalyzeToken;
        let showCompletionMessage = false;
        let timingStarted = false;
        setSubmitting(true);

        try {
            if (!(await ensureLegalAcceptance())) return;

            const formPayload = collectFormData();
            if (!formPayload.make || !formPayload.model || !formPayload.year) {
                showAnalyzeError('נא למלא יצרן, דגם ושנתון.', { type: 'backend_error' });
                return;
            }

            resetResultFlowState();
            isLoading = true;
            lastAnalyzePayload = formPayload;
            const payload = { ...lastAnalyzePayload, legal_confirm: true };
            trackAnalytics('result_requested', { flow_type: 'reliability' });
            console.info('[ANALYZE_START]', { token });
            showTimingBanner('analyze');
            timingStarted = true;

            let response;
            try {
                response = await fetch('/analyze', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json',
                        'X-CSRF-Token': getCSRFToken(),
                    },
                    credentials: 'include',
                    body: JSON.stringify(payload)
                });
            } catch (err) {
                if (token !== currentAnalyzeToken) return;
                showAnalyzeError('שגיאת רשת בשליחת הבקשה.', {
                    type: 'network_error',
                    details: err.message,
                });
                return;
            }

            if (token !== currentAnalyzeToken) return;
            console.info('[ANALYZE_HTTP]', { status: response.status });

            let rawText = '';
            try {
                rawText = await response.text();
            } catch (err) {
                if (token !== currentAnalyzeToken) return;
                showAnalyzeError('לא הצלחנו לקרוא את תגובת השרת', {
                    type: 'network_error',
                    status: response.status,
                    details: err.message,
                });
                return;
            }

            if (token !== currentAnalyzeToken) return;

            let payloadFromApi;
            try {
                payloadFromApi = rawText ? JSON.parse(rawText) : {};
            } catch (err) {
                showAnalyzeError('השרת החזיר תגובה שלא ניתן לקרוא', {
                    type: 'json_parse_error',
                    status: response.status,
                    raw_preview: rawText.slice(0, 300)
                });
                return;
            }

            if (token !== currentAnalyzeToken) return;
            console.info('[ANALYZE_PAYLOAD_KEYS]', Object.keys(payloadFromApi || {}));

            if (!response.ok) {
                showAnalyzeError('השרת החזיר שגיאה', {
                    type: 'backend_error',
                    status: response.status,
                    requestId: payloadFromApi?.request_id,
                    details: payloadFromApi?.error?.message || payloadFromApi?.error || payloadFromApi?.message,
                });
                if (payloadFromApi?.error?.code === 'unauthenticated') {
                    window.location.href = '/login';
                }
                return;
            }

            if (!payloadFromApi || typeof payloadFromApi !== 'object') {
                showAnalyzeError('השרת החזיר תשובה חלקית', {
                    type: 'backend_error',
                    status: response.status,
                });
                return;
            }

            if (payloadFromApi.ok === false) {
                showAnalyzeError('השרת החזיר שגיאה', {
                    type: 'backend_error',
                    status: response.status,
                    requestId: payloadFromApi.request_id,
                    details: payloadFromApi?.error?.message || payloadFromApi?.error || payloadFromApi?.message,
                });
                return;
            }

            const normalized = normalizeAnalyzeResponse(payloadFromApi);
            if (!normalized.result || typeof normalized.result !== 'object') {
                showAnalyzeError('השרת החזיר תשובה חלקית', {
                    type: 'backend_error',
                    status: response.status,
                    requestId: normalized.requestId,
                    details: 'missing result payload',
                });
                return;
            }

            if (token !== currentAnalyzeToken) return;
            console.info('[ANALYZE_RENDER_START]', { requestId: normalized.requestId });
            try {
                renderAnalyzeResult(normalized.result);
            } catch (err) {
                console.error('[ANALYZE_RENDER_ERROR]', err, payloadFromApi);
                showAnalyzeError('הניתוח הצליח אך הייתה בעיה בהצגת התוצאה', {
                    type: 'render_error',
                    requestId: normalized.requestId,
                    details: err.message
                });
                return;
            }
            console.info('[ANALYZE_RENDER_DONE]', { requestId: normalized.requestId });
            showCompletionMessage = true;
        } finally {
            if (token !== currentAnalyzeToken) return;
            if (!isResultReady) {
                hideReliabilityResearchCard();
                resultReadyPanel?.classList.add('hidden');
            }
            isLoading = false;
            analyzeInFlight = false;
            hideTimingBanner(timingStarted && showCompletionMessage);
            setSubmitting(false);
        }
    }

    // אתחול
    document.addEventListener('DOMContentLoaded', () => {
        buildModelMap();

        if (makeSelect) {
            makeSelect.addEventListener('change', () => {
                const val = makeSelect.value;
                if (val) {
                    populateModelsForMake(val);
                } else {
                    modelSelect.value = '';
                    modelSelect.disabled = true;
                    yearSelect.value = '';
                    yearSelect.disabled = true;
                }
            });
        }

        if (modelSelect) {
            modelSelect.addEventListener('change', () => {
                const make = makeSelect ? makeSelect.value : '';
                const model = modelSelect.value;
                if (make && model) {
                    populateYearsForModel(make, model);
                } else {
                    yearSelect.value = '';
                    yearSelect.disabled = true;
                }
            });
        }

        if (form) {
            form.addEventListener('submit', handleSubmit);
        }

        openResultButton?.addEventListener('click', function () {
            openReliabilityResult({ userInitiated: true });
        });
        reliabilityOpenResultNow?.addEventListener('click', function () {
            closeReliabilityResearch({ reason: 'open_result_now', openResult: true });
        });
        reliabilityResearchAnswerNow?.addEventListener('click', function () {
            openReliabilityResearchForm();
        });
        reliabilityResearchSkip?.addEventListener('click', function () {
            closeReliabilityResearch({ reason: 'skip', trackSkipped: true });
        });
        reliabilityResearchClose?.addEventListener('click', function () {
            closeReliabilityResearch({ reason: 'close_button' });
        });
        reliabilityResearchDismiss?.addEventListener('click', function () {
            closeReliabilityResearch({ reason: 'dismiss_form' });
        });

        if (reliabilityResearchForm && researchClient) {
            reliabilityResearchForm.addEventListener('submit', async (event) => {
                event.preventDefault();
                if (!currentHistoryId || !lastAnalyzePayload) {
                    setReliabilityResearchMessage('קודם צריך להפיק תוצאת אמינות לפני שמירת תשובות המחקר.', 'error');
                    return;
                }
                const responses = [];
                const ownershipStatus = reliabilityResearchForm.querySelector('input[name="ownership_status"]:checked')?.value || '';
                const garageType = document.getElementById('reliabilityGarageType')?.value || '';
                const lastServiceCost = document.getElementById('reliabilityLastServiceCost')?.value || '';
                const firstTestPass = reliabilityResearchForm.querySelector('input[name="first_test_pass"]:checked')?.value || '';
                const outOfWarrantyRepairs = reliabilityResearchForm.querySelector('input[name="out_of_warranty_repairs"]:checked')?.value || '';

                if (ownershipStatus) {
                    responses.push({
                        question_code: 'ownership_status',
                        response: { ownership_status: ownershipStatus },
                    });
                }
                if (garageType || lastServiceCost) {
                    responses.push({
                        question_code: 'maintenance_profile',
                        response: {
                            garage_type: garageType,
                            last_service_cost_ils: lastServiceCost,
                        },
                    });
                }
                if (firstTestPass) {
                    responses.push({
                        question_code: 'first_test_pass',
                        response: {
                            first_test_pass: firstTestPass === 'true',
                        },
                    });
                }
                if (outOfWarrantyRepairs) {
                    responses.push({
                        question_code: 'out_of_warranty_repairs',
                        response: {
                            out_of_warranty_repairs: outOfWarrantyRepairs === 'true',
                        },
                    });
                }
                if (!responses.length) {
                    setReliabilityResearchMessage('צריך למלא לפחות תשובת מחקר אחת כדי לשמור.', 'warning');
                    return;
                }
                if (!(await researchClient.ensureConsent('reliability_results'))) {
                    return;
                }

                try {
                    await researchClient.saveResponses({
                        flow_type: 'reliability',
                        source_analysis_type: 'search_history',
                        source_record_id: currentHistoryId,
                        vehicle_context: {
                            make: lastAnalyzePayload.make,
                            model: lastAnalyzePayload.model,
                            year: lastAnalyzePayload.year,
                            mileage_range: lastAnalyzePayload.mileage_range,
                            fuel_type: lastAnalyzePayload.fuel_type,
                            transmission: lastAnalyzePayload.transmission,
                        },
                        responses,
                    });
                    setReliabilityResearchMessage('תודה — התשובות נשמרו למחקר בלבד.', 'success');
                    markResearchPromptSeen('reliability', currentHistoryId);
                    trackAnalytics('research_completed', {
                        flow_type: 'reliability',
                        search_history_id: currentHistoryId,
                    });
                } catch (err) {
                    trackAnalytics('research_save_failed', {
                        flow_type: 'reliability',
                        search_history_id: currentHistoryId,
                        message: err.message || 'save_failed',
                    });
                    setReliabilityResearchMessage(
                        'לא הצלחנו לשמור את התשובות כרגע. התוצאה שלך עדיין זמינה.',
                        'error'
                    );
                }
            });
        }
        
        // Load history list only for authenticated sessions
        if (window.__IS_AUTHENTICATED__ === true && typeof window.loadHistoryList === 'function') {
            window.loadHistoryList();
        }
    });
    
    // History comparison functions (global scope for onclick handlers)
    window.loadHistoryList = async function() {
        try {
            const res = await safeFetchJson('/api/history/list', {
                method: 'GET',
                credentials: 'same-origin'
            });
            
            if (!res.ok) {
                if (res.error?.details?.status === 401 || res.error?.code === 'unauthenticated') {
                    return;
                }
                console.error('Failed to load history:', res.error);
                return;
            }
            
            const searches = res.data?.searches || [];
            const select1 = document.getElementById('history-select-1');
            const select2 = document.getElementById('history-select-2');
            
            if (!select1 || !select2) return;
            
            // Clear and populate selects
            select1.innerHTML = '<option value="">בחר חיפוש...</option>';
            select2.innerHTML = '<option value="">בחר חיפוש...</option>';
            
            searches.forEach(item => {
                const option1 = document.createElement('option');
                option1.value = item.id;
                option1.textContent = `${item.make} ${item.model} ${item.year} (${item.timestamp.split('T')[0]})`;
                select1.appendChild(option1);
                
                const option2 = option1.cloneNode(true);
                select2.appendChild(option2);
            });
        } catch (err) {
            console.error('Error loading history:', err);
        }
    };
    
    window.compareHistory = async function() {
        const select1 = document.getElementById('history-select-1');
        const select2 = document.getElementById('history-select-2');
        const resultDiv = document.getElementById('comparison-result');
        
        if (!select1 || !select2 || !resultDiv) return;
        
        const id1 = select1.value;
        const id2 = select2.value;
        
        if (!id1 || !id2) {
            alert('נא לבחור שני חיפושים להשוואה');
            return;
        }
        
        if (id1 === id2) {
            alert('נא לבחור שני חיפושים שונים');
            return;
        }
        
        try {
            // Fetch both items
            const [res1, res2] = await Promise.all([
                safeFetchJson(`/api/history/item/${id1}`, {
                    method: 'GET',
                    credentials: 'same-origin'
                }),
                safeFetchJson(`/api/history/item/${id2}`, {
                    method: 'GET',
                    credentials: 'same-origin'
                })
            ]);
            
            if (!res1.ok || !res2.ok) {
                alert('שגיאה בטעינת נתוני ההשוואה');
                return;
            }
            
            const item1 = res1.data;
            const item2 = res2.data;
            
            // Hebrew labels
            const labels = {
                'data_quality_label': 'איכות מידע',
                'decision_readiness': 'מצב בדיקה',
                'top_missing_info': 'חוסר מידע מרכזי',
                'top_verification_focus': 'מוקד אימות מרכזי',
                'avg_repair_cost_ILS': 'עלות תיקון ממוצעת (₪)'
            };
            
            // Build comparison HTML
            let html = `
                <div class="bg-slate-900/40 border border-slate-700/70 rounded-2xl p-6">
                    <h4 class="text-lg font-bold text-white mb-4">השוואה</h4>
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                        <div class="text-center">
                            <div class="text-sm text-slate-400 mb-2">רכב 1</div>
                            <div class="font-bold text-white">${escapeHtml(item1.make)} ${escapeHtml(item1.model)} ${escapeHtml(item1.year)}</div>
                        </div>
                        <div class="text-center text-slate-500">vs</div>
                        <div class="text-center">
                            <div class="text-sm text-slate-400 mb-2">רכב 2</div>
                            <div class="font-bold text-white">${escapeHtml(item2.make)} ${escapeHtml(item2.model)} ${escapeHtml(item2.year)}</div>
                        </div>
                    </div>
                    <div class="space-y-3">
            `;
            
            const missing1 = Array.isArray(item1.result?.missing_critical_info) ? item1.result.missing_critical_info[0] : '—';
            const missing2 = Array.isArray(item2.result?.missing_critical_info) ? item2.result.missing_critical_info[0] : '—';
            const focus1 = Array.isArray(item1.result?.verification_focus) ? item1.result.verification_focus[0] : '—';
            const focus2 = Array.isArray(item2.result?.verification_focus) ? item2.result.verification_focus[0] : '—';
            const quality1 = item1.result?.data_quality_label || 'חלקית';
            const quality2 = item2.result?.data_quality_label || 'חלקית';
            const readiness1 = item1.result?.decision_readiness || 'נדרש אימות נוסף';
            const readiness2 = item2.result?.decision_readiness || 'נדרש אימות נוסף';

            html += `
                <div class="flex justify-between items-center py-2 border-b border-slate-700/50">
                    <span class="text-slate-300">${labels['data_quality_label']}</span>
                    <div class="flex gap-4 items-center">
                        <span class="font-bold text-white">${escapeHtml(quality1)}</span>
                        <span class="text-slate-500">←→</span>
                        <span class="font-bold text-white">${escapeHtml(quality2)}</span>
                    </div>
                </div>
            `;

            [
                [labels['decision_readiness'], readiness1, readiness2],
                [labels['top_missing_info'], missing1, missing2],
                [labels['top_verification_focus'], focus1, focus2],
            ].forEach(([label, val1, val2]) => {
                html += `
                    <div class="flex justify-between items-center py-2 border-b border-slate-700/50">
                        <span class="text-slate-300 text-sm">${label}</span>
                        <div class="flex gap-4 items-center">
                            <span class="text-white max-w-[10rem] text-right">${escapeHtml(val1 || '—')}</span>
                            <span class="text-slate-500">←→</span>
                            <span class="text-white max-w-[10rem] text-right">${escapeHtml(val2 || '—')}</span>
                        </div>
                    </div>
                `;
            });

            const avg1 = item1.result?.avg_repair_cost_ILS;
            const avg2 = item2.result?.avg_repair_cost_ILS;
            if (avg1 !== undefined || avg2 !== undefined) {
                html += `
                    <div class="flex justify-between items-center py-2 border-b border-slate-700/50">
                        <span class="text-slate-300 text-sm">${labels['avg_repair_cost_ILS']}</span>
                        <div class="flex gap-4 items-center">
                            <span class="text-white">${escapeHtml(avg1 ?? '—')}</span>
                            <span class="text-slate-500">←→</span>
                            <span class="text-white">${escapeHtml(avg2 ?? '—')}</span>
                        </div>
                    </div>
                `;
            }
            
            // Add duration comparison if available
            if (item1.duration_ms || item2.duration_ms) {
                const dur1 = item1.duration_ms ? (item1.duration_ms / 1000).toFixed(1) : 'N/A';
                const dur2 = item2.duration_ms ? (item2.duration_ms / 1000).toFixed(1) : 'N/A';
                
                html += `
                    <div class="flex justify-between items-center py-2 border-b border-slate-700/50">
                        <span class="text-slate-300 text-sm">זמן עיבוד (שניות)</span>
                        <div class="flex gap-4 items-center">
                            <span class="text-white text-sm">${dur1}</span>
                            <span class="text-slate-500 text-sm">—</span>
                            <span class="text-white text-sm">${dur2}</span>
                        </div>
                    </div>
                `;
            }
            
            // Add repair cost comparison if available
            if (item1.result?.avg_repair_cost_ILS || item2.result?.avg_repair_cost_ILS) {
                const cost1 = item1.result?.avg_repair_cost_ILS || 'N/A';
                const cost2 = item2.result?.avg_repair_cost_ILS || 'N/A';
                
                html += `
                    <div class="flex justify-between items-center py-2 border-b border-slate-700/50">
                        <span class="text-slate-300 text-sm">${labels['avg_repair_cost_ILS']}</span>
                        <div class="flex gap-4 items-center">
                            <span class="text-white text-sm">${cost1}</span>
                            <span class="text-slate-500 text-sm">vs</span>
                            <span class="text-white text-sm">${cost2}</span>
                        </div>
                    </div>
                `;
            }
            
            html += `
                    </div>
                </div>
            `;
            
            resultDiv.innerHTML = html;
            resultDiv.classList.remove('hidden');
            
        } catch (err) {
            console.error('Error comparing history:', err);
            alert('שגיאה בהשוואה');
        }
    };
    // ============================================================
    // FEEDBACK CTA
    // ============================================================
    function renderFeedbackCTA(container, historyId) {
        if (!container) return;
        // Remove existing feedback CTA if any
        var existing = container.querySelector('.feedback-cta');
        if (existing) existing.remove();

        var wrapper = document.createElement('div');
        wrapper.className = 'feedback-cta mt-6 p-4 rounded-2xl border border-slate-700/60 bg-dark-lighter/60 text-center fade-in';
        wrapper.innerHTML =
            '<p class="text-sm text-slate-300 mb-3">האם הניתוח היה מועיל?</p>' +
            '<div class="flex justify-center gap-4">' +
                '<button data-feedback="positive" class="feedback-btn px-5 py-2.5 rounded-xl border border-emerald-500/40 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20 transition text-lg" title="👍">👍</button>' +
                '<button data-feedback="negative" class="feedback-btn px-5 py-2.5 rounded-xl border border-red-500/40 bg-red-500/10 text-red-300 hover:bg-red-500/20 transition text-lg" title="👎">👎</button>' +
            '</div>';

        container.appendChild(wrapper);

        wrapper.querySelectorAll('.feedback-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                var isPositive = btn.getAttribute('data-feedback') === 'positive';
                var payload = { is_positive: isPositive };
                if (historyId) payload.search_history_id = historyId;

                safeFetchJson('/api/feedback', {
                    method: 'POST',
                    body: JSON.stringify(payload),
                }).then(function(resp) {
                    wrapper.innerHTML = '<p class="text-sm text-emerald-300 font-semibold py-2">תודה על הפידבק!</p>';
                }).catch(function() {
                    wrapper.innerHTML = '<p class="text-sm text-red-300 font-semibold py-2">שגיאה בשליחת פידבק</p>';
                });
            });
        });
    }

    // Expose for compare page use
    window.renderFeedbackCTA = renderFeedbackCTA;
    window.renderResults = renderAnalyzeResult;
    window.renderAnalyzeResult = renderAnalyzeResult;

    // Public example page bootstrap: if server injected example data, render it.
    const exampleDataEl = document.getElementById('example-data');
    if (exampleDataEl) {
        try {
            const exampleData = JSON.parse(exampleDataEl.textContent || '{}');
            if (exampleData && Object.keys(exampleData).length > 0) {
                renderAnalyzeResult(exampleData, { openImmediately: true });
            }
        } catch (e) {
            console.error('[EXAMPLE] failed to parse example data', e);
        }
    }

})();
