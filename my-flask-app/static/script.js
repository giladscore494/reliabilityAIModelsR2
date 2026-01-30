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

    const makeSelect = document.getElementById('make');
    const modelSelect = document.getElementById('model');
    const yearSelect = document.getElementById('year');
    const form = document.getElementById('car-form');
    const submitBtn = document.getElementById('submit-button');
    const resultsContainer = document.getElementById('results-container');
    const legalCheckbox = document.getElementById('legal-confirm');
    const legalError = document.getElementById('legal-error');
    let legalAccepted = false;

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

    function getReliabilityLevel(score) {
        const numericScore = Number(score);
        if (score === null || score === undefined || Number.isNaN(numericScore)) {
            return {
                label: 'לא ידוע',
                gradient: 'linear-gradient(135deg, #64748b, #475569)',
                badgeClass: 'bg-slate-500/20 text-slate-200 border-slate-500/40'
            };
        }
        if (numericScore >= 80) {
            return {
                label: 'גבוה',
                gradient: 'linear-gradient(135deg, #22c55e, #15803d)',
                badgeClass: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/40'
            };
        }
        if (numericScore >= 60) {
            return {
                label: 'בינוני',
                gradient: 'linear-gradient(135deg, #fbbf24, #d97706)',
                badgeClass: 'bg-amber-500/20 text-amber-200 border-amber-500/40'
            };
        }
        return {
            label: 'נמוך',
            gradient: 'linear-gradient(135deg, #f97373, #b91c1c)',
            badgeClass: 'bg-red-500/20 text-red-200 border-red-500/40'
        };
    }

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


    function renderResults(data) {
        if (!resultsContainer) return;

        if (data && data.ok === false) {
            alert(data.message || data.error || 'שגיאת מודל: פלט לא תקין.');
            return;
        }

        resultsContainer.classList.remove('hidden');
        const safe = (v) => escapeHtml(v);

        // אמינות מוערכת (קטגורית בלבד)
        if (scoreContainer) {
            scoreContainer.innerHTML = '';
            const estimated = data.estimated_reliability || '';
            const sourceTag = data.source_tag || '';
            const mileageNote = data.mileage_note || '';

            const wrapper = document.createElement('div');
            wrapper.className = 'flex flex-col gap-2 mb-4';

            const headline = document.createElement('div');
            headline.className = 'text-xl md:text-2xl font-bold text-white';
            headline.textContent = estimated ? `אמינות מוערכת: ${estimated}` : 'אמינות מוערכת: לא ידוע';
            wrapper.appendChild(headline);

            const subtitle = document.createElement('div');
            subtitle.className = 'text-xs text-slate-400';
            subtitle.textContent = 'מבוסס על ניתוח AI';
            wrapper.appendChild(subtitle);

            if (sourceTag) {
                const p = document.createElement('p');
                p.textContent = sourceTag;
                p.className = 'text-[11px] text-slate-500';
                wrapper.appendChild(p);
            }

            if (mileageNote) {
                const p = document.createElement('p');
                p.textContent = mileageNote;
                p.className = 'text-xs bg-amber-950/40 text-amber-300 border border-amber-700/60 rounded-lg px-3 py-2';
                wrapper.appendChild(p);
            }

            scoreContainer.appendChild(wrapper);
        }

        // סיכומים
        if (summarySimpleEl) {
            summarySimpleEl.textContent = (data.reliability_summary_simple || '').trim() || 'אין סיכום פשוט זמין.';
        }
        if (summaryDetailedEl) {
            summaryDetailedEl.textContent = (data.reliability_summary || '').trim() || 'אין סיכום מקצועי זמין.';
        }
        if (summaryDetailedBlock && !summaryDetailedBlock.classList.contains('hidden')) {
            // להשאיר פתוח אם המשתמש כבר פתח
        }

        // תקלות נפוצות
        if (faultsContainer) {
            const arr = Array.isArray(data.common_issues) ? data.common_issues : [];
            const checks = Array.isArray(data.recommended_checks) ? data.recommended_checks : [];
            let html = '';
            if (arr.length) {
                html += '<h4 class="text-base font-semibold text-white mb-2">תקלות נפוצות על פי הנתונים</h4>';
                html += '<ul class="list-disc list-inside space-y-1 text-sm text-slate-200">';
                html += arr.map(x => `<li>${safe(x)}</li>`).join('');
                html += '</ul>';
            } else {
                html += '<p class="text-sm text-slate-400">לא דווחו תקלות נפוצות ספציפיות לדגם הזה בקילומטראז׳ הנתון.</p>';
            }
            if (checks.length) {
                html += '<h4 class="mt-4 text-sm font-semibold text-white">בדיקות מומלצות לפני קניה</h4>';
                html += '<ul class="list-disc list-inside space-y-1 text-sm text-slate-200">';
                html += checks.map(x => `<li>${safe(x)}</li>`).join('');
                html += '</ul>';
            }
            faultsContainer.innerHTML = html;
        }

        // עלויות
        if (costsContainer) {
            const avg = data.avg_repair_cost_ILS;
            const list = Array.isArray(data.issues_with_costs) ? data.issues_with_costs : [];
            let html = '';
            if (avg !== undefined && avg !== null && avg !== '') {
                html += `<p class="text-sm text-slate-300 mb-3">עלות תיקון ממוצעת משוערת: <span class="font-semibold">${safe(avg)} ₪</span></p>`;
            }
            if (list.length) {
                html += '<div class="space-y-2">';
                html += list.map(row => {
                    const issue = safe(row.issue || '');
                    const cost = safe(row.avg_cost_ILS || '');
                    const severity = safe(row.severity || '');
                    const src = safe(row.source || '');
                    return `
                        <div class="flex flex-wrap items-center justify-between gap-2 text-sm bg-slate-900/40 border border-slate-700/70 rounded-xl px-3 py-2">
                            <div class="flex-1">
                                <div class="font-semibold text-slate-100">${issue}</div>
                                <div class="text-[11px] text-slate-400">${src}</div>
                            </div>
                            <div class="flex flex-col items-end text-xs text-slate-200">
                                <span class="font-bold">${cost} ₪</span>
                                <span class="mt-0.5 px-2 py-0.5 rounded-full border border-slate-600 text-[11px]">${severity}</span>
                            </div>
                        </div>
                    `;
                }).join('');
                html += '</div>';
            } else {
                html += '<p class="text-sm text-slate-400">אין פירוט עלויות ספציפי, אך ניתן להניח עלויות תחזוקה ממוצעות בקטגוריה.</p>';
            }
            costsContainer.innerHTML = html;
        }

        // מתחרים
        if (competitorsContainer) {
            const arr = Array.isArray(data.common_competitors_brief) ? data.common_competitors_brief : [];
            let html = '';
            if (arr.length) {
                html += '<p class="text-sm text-slate-300 mb-3">דגמים נוספים שכדאי לבדוק מבחינת אמינות ואופי שימוש דומה:</p>';
                html += '<ul class="space-y-2 text-sm text-slate-200">';
                html += arr.map(c => `
                    <li class="bg-slate-900/40 border border-slate-700/70 rounded-xl px-3 py-2">
                        <span class="font-semibold">${safe(c.model || '')}</span>
                        <span class="text-slate-300"> – ${safe(c.brief_summary || '')}</span>
                    </li>
                `).join('');
                html += '</ul>';
            } else {
                html += '<p class="text-sm text-slate-400">לא הוגדרו מתחרים ספציפיים לדגם זה.</p>';
            }
            competitorsContainer.innerHTML = html;
        }

        // מקורות
        if (sourcesListEl && sourcesBlockEl) {
            const sources = Array.isArray(data.sources) ? data.sources : [];
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
                        li.innerHTML = url ? `<a class="text-primary hover:underline" href="${url}" target="_blank" rel="noopener">${title || url}</a>` : title;
                    } else {
                        li.textContent = safe(src || '');
                    }
                    sourcesListEl.appendChild(li);
                });
            }
        }

        // דוח אמינות
        if (reportContainer) {
            const rep = data.reliability_report || {};
            let html = '';
            if (rep.available === false) {
                html = '<p class="text-sm text-slate-400">דוח אמינות לא זמין לפלט זה (MISSING_OR_INVALID).</p>';
            } else if (Object.keys(rep).length === 0) {
                html = '<p class="text-sm text-slate-400">הדוח לא סופק על ידי המודל.</p>';
            } else {
                const risks = Array.isArray(rep.top_risks) ? rep.top_risks : [];
                const checklist = rep.buyer_checklist || {};
                html += `
                    <div class="space-y-3">
                        <div class="flex flex-wrap items-center gap-3">
                            <div class="text-lg font-bold text-white">ציון דוח: ${safe(rep.overall_score || '')}</div>
                            <span class="px-3 py-1 rounded-full text-xs bg-slate-800 text-slate-200">ביטחון: ${safe(rep.confidence || '')}</span>
                        </div>
                        <p class="text-slate-200 text-sm">${safe(rep.one_sentence_verdict || '')}</p>
                        <div>
                            <h4 class="text-sm font-semibold text-white mb-1">סיכונים מרכזיים</h4>
                            <ul class="list-disc list-inside text-sm text-slate-200 space-y-1">
                                ${risks.slice(0,6).map(r => `<li><span class="font-semibold">${safe(r.risk_title||'')}</span> – ${safe(r.why_it_matters||'')} (${safe(r.severity||'')})</li>`).join('') || '<li class="text-slate-400">אין סיכונים מפורטים.</li>'}
                            </ul>
                        </div>
                        <div>
                            <h4 class="text-sm font-semibold text-white mb-1">מה לבדוק</h4>
                            <ul class="list-disc list-inside text-sm text-slate-200 space-y-1">
                                ${(checklist.ask_seller||[]).map(x=>`<li>${safe(x)}</li>`).join('') || '<li class="text-slate-400">אין נתונים</li>'}
                            </ul>
                        </div>
                        <div>
                            <h4 class="text-sm font-semibold text-white mb-1">שינויים עם ק״מ</h4>
                            <ul class="list-disc list-inside text-sm text-slate-200 space-y-1">
                                ${(rep.what_changes_with_mileage||[]).map(x=>`<li>${safe(x.mileage_band||'')}: ${safe(x.what_to_expect||'')}</li>`).join('') || '<li class="text-slate-400">אין מידע</li>'}
                            </ul>
                        </div>
                    </div>
                `;
            }
            reportContainer.innerHTML = html;
        }

        resultsContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
        if (!validateLegal()) return;
        if (!(await ensureLegalAcceptance())) return;

        const payload = { ...collectFormData(), legal_confirm: true };
        if (!payload.make || !payload.model || !payload.year) {
            alert('נא למלא יצרן, דגם ושנתון.');
            return;
        }

        setSubmitting(true);
        showTimingBanner('analyze');
        
        try {
            const res = await safeFetchJson('/analyze', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                credentials: 'include',
                body: JSON.stringify(payload)
            });

            if (!res || res.ok === false) {
                const code = res && res.error && res.error.code;
                if (code === 'unauthenticated') {
                    alert('נדרשת התחברות. אנא התחבר למערכת.');
                    window.location.href = '/login';
                    return;
                }
                const message = (res && res.error && res.error.message) || 'שגיאת שרת. אנא נסה שוב מאוחר יותר.';
                showRequestAwareError(message, res && res.request_id);
                return;
            }

            const payloadFromApi = res.data || {};
            renderResults(payloadFromApi);
        } catch (err) {
            console.error(err);
            alert('שגיאה כללית בשליחת הבקשה. אנא נסה שוב מאוחר יותר.');
        } finally {
            hideTimingBanner(true);
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
        
        // Load history list on page load if logged in
        if (typeof window.loadHistoryList === 'function') {
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
                'estimated_reliability': 'אמינות מוערכת',
                'avg_repair_cost_ILS': 'עלות תיקון ממוצעת (₪)',
                'engine_transmission_score': 'מנוע ותיבת הילוכים',
                'electrical_score': 'חשמל ואלקטרוניקה',
                'suspension_brakes_score': 'מתלים ובלמים',
                'maintenance_cost_score': 'עלויות תחזוקה',
                'satisfaction_score': 'שביעות רצון',
                'recalls_score': 'זכורות וכשלים'
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
            
            // Compare estimated reliability (categorical)
            const rel1 = item1.result?.estimated_reliability || 'לא ידוע';
            const rel2 = item2.result?.estimated_reliability || 'לא ידוע';

            html += `
                <div class="flex justify-between items-center py-2 border-b border-slate-700/50">
                    <span class="text-slate-300">${labels['estimated_reliability']}</span>
                    <div class="flex gap-4 items-center">
                        <span class="font-bold text-white">${escapeHtml(rel1)}</span>
                        <span class="text-slate-500">←→</span>
                        <span class="font-bold text-white">${escapeHtml(rel2)}</span>
                    </div>
                </div>
            `;

            // Compare breakdown scores if available
            if (item1.result?.score_breakdown && item2.result?.score_breakdown) {
                const breakdown1 = item1.result.score_breakdown;
                const breakdown2 = item2.result.score_breakdown;
                
                Object.keys(labels).forEach(key => {
                    if (key === 'estimated_reliability' || key === 'avg_repair_cost_ILS') return;
                    
                    const val1 = breakdown1[key] || 0;
                    const val2 = breakdown2[key] || 0;
                    const diff = val1 - val2;
                    const diffColor = diff > 0 ? 'text-green-400' : diff < 0 ? 'text-red-400' : 'text-slate-400';
                    
                    html += `
                        <div class="flex justify-between items-center py-2 border-b border-slate-700/50">
                            <span class="text-slate-300 text-sm">${labels[key]}</span>
                            <div class="flex gap-4 items-center">
                                <span class="text-white">${val1}</span>
                                <span class="${diffColor} text-sm">${diff > 0 ? '+' : ''}${diff}</span>
                                <span class="text-white">${val2}</span>
                            </div>
                        </div>
                    `;
                });
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
})();
