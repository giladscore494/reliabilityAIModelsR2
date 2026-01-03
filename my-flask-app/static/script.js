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
            return {
                ok: false,
                error: {
                    code: (errObj && errObj.code) || 'HTTP_ERROR',
                    message: (errObj && errObj.message) || response.statusText || 'שגיאה בבקשה',
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

    const summarySimpleEl = document.getElementById('summary-simple-text');
    const summaryDetailedEl = document.getElementById('summary-detailed-text');
    const summaryToggleBtn = document.getElementById('summary-toggle-btn');
    const summaryDetailedBlock = document.getElementById('summary-detailed-block');
    const scoreContainer = document.getElementById('reliability-score-container');
    const sourcesListEl = document.getElementById('sources-list');
    const sourcesBlockEl = document.getElementById('sources-block');
    const reportContainer = document.getElementById('report');
    const microContainer = document.getElementById('micro-container');
    const microAdjusted = document.getElementById('micro-adjusted-score');
    const microDelta = document.getElementById('micro-delta');
    const microRisks = document.getElementById('micro-risks');
    const microActions = document.getElementById('micro-actions');
    const timelineContainer = document.getElementById('timeline-container');
    const timelinePhases = document.getElementById('timeline-phases');
    const timelineKm = document.getElementById('timeline-km');
    const simContainer = document.getElementById('simulator-container');
    const simOutput = document.getElementById('sim-output');
    const simAnnualInput = document.getElementById('sim-annual-km');
    const simAnnualLabel = document.getElementById('sim-annual-km-label');
    const simCityInput = document.getElementById('sim-city-pct');
    const simCityLabel = document.getElementById('sim-city-pct-label');
    const simKeepInput = document.getElementById('sim-keep-years');
    const simKeepLabel = document.getElementById('sim-keep-years-label');
    const simDriverSelect = document.getElementById('sim-driver-style');

    const faultsContainer = document.getElementById('faults');
    const costsContainer = document.getElementById('costs');
    const competitorsContainer = document.getElementById('competitors');
    // All innerHTML below interpolates values passed through escapeHtml() to prevent XSS.

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

    function renderMicroReliability(micro) {
        if (!microContainer) return;
        if (!micro) {
            microContainer.classList.add('hidden');
            return;
        }
        microContainer.classList.remove('hidden');
        if (microAdjusted) microAdjusted.textContent = micro.adjusted_score ?? '';
        if (microDelta) {
            const delta = micro.delta || 0;
            microDelta.textContent = delta === 0 ? 'ללא שינוי' : `${delta > 0 ? '+' : ''}${delta} נק׳ מהבסיס`;
            microDelta.className = 'text-sm ' + (delta >= 0 ? 'text-green-400' : 'text-amber-300');
        }
        if (microRisks) {
            const risks = Array.isArray(micro.top_risks) ? micro.top_risks : [];
            microRisks.innerHTML = risks.slice(0, 4).map(r => `
                <div class="bg-slate-800/60 border border-slate-700/70 rounded-xl px-3 py-2">
                    <div class="flex items-center justify-between text-xs text-slate-300">
                        <span class="font-semibold">${escapeHtml(r.subsystem || '')}</span>
                        <span class="px-2 py-0.5 rounded-full border border-slate-600 text-[11px]">${escapeHtml(r.level || '')}</span>
                    </div>
                    <p class="text-[12px] text-slate-400 mt-1">${escapeHtml(r.why || '')}</p>
                    <p class="text-[11px] text-slate-300 mt-1">${escapeHtml(r.mitigation || '')}</p>
                </div>
            `).join('');
        }
        if (microActions) {
            const actions = Array.isArray(micro.quick_actions) ? micro.quick_actions : [];
            microActions.innerHTML = actions.map(a => `<li>${escapeHtml(a)}</li>`).join('');
        }
    }

    function renderTimeline(plan) {
        if (!timelineContainer) return;
        if (!plan) {
            timelineContainer.classList.add('hidden');
            return;
        }
        timelineContainer.classList.remove('hidden');
        if (timelineKm) {
            const proj = plan.projected_km || {};
            timelineKm.textContent = `נוכחי ${proj.current || 0} → 36ח׳ ${proj.m36 || ''} ק״מ`;
        }
        if (timelinePhases) {
            const phases = Array.isArray(plan.phases) ? plan.phases : [];
            timelinePhases.innerHTML = phases.map(ph => {
                const actions = Array.isArray(ph.actions) ? ph.actions : [];
                return `
                    <div class="bg-slate-800/60 border border-slate-700/70 rounded-xl p-3">
                        <div class="flex items-center justify-between mb-2">
                            <div class="font-semibold text-slate-100">${escapeHtml(ph.title || '')}</div>
                            <div class="text-[11px] text-slate-400">${(ph.month_range||[]).join('-')} ח׳</div>
                        </div>
                        <div class="space-y-2 text-xs text-slate-200">
                            ${actions.map(a => `
                                <div class="border border-slate-700/60 rounded-lg px-2 py-1">
                                    <div class="flex items-center justify-between">
                                        <span class="font-semibold">${escapeHtml(a.name || '')}</span>
                                        <span class="text-[11px] text-slate-400">${escapeHtml(a.subsystem || '')}</span>
                                    </div>
                                    <div class="text-[11px] text-slate-400">${escapeHtml(a.reason || '')}</div>
                                    <div class="text-[11px] text-slate-300 mt-0.5">עלות: ${escapeHtml((a.cost_ils||[]).join('–'))} ₪</div>
                                </div>
                            `).join('') || '<div class="text-slate-500 text-xs">אין פעולות מתוכננות</div>'}
                        </div>
                    </div>
                `;
            }).join('');
        }
    }

    function computeSim(simModel, sliders) {
        const defaults = simModel.defaults || {};
        const buckets = simModel.cost_buckets || {};
        const annual = Number(sliders.annual_km ?? defaults.annual_km ?? 15000);
        const city = Number(sliders.city_pct ?? defaults.city_pct ?? 50);
        const years = Number(sliders.keep_years ?? defaults.keep_years ?? 3);
        const driverStyle = sliders.driver_style || defaults.driver_style || 'normal';

        const maintKm = buckets.maintenance_per_km_ils || [0, 0];
        const baseMin = (maintKm[0] || 0) * annual * years;
        const baseMax = (maintKm[1] || 0) * annual * years;
        const risk = buckets.risk_repairs_yearly_ils || [0, 0];
        const tiresKm = buckets.tires_per_km_ils || [0, 0];

        const cityMult = buckets.brakes_city_multiplier || 1;
        const heatMult = buckets.heat_ac_multiplier || 1;
        const driverMult = driverStyle === 'aggressive' ? 1.2 : (driverStyle === 'calm' ? 0.9 : 1);

        const tiresMin = (tiresKm[0] || 0) * annual * years * driverMult;
        const tiresMax = (tiresKm[1] || 0) * annual * years * driverMult;

        const riskMin = (risk[0] || 0) * years * heatMult;
        const riskMax = (risk[1] || 0) * years * heatMult * cityMult;

        const totalMin = Math.round(baseMin + tiresMin + riskMin);
        const totalMax = Math.round(baseMax + tiresMax + riskMax);

        return {
            total_min: totalMin,
            total_max: totalMax,
            breakdown: {
                maintenance: [Math.round(baseMin), Math.round(baseMax)],
                tires: [Math.round(tiresMin), Math.round(tiresMax)],
                risk_repairs: [Math.round(riskMin), Math.round(riskMax)],
            }
        };
    }

    function renderSimulator(simModel) {
        if (!simContainer) return;
        if (!simModel) {
            simContainer.classList.add('hidden');
            return;
        }
        simContainer.classList.remove('hidden');
        const defaults = simModel.defaults || {};

        const syncLabels = () => {
            if (simAnnualLabel) simAnnualLabel.textContent = simAnnualInput ? simAnnualInput.value : defaults.annual_km;
            if (simCityLabel) simCityLabel.textContent = simCityInput ? simCityInput.value : defaults.city_pct;
            if (simKeepLabel) simKeepLabel.textContent = simKeepInput ? simKeepInput.value : defaults.keep_years;
        };

        if (simAnnualInput) simAnnualInput.value = defaults.annual_km || 15000;
        if (simCityInput) simCityInput.value = defaults.city_pct || 50;
        if (simKeepInput) simKeepInput.value = defaults.keep_years || 3;
        if (simDriverSelect) simDriverSelect.value = defaults.driver_style || 'normal';
        syncLabels();

        const update = () => {
            syncLabels();
            const sliders = {
                annual_km: simAnnualInput ? simAnnualInput.value : defaults.annual_km,
                city_pct: simCityInput ? simCityInput.value : defaults.city_pct,
                keep_years: simKeepInput ? simKeepInput.value : defaults.keep_years,
                driver_style: simDriverSelect ? simDriverSelect.value : defaults.driver_style,
            };
            const calc = computeSim(simModel, sliders);
            if (simOutput) {
                simOutput.innerHTML = `
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                        <div class="bg-slate-800/70 rounded-xl p-3">
                            <div class="text-xs text-slate-400">סה\"כ מינימום</div>
                            <div class="text-2xl font-bold text-green-400">${calc.total_min.toLocaleString()} ₪</div>
                        </div>
                        <div class="bg-slate-800/70 rounded-xl p-3">
                            <div class="text-xs text-slate-400">סה\"כ מקסימום</div>
                            <div class="text-2xl font-bold text-amber-300">${calc.total_max.toLocaleString()} ₪</div>
                        </div>
                        <div class="bg-slate-800/70 rounded-xl p-3 text-xs text-slate-300 space-y-1">
                            <div>תחזוקה: ${calc.breakdown.maintenance[0].toLocaleString()}–${calc.breakdown.maintenance[1].toLocaleString()} ₪</div>
                            <div>צמיגים/בלמים: ${calc.breakdown.tires[0].toLocaleString()}–${calc.breakdown.tires[1].toLocaleString()} ₪</div>
                            <div>סיכוני תקלות: ${calc.breakdown.risk_repairs[0].toLocaleString()}–${calc.breakdown.risk_repairs[1].toLocaleString()} ₪</div>
                        </div>
                    </div>
                `;
            }
        };

        [simAnnualInput, simCityInput, simKeepInput].forEach(inp => {
            if (!inp) return;
            inp.addEventListener('input', update);
        });
        if (simDriverSelect) simDriverSelect.addEventListener('change', update);
        update();
    }

    function renderResults(data) {
        if (!resultsContainer) return;

        if (data && data.ok === false) {
            alert(data.message || data.error || 'שגיאת מודל: פלט לא תקין.');
            return;
        }

        resultsContainer.classList.remove('hidden');
        const safe = (v) => escapeHtml(v);

        renderMicroReliability(data.micro_reliability);
        renderTimeline(data.timeline_plan);
        renderSimulator(data.sim_model);

        // ציון
        if (scoreContainer) {
            scoreContainer.innerHTML = '';
            const baseRaw = data.base_score_calculated;
            let baseNum = null;
            if (baseRaw !== undefined && baseRaw !== null) {
                const m = String(baseRaw).match(/-?\d+(\.\d+)?/);
                if (m) baseNum = parseFloat(m[0]);
            }

            let gradient = 'linear-gradient(135deg, #f97373, #b91c1c)'; // נמוך
            if (baseNum !== null) {
                if (baseNum >= 80) gradient = 'linear-gradient(135deg, #22c55e, #15803d)';
                else if (baseNum >= 60) gradient = 'linear-gradient(135deg, #fbbf24, #d97706)';
            }

            const sourceTag = data.source_tag || '';
            const mileageNote = data.mileage_note || '';

            const wrapper = document.createElement('div');
            wrapper.className = 'flex flex-col md:flex-row items-center md:items-center md:justify-center gap-6 mb-4';

            const circle = document.createElement('div');
            circle.className = 'score-circle';
            circle.style.backgroundImage = gradient;

            const scoreText = document.createElement('div');
            scoreText.className = 'text-4xl md:text-5xl font-black leading-none';
            scoreText.textContent = baseNum !== null ? String(Math.round(baseNum)) : '?';

            const label = document.createElement('div');
            label.className = 'mt-1 text-xs font-semibold tracking-wide uppercase text-white/80';
            label.textContent = 'ציון אמינות';

            circle.appendChild(scoreText);
            circle.appendChild(label);

            const side = document.createElement('div');
            side.className = 'text-xs md:text-sm text-slate-300 space-y-2 max-w-md';

            if (sourceTag) {
                const p = document.createElement('p');
                p.textContent = sourceTag;
                p.className = 'text-[11px] text-slate-500';
                side.appendChild(p);
            }

            if (mileageNote) {
                const p = document.createElement('p');
                p.textContent = mileageNote;
                p.className = 'text-xs bg-amber-950/40 text-amber-300 border border-amber-700/60 rounded-lg px-3 py-2';
                side.appendChild(p);
            }

            wrapper.appendChild(circle);
            wrapper.appendChild(side);
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

        const payload = collectFormData();
        if (!payload.make || !payload.model || !payload.year) {
            alert('נא למלא יצרן, דגם ושנתון.');
            return;
        }

        setSubmitting(true);
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
    });
})();
