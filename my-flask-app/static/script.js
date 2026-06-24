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
    const ackDataScript = document.getElementById('reliability-ack-data');
    let RESULT_ACK_DATA = { acknowledged: false, feature_key: '', version: '' };
    if (ackDataScript) {
        try {
            RESULT_ACK_DATA = JSON.parse(ackDataScript.textContent || ackDataScript.innerHTML || '{}');
        } catch (e) {
            RESULT_ACK_DATA = { acknowledged: false, feature_key: '', version: '' };
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


    const GENERIC_RESULT_TEXTS = new Set([
        'לא נמצא מידע זמין',
        'הסבר AI לא זמין כרגע',
        'מוצגת השוואה מספרית',
        'מידע על איכות הניתוח טרם נטען',
        'לא ידוע / לבדיקה',
        'לא זמין',
        'אין סיכום זמין.',
        'אין סיכום מקצועי זמין.',
        'דורש בדיקה',
        'מידע חלקי'
    ]);

    function meaningfulText(value) {
        const text = String(value ?? '').trim();
        if (!text) return '';
        if (GENERIC_RESULT_TEXTS.has(text)) return '';
        return text;
    }

    function meaningfulList(items) {
        return (Array.isArray(items) ? items : [])
            .map((item) => (typeof item === 'string' ? meaningfulText(item) : item))
            .filter((item) => {
                if (!item) return false;
                if (typeof item !== 'object') return Boolean(meaningfulText(item));
                return Object.values(item).some((value) => {
                    if (Array.isArray(value)) return meaningfulList(value).length > 0;
                    if (value && typeof value === 'object') return true;
                    return Boolean(meaningfulText(value));
                });
            });
    }

    function renderPartialResearchState(researchStatus, requestId) {
        const checked = meaningfulList(researchStatus?.checked_areas || []);
        const found = meaningfulList(researchStatus?.sources_found || []);
        const open = meaningfulList(researchStatus?.open_fields || []);
        const rid = requestId ? `<p class="text-xs text-slate-500 mt-3">request_id: ${escapeHtml(requestId)}</p>` : '';
        return `<div class="yr-partial-research rounded-3xl border border-amber-400/35 bg-amber-400/10 p-5 md:p-6 text-right">
            <div class="yr-section-kicker">מחקר חלקי</div>
            <h3 class="text-2xl font-black text-white mb-2">המחקר לא מספיק לתוצאה מלאה</h3>
            <p class="text-sm leading-7 text-slate-300 mb-4">מוצגים רק פריטים שנמצאו להם מקורות. שדות פתוחים נשארים מחוץ לכרטיסי התוצאה במקום להתמלא בטקסט גנרי.</p>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
                ${checked.length ? `<div><h4 class="font-bold text-white mb-2">נבדק</h4><ul class="space-y-1 text-slate-300">${checked.map(x => `<li>• ${escapeHtml(x)}</li>`).join('')}</ul></div>` : ''}
                ${found.length ? `<div><h4 class="font-bold text-white mb-2">מקורות שנמצאו</h4><ul class="space-y-1 text-slate-300">${found.map(x => `<li>• ${escapeHtml(typeof x === 'string' ? x : (x.title || x.url || x.domain || 'מקור'))}</li>`).join('')}</ul></div>` : ''}
                ${open.length ? `<div><h4 class="font-bold text-white mb-2">עדיין פתוח</h4><ul class="space-y-1 text-slate-300">${open.map(x => `<li>• ${escapeHtml(typeof x === 'string' ? x : [x.field, x.missing_source_type, x.why_open].filter(Boolean).join(' — '))}</li>`).join('')}</ul></div>` : ''}
            </div>${rid}
        </div>`;
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
        showAnalyzeError(`${message}${suffix}`, { type: 'request_error' });
    }

    function normalizeInfoReview(data) {
        const asObject = (value) => (value && typeof value === 'object' ? value : {});
        const report = asObject(data?.reliability_report);
        const checklist = asObject(data?.what_must_be_checked_before_a_decision || report.what_must_be_checked_before_a_decision);
        const missingInfo = Array.isArray(data?.missing_critical_info) ? meaningfulList(data.missing_critical_info) : [];
        const verificationFocus = Array.isArray(data?.verification_focus) ? meaningfulList(data.verification_focus) : [];
        const riskAreas = Array.isArray(data?.key_risk_areas_to_examine || report.key_risk_areas_to_examine)
            ? (data?.key_risk_areas_to_examine || report.key_risk_areas_to_examine).filter(Boolean)
            : [];
        const knownUncertainties = Array.isArray(data?.known_uncertainties || report.known_uncertainties)
            ? (data?.known_uncertainties || report.known_uncertainties).filter(Boolean)
            : [];
        const estimatedCostSensitivity = Array.isArray(data?.estimated_cost_sensitivity || report.estimated_cost_sensitivity)
            ? (data?.estimated_cost_sensitivity || report.estimated_cost_sensitivity).filter(Boolean)
            : [];
        const basedOnAvailableInformation = meaningfulText(
            data?.based_on_available_information || report.based_on_available_information || ''
        );
        const researchStatus = (data?.research_status && typeof data.research_status === 'object') ? data.research_status : {};
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
            dataQualityLabel: data?.data_quality_label || null,
            decisionReadiness: data?.decision_readiness || 'נדרש אימות נוסף',
            missingInfo,
            verificationFocus,
            riskAreas,
            knownUncertainties,
            estimatedCostSensitivity,
            basedOnAvailableInformation,
            checksToVerify,
            sources: meaningfulList(data?.sources),
            researchStatus,
        };
    }

    function buildDataQualityIndicator(data, infoReview) {
        const QUALITY_LEVELS = {
            'חסרה':  { bars: 1, colorClass: 'bg-orange-500' },
            'חלקית': { bars: 3, colorClass: 'bg-amber-400' },
            'טובה':  { bars: 5, colorClass: 'bg-emerald-500' },
        };
        const READINESS_STYLES = {
            'חסר מידע קריטי':        'bg-red-950/40 border-red-700/60 text-red-300',
            'נדרש אימות נוסף':       'bg-amber-950/40 border-amber-700/60 text-amber-300',
            'מוכן לבדיקה מקצועית':  'bg-emerald-950/40 border-emerald-700/60 text-emerald-300',
        };

        const label = infoReview.dataQualityLabel || '';
        const level = QUALITY_LEVELS[label] || null;
        const filledBars = level ? level.bars : 0;
        const barColor = level ? level.colorClass : 'bg-slate-600';
        const isFallback = !label;

        const sourceCount = typeof data.source_count === 'number' ? data.source_count : 0;
        const sourceScopeLabel = data.source_scope_label || '';
        const weaklySourced = data.weakly_sourced === true;
        const decisionReadiness = infoReview.decisionReadiness || '';

        const wrapper = document.createElement('div');
        wrapper.className = 'w-full rounded-3xl border border-slate-700/70 bg-slate-900/40 p-5 md:p-6 text-right';

        // Meter element with ARIA attributes
        const meter = document.createElement('div');
        meter.setAttribute('role', 'meter');
        meter.setAttribute('aria-valuenow', String(filledBars));
        meter.setAttribute('aria-valuemin', '0');
        meter.setAttribute('aria-valuemax', '5');
        meter.setAttribute('aria-label', 'איכות המידע הזמין על הרכב');
        if (isFallback) {
            meter.setAttribute('aria-busy', 'true');
        }

        // 5-bar track
        const barTrack = document.createElement('div');
        barTrack.className = 'flex gap-1.5';
        barTrack.setAttribute('aria-hidden', 'true');
        for (let i = 0; i < 5; i++) {
            const bar = document.createElement('div');
            bar.className = 'h-3 flex-1 rounded-full ' + (i < filledBars ? barColor : 'bg-slate-700');
            barTrack.appendChild(bar);
        }
        meter.appendChild(barTrack);

        // Quality label
        const qualityLabel = document.createElement('div');
        qualityLabel.className = 'mt-2 text-lg font-black text-white';
        qualityLabel.textContent = isFallback
            ? 'איכות המחקר דורשת אימות נוסף'
            : `איכות המידע הזמין על הרכב הזה: ${label}`;
        meter.appendChild(qualityLabel);

        // Sub-label
        const subLabel = document.createElement('div');
        subLabel.className = 'mt-1 text-xs text-slate-400';
        subLabel.textContent = 'זה לא ציון על הרכב – זה ציון על כמות המידע שיש לנו עליו';
        meter.appendChild(subLabel);

        wrapper.appendChild(meter);

        // Source + warning chips
        const chips = [];
        if (sourceCount > 0 && sourceScopeLabel && sourceScopeLabel !== 'לא זוהה') {
            const hasIsraeli = sourceScopeLabel.includes('ישראליים');
            const hasGlobal = sourceScopeLabel.includes('גלובליים');
            if (hasIsraeli && hasGlobal) {
                chips.push({ icon: '🇮🇱', text: 'מקורות ישראליים', dt: 'מקורות ישראליים' });
                chips.push({ icon: '📚', text: 'מקורות גלובליים', dt: 'מקורות גלובליים' });
            } else if (hasIsraeli) {
                chips.push({ icon: '🇮🇱', text: sourceCount + ' מקורות ישראליים', dt: 'מקורות ישראליים' });
            } else if (hasGlobal) {
                chips.push({ icon: '📚', text: sourceCount + ' מקורות גלובליים', dt: 'מקורות גלובליים' });
            }
        } else if (sourceCount > 0) {
            chips.push({ icon: '📚', text: sourceCount + ' מקורות', dt: 'מספר מקורות' });
        }
        if (weaklySourced) {
            chips.push({ icon: '⚠️', text: 'מבוסס על מקורות חלשים', dt: 'אזהרת מקורות', isWarning: true });
        }

        if (chips.length) {
            const dl = document.createElement('dl');
            dl.className = 'flex flex-wrap gap-2 mt-4';
            chips.forEach(function(chip) {
                const div = document.createElement('div');
                div.className = 'inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-semibold border ' +
                    (chip.isWarning
                        ? 'bg-orange-950/40 border-orange-700/60 text-orange-300'
                        : 'bg-slate-800 border-slate-700 text-slate-200');
                const dt = document.createElement('dt');
                dt.className = 'sr-only';
                dt.textContent = chip.dt;
                const dd = document.createElement('dd');
                dd.className = 'flex items-center gap-1';
                dd.textContent = chip.icon + ' ' + chip.text;
                div.appendChild(dt);
                div.appendChild(dd);
                dl.appendChild(div);
            });
            wrapper.appendChild(dl);
        }

        // Decision readiness badge
        if (decisionReadiness) {
            const badgeStyle = READINESS_STYLES[decisionReadiness] || 'bg-slate-800 border-slate-700 text-slate-300';
            const badge = document.createElement('div');
            badge.className = 'mt-3 inline-block px-4 py-1.5 rounded-full text-sm font-bold border ' + badgeStyle;
            badge.textContent = decisionReadiness;
            wrapper.appendChild(badge);
        }

        // System disclaimer (prominent)
        const disclaimer = document.createElement('div');
        disclaimer.className = 'mt-4 rounded-xl bg-slate-800/60 border border-slate-600/60 px-4 py-3 text-sm font-semibold text-slate-200';
        disclaimer.textContent = 'המערכת לא קובעת אם לקנות את הרכב, אלא מציפה מה לבדוק.';
        wrapper.appendChild(disclaimer);

        // Verification focus list
        if (infoReview.verificationFocus.length) {
            const focusTitle = document.createElement('div');
            focusTitle.className = 'mt-4 text-xs font-semibold text-slate-400';
            focusTitle.textContent = 'מוקדי אימות מרכזיים';
            wrapper.appendChild(focusTitle);
            const focusList = document.createElement('ul');
            focusList.className = 'mt-2 list-disc list-inside space-y-1 text-sm text-slate-200';
            infoReview.verificationFocus.slice(0, 4).forEach(function(item) {
                const li = document.createElement('li');
                li.textContent = item;
                focusList.appendChild(li);
            });
            wrapper.appendChild(focusList);
        }

        const sourceTag = data.source_tag || '';
        const mileageNote = data.mileage_note || '';

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

        return wrapper;
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
        if (!ensureReliabilityResultAcknowledgement(options)) return;
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
    const reliabilityResultAckModal = document.getElementById('reliabilityResultAckModal');
    const reliabilityResultAckCheckbox = document.getElementById('reliabilityResultAckCheckbox');
    const reliabilityResultAckConfirm = document.getElementById('reliabilityResultAckConfirm');
    const reliabilityResultAckCancel = document.getElementById('reliabilityResultAckCancel');
    const reliabilityResultAckError = document.getElementById('reliabilityResultAckError');
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
    const isAuthenticated = window.__IS_AUTHENTICATED__ === true;
    let reliabilityResultAckAccepted = !isAuthenticated || RESULT_ACK_DATA.acknowledged === true;
    let pendingResultOpenOptions = null;
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

                        if (elapsedMs > p75_ms) {
                            // Overtime: pulsing animation on ring
                            progressRing.classList.add('ring-overtime');
                            progressRing.style.stroke = 'hsl(30, 90%, 55%)';
                            if (statusTextEl) statusTextEl.textContent = 'ממשיך לעבד... (חורג מהזמן המשוער)';
                        } else {
                            progressRing.classList.remove('ring-overtime');
                            // Rainbow hue cycling (0-360 degrees over p75_ms)
                            const hue = (elapsedMs / p75_ms) * 360;
                            progressRing.style.stroke = `hsl(${hue % 360}, 80%, 60%)`;
                        }
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
                    if (elapsedMs > 20000) {
                        progressRing.classList.add('ring-overtime');
                        progressRing.style.stroke = 'hsl(30, 90%, 55%)';
                        if (statusTextEl) statusTextEl.textContent = 'ממשיך לעבד... (חורג מהזמן המשוער)';
                    } else {
                        progressRing.classList.remove('ring-overtime');
                        const hue = (elapsedMs / 20000) * 360;
                        progressRing.style.stroke = `hsl(${hue % 360}, 80%, 60%)`;
                    }
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

    // Fuel/transmission Hebrew label maps
    const FUEL_LABEL_MAP = {
        'petrol': 'בנזין',
        'diesel': 'דיזל',
        'hybrid': 'היברידי',
        'mild_hybrid': 'מיקרו-היברידי / mild hybrid',
        'plug_in_hybrid': 'פלאג-אין',
        'electric': 'חשמלי',
    };
    const TRANS_LABEL_MAP = {
        'automatic': 'אוטומטית',
        'manual': 'ידנית',
        'robotic': 'רובוטית כפולת מצמדים',
        'dual_clutch': 'רובוטית כפולת מצמדים',
        'dct': 'רובוטית כפולת מצמדים',
        'cvt': 'רציפה',
        'single_speed': 'הילוך יחיד',
    };

    const variantSelect = document.getElementById('variant');
    const variantSection = document.getElementById('variant-section');
    const variantIdInput = document.getElementById('variant_id');
    const variantSummary = document.getElementById('variant-summary');

    function buildModelMap() {
        // New catalog format: CAR_DATA = { make: { model: { year_start, year_end, variants } } }
    }

    function populateModelsForMake(make) {
        modelSelect.innerHTML = '';
        yearSelect.innerHTML = '';
        yearSelect.disabled = true;
        resetVariantSection();

        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = '-- בחר דגם --';
        modelSelect.appendChild(placeholder);

        const models = CAR_DATA[make];
        if (!models || typeof models !== 'object') {
            modelSelect.disabled = true;
            return;
        }

        const modelNames = Object.keys(models).sort();
        modelNames.forEach(name => {
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            modelSelect.appendChild(opt);
        });

        modelSelect.disabled = modelNames.length === 0;
    }

    function populateYearsForModel(make, modelName) {
        yearSelect.innerHTML = '';
        resetVariantSection();
        const models = CAR_DATA[make];
        const info = models && models[modelName];
        const nowYear = new Date().getFullYear();
        let from = nowYear - 20;
        let to = nowYear + 1;

        if (info) {
            if (info.year_start) from = info.year_start;
            if (info.year_end != null) to = info.year_end;
        }

        for (let y = to; y >= from; y--) {
            const opt = document.createElement('option');
            opt.value = String(y);
            opt.textContent = String(y);
            yearSelect.appendChild(opt);
        }
        yearSelect.disabled = false;
    }

    function resetVariantSection() {
        if (variantSection) variantSection.classList.add('hidden');
        if (variantSelect) variantSelect.innerHTML = '<option value="">-- בחר גרסה --</option>';
        if (variantIdInput) variantIdInput.value = '';
        if (variantSummary) { variantSummary.classList.add('hidden'); variantSummary.innerHTML = ''; }
    }

    function populateVariantsForYear(make, modelName, year) {
        resetVariantSection();
        const models = CAR_DATA[make];
        const info = models && models[modelName];
        if (!info || !info.variants) return;

        const y = parseInt(year, 10);
        const filtered = info.variants.filter(v => {
            const vs = v.year_start || 0;
            const ve = v.year_end || 9999;
            return y >= vs && y <= ve;
        });

        if (filtered.length === 0) return;

        variantSection.classList.remove('hidden');
        filtered.forEach(v => {
            const opt = document.createElement('option');
            opt.value = v.variant_id;
            opt.textContent = v.label;
            opt.dataset.fuel = v.fuel_type || '';
            opt.dataset.trans = v.transmission || '';
            opt.dataset.body = v.body_type || '';
            opt.dataset.engine = v.engine || '';
            opt.dataset.hp = v.horsepower_hp || '';
            opt.dataset.drivetrain = v.drivetrain || '';
            opt.dataset.trim = v.version_or_trim || '';
            variantSelect.appendChild(opt);
        });
    }

    function onVariantChange() {
        const sel = variantSelect.options[variantSelect.selectedIndex];
        if (!sel || !sel.value) {
            if (variantIdInput) variantIdInput.value = '';
            if (variantSummary) { variantSummary.classList.add('hidden'); variantSummary.innerHTML = ''; }
            return;
        }
        variantIdInput.value = sel.value;

        // Auto-fill fuel_type and transmission
        const fuelSelect = document.getElementById('fuel_type');
        const transSelect = document.getElementById('transmission');
        if (fuelSelect && sel.dataset.fuel) {
            const heLabel = FUEL_LABEL_MAP[sel.dataset.fuel.toLowerCase()] || '';
            if (heLabel) setSelectByText(fuelSelect, heLabel);
        }
        if (transSelect && sel.dataset.trans) {
            const heLabel = normalizeTransmissionForSelect(sel.dataset.trans);
            if (heLabel) setSelectByText(transSelect, heLabel);
        }

        // Show variant summary
        const parts = [];
        parts.push('גרסה שנבחרה');
        if (sel.dataset.fuel) parts.push('דלק: ' + (FUEL_LABEL_MAP[sel.dataset.fuel.toLowerCase()] || sel.dataset.fuel));
        if (sel.dataset.engine) parts.push('מנוע: ' + sel.dataset.engine);
        if (sel.dataset.hp) parts.push('הספק: ' + sel.dataset.hp + ' כ״ס');
        if (sel.dataset.trans) parts.push('גיר: ' + (normalizeTransmissionForSelect(sel.dataset.trans) || sel.dataset.trans));
        if (sel.dataset.body) parts.push('מרכב: ' + sel.dataset.body);
        if (sel.dataset.drivetrain) parts.push('הנעה: ' + sel.dataset.drivetrain);
        if (sel.dataset.trim) parts.push('גרסה: ' + sel.dataset.trim);
        if (parts.length && variantSummary) {
            variantSummary.innerHTML = parts.map(p => '<div>' + escapeHtml(p) + '</div>').join('');
            variantSummary.classList.remove('hidden');
        }
    }

    function normalizeTransmissionForSelect(value) {
        const raw = String(value || '').toLowerCase().replace(/-/g, '_').trim();
        if (!raw) return '';
        if (raw.includes('dual_clutch') || raw.includes('dct')) return 'רובוטית כפולת מצמדים';
        if (raw.includes('cvt')) return 'רציפה';
        if (raw.includes('manual')) return 'ידנית';
        if (raw.includes('single_speed')) return 'הילוך יחיד';
        if (raw.includes('automatic')) return 'אוטומטית';
        return TRANS_LABEL_MAP[raw] || '';
    }

    function setSelectByText(selectEl, text) {
        for (let i = 0; i < selectEl.options.length; i++) {
            if (selectEl.options[i].textContent.trim() === text) {
                selectEl.selectedIndex = i;
                return;
            }
        }
    }

    function setSubmitting(isSubmitting) {
        if (!submitBtn) return;
        const spinner = submitBtn.querySelector('.spinner');
        const textSpan = submitBtn.querySelector('.button-text');

        submitBtn.disabled = isSubmitting;
        submitBtn.setAttribute('aria-busy', isSubmitting ? 'true' : 'false');
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
            scoreContainer.appendChild(buildDataQualityIndicator(data, infoReview));
        }

        if (summarySimpleEl) {
            summarySimpleEl.textContent = infoReview.basedOnAvailableInformation;
            summarySimpleEl.closest('section')?.classList.toggle('hidden', !infoReview.basedOnAvailableInformation);
        }
        if (summaryDetailedEl) {
            const detailLines = [
                `מצב הבדיקה: ${infoReview.decisionReadiness}`,
                infoReview.missingInfo.length ? `מידע קריטי חסר: ${infoReview.missingInfo.join(' • ')}` : '',
                infoReview.verificationFocus.length ? `מוקדי אימות: ${infoReview.verificationFocus.join(' • ')}` : '',
            ].filter(Boolean);
            summaryDetailedEl.textContent = detailLines.join('\n');
        }
        if (summaryDetailedBlock && !summaryDetailedBlock.classList.contains('hidden')) {
            // להשאיר פתוח אם המשתמש כבר פתח
        }

        if (faultsContainer) {
            const arr = infoReview.riskAreas;
            let html = '';
            if (arr.length) {
                html += '<div class="yr-section-kicker">תחומי סיכון עיקריים</div>';
                html += '<ul class="list-disc list-inside space-y-1 text-sm text-slate-200">';
                html += arr.map(item => {
                    if (item && typeof item === 'object') {
                        return `<li><span class="font-semibold">${safe(item.risk_area || '')}</span>${item.why_to_check ? ` – ${safe(item.why_to_check)}` : ''}</li>`;
                    }
                    return `<li>${safe(item)}</li>`;
                }).join('');
                html += '</ul>';
            } else {

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
                html += '<h5 class="text-sm font-semibold text-slate-300 mt-4 mb-1">מה לבדוק לפני קנייה</h5>';
                html += '<ul class="list-disc list-inside space-y-1 text-sm text-slate-300">';
                html += recommendedChecks.map(item => `<li>${safe(item)}</li>`).join('');
                html += '</ul>';
            }

            faultsContainer.innerHTML = html;
            faultsContainer.classList.toggle('hidden', !html.trim());
        }

        if (costsContainer) {
            const list = infoReview.estimatedCostSensitivity;
            let html = '';
            if (list.length) {
                html += '<div class="yr-section-kicker">רגישות עלויות</div>';
                html += '<ul class="list-disc list-inside space-y-1 text-sm text-slate-200">';
                html += list.map(item => `<li>${safe(item)}</li>`).join('');
                html += '</ul>';
            } else {

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
            costsContainer.classList.toggle('hidden', !html.trim());
        }

        if (competitorsContainer) {
            const vp = (data.vehicle_profile && typeof data.vehicle_profile === 'object') ? data.vehicle_profile : null;
            const vpCompetitors = (vp && Array.isArray(vp.competitors)) ? vp.competitors.filter(Boolean).slice(0, 5) : [];
            let html = '<div class="yr-section-kicker">מתחרים רלוונטיים</div>';

            if (vpCompetitors.length) {
                html += '<p class="text-sm text-slate-400 mb-4">חלופות ישראליות מאותו אזור שימוש/תקציב כאשר המידע זמין. זו אינה השוואה מלאה.</p>';
                html += '<div class="yr-competitor-grid">';
                html += vpCompetitors.map(comp => {
                    const model = safe(meaningfulText(comp.model_name || comp.model || comp.name));
                    const why = safe(meaningfulText(comp.why_relevant || comp.why_consider || comp.reason));
                    const adv = safe(meaningfulText(comp.advantage_vs_reviewed_vehicle || comp.advantage_vs_current || comp.key_advantage));
                    const dis = safe(meaningfulText(comp.disadvantage_or_risk_vs_reviewed_vehicle || comp.disadvantage_vs_current || comp.key_risk));
                    const bestFor = safe(meaningfulText(comp.better_for || comp.best_for || comp.who_should_choose));
                    if (!model || !why) return '';
                    const weak = (comp.confidence === 'low');
                    return `<article class="yr-competitor-card">
                        <div class="flex items-start justify-between gap-3 mb-3">
                            <h4 class="font-black text-white text-lg">${model}</h4>
                            ${weak ? '<span class="yr-mini-badge yr-mini-badge--warn">דורש בדיקה</span>' : ''}
                        </div>
                        <dl class="space-y-2 text-sm leading-6">
                            <div><dt>למה רלוונטי</dt><dd>${why}</dd></div>
                            ${adv ? `<div><dt>יתרון מול הנסקר</dt><dd>${adv}</dd></div>` : ''}
                            ${dis ? `<div><dt>חיסרון/סיכון</dt><dd>${dis}</dd></div>` : ''}
                            ${bestFor ? `<div><dt>למי עדיף</dt><dd>${bestFor}</dd></div>` : ''}
                        </dl>
                    </article>`;
                }).join('');
                html += '</div>';
            } else {
                html = '';
            }

            competitorsContainer.innerHTML = html;
            competitorsContainer.classList.toggle('hidden', !html.trim());
        }

        // Vehicle Profile Card (Single Vehicle Intelligence Card)
        const vpContainer = document.getElementById('vehicle-profile-container');
        if (vpContainer) {
            const vp = (data.vehicle_profile && typeof data.vehicle_profile === 'object') ? data.vehicle_profile : null;
            if (!vp) {
                vpContainer.innerHTML = renderPartialResearchState(infoReview.researchStatus || {}, data.request_id);
            } else {
                let vpHtml = '';

                // 1. Vehicle Identity
                const vi = vp.vehicle_identity || {};
                const marketStatus = vi.israel_market_status;
                const marketStatusMap = {
                    'sold_new': { label: 'נמכר חדש בישראל', cls: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/40' },
                    'sold_used_only': { label: 'יד שנייה בלבד', cls: 'bg-amber-500/20 text-amber-200 border-amber-500/40' },
                    'parallel_import': { label: 'יבוא מקביל', cls: 'bg-blue-500/20 text-blue-200 border-blue-500/40' },
                    'discontinued_in_israel': { label: 'הופסק בישראל', cls: 'bg-red-500/20 text-red-200 border-red-500/40' },
                    'unclear': { label: 'סטטוס לא ברור', cls: 'bg-slate-500/20 text-slate-300 border-slate-500/40' },
                };
                const statusInfo = marketStatus ? marketStatusMap[marketStatus] : null;
                vpHtml += `<div class="mb-6 pb-6 border-b border-slate-700/50">
                    <h3 class="text-lg font-bold text-white mb-2 flex items-center gap-2">
                        כרטיס זהות טכנית
                        ${statusInfo ? `<span class="text-[10px] px-2 py-0.5 rounded-full border ${statusInfo.cls}">${statusInfo.label}</span>` : ''}
                    </h3>
                    <div class="grid grid-cols-2 gap-2 text-sm">
                        ${vi.make ? `<div><span class="text-slate-400">יצרן: </span><span class="text-white">${safe(vi.make)}</span></div>` : ''}
                        ${vi.model ? `<div><span class="text-slate-400">דגם: </span><span class="text-white">${safe(vi.model)}</span></div>` : ''}
                        ${vi.year ? `<div><span class="text-slate-400">שנה: </span><span class="text-white">${safe(vi.year)}</span></div>` : ''}
                        ${vi.generation ? `<div><span class="text-slate-400">דור: </span><span class="text-white">${safe(vi.generation)}</span></div>` : ''}
                        ${vi.body_type ? `<div><span class="text-slate-400">סוג: </span><span class="text-white">${safe(vi.body_type)}</span></div>` : ''}
                        ${vi.segment ? `<div><span class="text-slate-400">סגמנט: </span><span class="text-white">${safe(vi.segment)}</span></div>` : ''}
                    </div>
                </div>`;

                // 2. Pricing
                const pricing = vp.pricing_israel || {};
                const hasPricing = pricing.new_price_range_ils || pricing.used_price_range_ils;
                if (hasPricing) {
                    vpHtml += `<div class="mb-6 pb-6 border-b border-slate-700/50">
                        <h3 class="text-lg font-bold text-white mb-2">מחירים בישראל</h3>
                        <div class="space-y-1 text-sm">
                            ${pricing.new_price_range_ils ? `<div><span class="text-slate-400">מחיר חדש: </span><span class="text-white">${safe(pricing.new_price_range_ils)}</span></div>` : ''}
                            ${pricing.used_price_range_ils ? `<div><span class="text-slate-400">מחיר יד-2: </span><span class="text-white">${safe(pricing.used_price_range_ils)}</span></div>` : ''}
                            ${(pricing.price_notes || []).map(n => `<div class="text-slate-400 text-xs">${safe(n)}</div>`).join('')}
                        </div>
                    </div>`;
                }

                // 3. License Fee
                const lf = vp.license_fee_israel || {};
                vpHtml += `<div class="mb-6 pb-6 border-b border-slate-700/50">
                    <h3 class="text-lg font-bold text-white mb-2">אגרת רישוי</h3>
                    ${lf.method === 'unknown' ?
                        '<p class="text-sm text-slate-400">אגרה רשמית לא נמצאה בפרסומי משרד התחבורה / היבואן.</p>' :
                        `<div class="text-sm">${lf.annual_fee_ils ? `<div><span class="text-slate-400">אגרה שנתית: </span><span class="text-white font-semibold">${safe(String(lf.annual_fee_ils))} ₪</span></div>` : '<div class="text-slate-400">לא נמצאה</div>'}
                        ${(lf.notes || []).map(n => `<div class="text-slate-400 text-xs">${safe(n)}</div>`).join('')}</div>`
                    }
                </div>`;

                // 4. Trim Levels
                const trims = vp.trim_levels_israel || [];
                vpHtml += `<div class="mb-6 pb-6 border-b border-slate-700/50">
                    <h3 class="text-lg font-bold text-white mb-2">רמות גימור בישראל</h3>
                    ${trims.length === 0 ? '<p class="text-sm text-slate-400">מידע על גימורים ספציפיים לא זוהה.</p>' :
                        `<div class="space-y-3">${trims.map(t => `
                            <div class="bg-slate-900/40 border border-slate-700/70 rounded-xl px-3 py-3">
                                <div class="font-semibold text-white mb-1">${safe(t.trim_name || '')}${t.price_ils ? ` – <span class="text-primary">${safe(String(t.price_ils))} ₪</span>` : ''}</div>
                                ${t.powertrain ? `<div class="text-xs text-slate-300">${safe(t.powertrain)}</div>` : ''}
                            </div>`).join('')}</div>`
                    }
                </div>`;

                // 5. Recommended Trim
                const rt = vp.recommended_trim || {};
                if (rt.trim_name || rt.reason) {
                    vpHtml += `<div class="mb-6 pb-6 border-b border-slate-700/50">
                        <h3 class="text-lg font-bold text-white mb-2 flex items-center gap-2">
                            גימור מומלץ
                            ${rt.confidence === 'low' ? '<span class="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-200 border border-amber-500/40">אינדיקציה בלבד</span>' : ''}
                        </h3>
                        <div class="text-sm">
                            ${rt.trim_name ? `<div class="font-semibold text-white mb-1">${safe(rt.trim_name)}</div>` : ''}
                            ${rt.reason ? `<div class="text-slate-300">${safe(rt.reason)}</div>` : ''}
                        </div>
                    </div>`;
                }

                // 6. Official Safety
                const safety = vp.official_safety || {};
                vpHtml += `<div class="mb-6 pb-6 border-b border-slate-700/50">
                    <h3 class="text-lg font-bold text-white mb-2">בטיחות רשמית</h3>
                    ${(!safety.rating || !safety.organization || safety.organization === 'unknown') ?
                        '<p class="text-sm text-slate-400">לא נמצא ציון בטיחות במקור רשמי.</p>' :
                        `<div class="text-sm space-y-1">
                            ${safety.rating ? `<div><span class="text-slate-400">דירוג: </span><span class="text-white font-semibold">${safe(safety.rating)}</span></div>` : ''}
                            ${safety.organization ? `<div><span class="text-slate-400">ארגון: </span><span class="text-white">${safe(safety.organization)}</span></div>` : ''}
                            ${safety.test_year ? `<div><span class="text-slate-400">שנת בדיקה: </span><span class="text-white">${safe(String(safety.test_year))}</span></div>` : ''}
                            ${safety.adult_score ? `<div><span class="text-slate-400">מבוגרים: </span><span class="text-white">${safe(safety.adult_score)}</span></div>` : ''}
                            ${safety.child_score ? `<div><span class="text-slate-400">ילדים: </span><span class="text-white">${safe(safety.child_score)}</span></div>` : ''}
                        </div>`
                    }
                </div>`;

                // 7. Warranty
                const warranty = vp.warranty_israel || {};
                if (warranty.vehicle_warranty || warranty.battery_warranty) {
                    vpHtml += `<div class="mb-6 pb-6 border-b border-slate-700/50">
                        <h3 class="text-lg font-bold text-white mb-2">אחריות</h3>
                        <div class="text-sm space-y-1">
                            ${warranty.vehicle_warranty ? `<div><span class="text-slate-400">אחריות רכב: </span><span class="text-white">${safe(warranty.vehicle_warranty)}</span></div>` : ''}
                            ${warranty.battery_warranty ? `<div><span class="text-slate-400">אחריות סוללה: </span><span class="text-white">${safe(warranty.battery_warranty)}</span></div>` : ''}
                            ${(warranty.importer_notes || []).map(n => `<div class="text-slate-400 text-xs">${safe(n)}</div>`).join('')}
                        </div>
                    </div>`;
                }

                // 8. Recalls Israel
                const recalls = vp.recalls_israel || {};
                if (recalls.checked_against_official_source) {
                    vpHtml += `<div class="mb-6 pb-6 border-b border-slate-700/50">
                        <h3 class="text-lg font-bold text-white mb-2">Recalls (ישראל)</h3>
                        ${(!recalls.known_recalls || recalls.known_recalls.length === 0) ?
                            '<p class="text-sm text-slate-400">לא נמצאו recalls רשומים מול המקור הרשמי.</p>' :
                            `<ul class="space-y-2">${recalls.known_recalls.map(r => `
                                <li class="bg-slate-900/40 border border-slate-700/70 rounded-xl px-3 py-2 text-sm">
                                    ${r.year ? `<span class="text-slate-400">${safe(String(r.year))}: </span>` : ''}
                                    <span class="text-white">${safe(r.issue || '')}</span>
                                    ${r.source ? `<a href="${sanitizeUrl(safe(r.source))}" target="_blank" rel="noopener noreferrer" class="text-primary hover:underline text-xs mr-2">מקור</a>` : ''}
                                </li>`).join('')}</ul>`
                        }
                    </div>`;
                }

                // 9. Ownership Cost Notes
                const oc = vp.ownership_cost_notes || {};
                const costLevelMap = { 'low': 'נמוך', 'medium': 'בינוני', 'high': 'גבוה' };
                vpHtml += `<div class="mb-6 pb-6 border-b border-slate-700/50">
                    <h3 class="text-lg font-bold text-white mb-2">עלויות אחזקה</h3>
                    <div class="grid grid-cols-2 gap-2 text-sm">
                        <div><span class="text-slate-400">תחזוקה: </span><span class="text-white">${costLevelMap[oc.maintenance_cost_pressure] || ''}</span></div>
                        <div><span class="text-slate-400">ביטוח: </span><span class="text-white">${costLevelMap[oc.insurance_cost_pressure] || ''}</span></div>
                        <div><span class="text-slate-400">פחת: </span><span class="text-white">${costLevelMap[oc.depreciation_risk] || ''}</span></div>
                        <div><span class="text-slate-400">חלפים: </span><span class="text-white">${costLevelMap[oc.parts_availability] || ''}</span></div>
                    </div>
                    ${(oc.notes || []).length ? `<div class="mt-2 space-y-1">${(oc.notes || []).map(n => `<div class="text-slate-400 text-xs">${safe(n)}</div>`).join('')}</div>` : ''}
                </div>`;

                // 10. Best for / Not ideal for
                const bestFor = vp.best_for || [];
                const notIdeal = vp.not_ideal_for || [];
                if (bestFor.length || notIdeal.length) {
                    vpHtml += `<div class="mb-6 pb-6 border-b border-slate-700/50">
                        <h3 class="text-lg font-bold text-white mb-2">למי הרכב מתאים?</h3>
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            ${bestFor.length ? `<div>
                                <h4 class="text-sm font-semibold text-emerald-300 mb-1">מתאים ל:</h4>
                                <ul class="list-disc list-inside text-sm text-slate-200 space-y-1">${bestFor.map(x => `<li>${safe(x)}</li>`).join('')}</ul>
                            </div>` : ''}
                            ${notIdeal.length ? `<div>
                                <h4 class="text-sm font-semibold text-amber-300 mb-1">פחות מתאים ל:</h4>
                                <ul class="list-disc list-inside text-sm text-slate-200 space-y-1">${notIdeal.map(x => `<li>${safe(x)}</li>`).join('')}</ul>
                            </div>` : ''}
                        </div>
                    </div>`;
                }

                // 11. Buyer Summary
                const buyerSummary = vp.buyer_summary;
                if (buyerSummary) {
                    vpHtml += `<div class="mb-4">
                        <h3 class="text-lg font-bold text-white mb-2">סיכום פרקטי</h3>
                        <p class="text-slate-200 leading-relaxed text-sm">${safe(buyerSummary)}</p>
                    </div>`;
                }

                vpContainer.innerHTML = vpHtml;
            }
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
            const groups = [
                ['נקודות בדיקה מכניות', checklist.mechanical_inspection_points || []],
                ['מסמכים לאימות', checklist.documents_to_verify || []],
                ['שאלות למוכר', checklist.questions_to_ask_seller || []],
                ['דגלים אדומים', checklist.red_flags_to_look_for || []],
                ['אי-ודאויות שדורשות אימות', infoReview.knownUncertainties || []],
            ].map(([title, items]) => [title, Array.isArray(items) ? items.filter(Boolean) : []])
             .filter(([, items]) => items.length);
            let html = '<div class="yr-section-kicker">מה לבדוק לפני קנייה</div>';
            if (!groups.length) {

            } else {
                html += '<div class="space-y-4">' + groups.map(([title, items]) => `
                    <div>
                        <h4 class="text-sm font-semibold text-white mb-2">${safe(title)}</h4>
                        <ul class="list-disc list-inside text-sm text-slate-200 space-y-1">
                            ${items.map(x => `<li>${safe(x)}</li>`).join('')}
                        </ul>
                    </div>
                `).join('') + '</div>';
            }
            reportContainer.innerHTML = html;
            reportContainer.classList.toggle('hidden', !groups.length);
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
            legalError.classList.add('flex');
            legalError.scrollIntoView({ behavior: 'smooth', block: 'center' });
            return false;
        }
        legalError.classList.remove('flex');
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
        // Catalog variant fields
        const vid = (document.getElementById('variant_id') || {}).value || '';
        if (vid) payload.variant_id = vid;
        const vSel = document.getElementById('variant');
        if (vSel && vSel.selectedIndex > 0) {
            const opt = vSel.options[vSel.selectedIndex];
            if (opt.dataset.trim) payload.version_or_trim = opt.dataset.trim;
            if (opt.dataset.body) payload.body_type = opt.dataset.body;
            if (opt.dataset.fuel) payload.catalog_fuel_type = opt.dataset.fuel;
            if (opt.dataset.engine) payload.catalog_engine = opt.dataset.engine;
            if (opt.dataset.hp) payload.catalog_horsepower_hp = opt.dataset.hp;
            if (opt.dataset.trans) payload.catalog_transmission = opt.dataset.trans;
            if (opt.dataset.drivetrain) payload.catalog_drivetrain = opt.dataset.drivetrain;
        }
        return payload;
    }

    function closeReliabilityResultAckModal() {
        reliabilityResultAckModal?.classList.add('hidden');
        reliabilityResultAckError?.classList.add('hidden');
    }

    function ensureReliabilityResultAcknowledgement(options = {}) {
        if (!isAuthenticated || reliabilityResultAckAccepted) return true;
        pendingResultOpenOptions = options;
        reliabilityResultAckModal?.classList.remove('hidden');
        return false;
    }

    async function confirmReliabilityResultAcknowledgement() {
        if (!reliabilityResultAckCheckbox?.checked) {
            reliabilityResultAckError?.classList.remove('hidden');
            return;
        }
        reliabilityResultAckError?.classList.add('hidden');

        const featureKey = RESULT_ACK_DATA.feature_key || '';
        const featureVersion = RESULT_ACK_DATA.version || '';
        if (!featureKey || !featureVersion) {
            reliabilityResultAckAccepted = true;
            closeReliabilityResultAckModal();
            if (pendingResultOpenOptions) {
                const options = pendingResultOpenOptions;
                pendingResultOpenOptions = null;
                openReliabilityResult(options);
            }
            return;
        }

        const res = await safeFetchJson('/api/legal/accept', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                legal_confirm: true,
                feature_consents: [{ feature_key: featureKey, version: featureVersion }],
            })
        });
        if (!(res && res.ok)) {
            const message = (res && res.error && res.error.message) || 'לא הצלחנו לשמור אישור כרגע.';
            if (reliabilityResultAckError) reliabilityResultAckError.textContent = message;
            reliabilityResultAckError?.classList.remove('hidden');
            return;
        }
        reliabilityResultAckAccepted = true;
        closeReliabilityResultAckModal();
        if (pendingResultOpenOptions) {
            const options = pendingResultOpenOptions;
            pendingResultOpenOptions = null;
            openReliabilityResult(options);
        }
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
                    resetVariantSection();
                }
            });
        }

        if (yearSelect) {
            yearSelect.addEventListener('change', () => {
                const make = makeSelect ? makeSelect.value : '';
                const model = modelSelect ? modelSelect.value : '';
                const year = yearSelect.value;
                if (make && model && year) {
                    populateVariantsForYear(make, model, year);
                } else {
                    resetVariantSection();
                }
            });
        }

        if (variantSelect) {
            variantSelect.addEventListener('change', onVariantChange);
        }

        if (form) {
            form.addEventListener('submit', handleSubmit);
        }

        openResultButton?.addEventListener('click', function () {
            openReliabilityResult({ userInitiated: true });
        });
        reliabilityResultAckCancel?.addEventListener('click', function () {
            pendingResultOpenOptions = null;
            closeReliabilityResultAckModal();
        });
        reliabilityResultAckConfirm?.addEventListener('click', confirmReliabilityResultAcknowledgement);
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
            resultDiv.innerHTML = '<p class="text-red-400 text-sm p-3 bg-red-950/30 border border-red-900/50 rounded-lg">נא לבחור שני חיפושים להשוואה</p>';
            resultDiv.classList.remove('hidden');
            return;
        }

        if (id1 === id2) {
            resultDiv.innerHTML = '<p class="text-red-400 text-sm p-3 bg-red-950/30 border border-red-900/50 rounded-lg">נא לבחור שני חיפושים שונים</p>';
            resultDiv.classList.remove('hidden');
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
                resultDiv.innerHTML = '<p class="text-red-400 text-sm p-3 bg-red-950/30 border border-red-900/50 rounded-lg">שגיאה בטעינת נתוני ההשוואה</p>';
                resultDiv.classList.remove('hidden');
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
            resultDiv.innerHTML = '<p class="text-red-400 text-sm p-3 bg-red-950/30 border border-red-900/50 rounded-lg">שגיאה בהשוואה</p>';
            resultDiv.classList.remove('hidden');
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
