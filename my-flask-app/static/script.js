// /static/script.js
// לוגיקת צד לקוח לטופס בדיקת אמינות + הצגת תוצאות

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

    const faultsContainer = document.getElementById('faults');
    const costsContainer = document.getElementById('costs');
    const competitorsContainer = document.getElementById('competitors');

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

        resultsContainer.classList.remove('hidden');

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
                html += arr.map(x => `<li>${x}</li>`).join('');
                html += '</ul>';
            } else {
                html += '<p class="text-sm text-slate-400">לא דווחו תקלות נפוצות ספציפיות לדגם הזה בקילומטראז׳ הנתון.</p>';
            }
            if (checks.length) {
                html += '<h4 class="mt-4 text-sm font-semibold text-white">בדיקות מומלצות לפני קניה</h4>';
                html += '<ul class="list-disc list-inside space-y-1 text-sm text-slate-200">';
                html += checks.map(x => `<li>${x}</li>`).join('');
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
                html += `<p class="text-sm text-slate-300 mb-3">עלות תיקון ממוצעת משוערת: <span class="font-semibold">${avg} ₪</span></p>`;
            }
            if (list.length) {
                html += '<div class="space-y-2">';
                html += list.map(row => {
                    const issue = row.issue || '';
                    const cost = row.avg_cost_ILS || '';
                    const severity = row.severity || '';
                    const src = row.source || '';
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
                        <span class="font-semibold">${c.model || ''}</span>
                        <span class="text-slate-300"> – ${c.brief_summary || ''}</span>
                    </li>
                `).join('');
                html += '</ul>';
            } else {
                html += '<p class="text-sm text-slate-400">לא הוגדרו מתחרים ספציפיים לדגם זה.</p>';
            }
            competitorsContainer.innerHTML = html;
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
            const res = await fetch('/analyze', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (!res.ok || data.error) {
                alert(data.error || 'שגיאה בשרת');
                return;
            }
            renderResults(data);
        } catch (err) {
            console.error(err);
            alert('שגיאה כללית בשליחת הבקשה');
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
