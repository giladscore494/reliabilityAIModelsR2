// /static/recommendations.js
// לוגיקת צד לקוח למנוע ההמלצות (Car Advisor / Gemini 3)
// XSS Protection: All AI-generated content is HTML-escaped on the backend via sanitization.py
// before being sent to the frontend. Template literals are safe to use with innerHTML.

(function () {
    const form = document.getElementById('advisor-form');
    const submitBtn = document.getElementById('advisor-submit');
    const resultsSection = document.getElementById('advisor-results');
    const queriesEl = document.getElementById('advisor-search-queries');
    const tableWrapper = document.getElementById('advisor-table-wrapper');
    const errorEl = document.getElementById('advisor-error');
    const consentCheckbox = document.getElementById('advisor-consent');
    let legalAccepted = false;

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
                badge: 'המלצה ראשית',
                car: bestFit,
                chip: bestFit.fit_score != null ? `${Math.round(bestFit.fit_score)}% Fit` : '',
                text: 'מבוסס על כל הפרמטרים שהזנת: תקציב, שימוש, משפחה והעדפות. זה הדגם שהכי מתאים לפרופיל הכולל שלך.'
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
                chip: relScore != null ? `ציון אמינות ${safeNum(relScore, 1)}` : '',
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
        const reliabilityScore = car.reliability_score != null ? safeNum(car.reliability_score, 1) : '';
        const reliabilityGrade = getReliabilityGrade(
            car.reliability_score != null ? Number(car.reliability_score) : null
        );
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

        const comparisonComment = car.comparison_comment || '';
        const notRecommendedReason = car.not_recommended_reason || '';

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
        const safeReliabilityScore = h(reliabilityScore || '-');
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
                        ${reliabilityScore !== '' ? `
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
                                <th class="px-2 py-1 font-semibold text-slate-300">ציון אמינות (1–10)</th>
                                <td class="px-2 py-1 text-slate-100">${safeReliabilityScore}</td>
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

                ${comparisonComment ? `
                    <div class="mt-2 text-[11px] md:text-xs text-slate-300 leading-relaxed">
                        <span class="font-semibold text-slate-100">הסבר כללי:</span>
                        <br>${safeComparisonComment}
                    </div>
                ` : ''}

                ${notRecommendedReason ? `
                    <div class="mt-2 text-[11px] md:text-xs text-red-300 leading-relaxed border border-red-500/40 bg-red-900/20 rounded-xl px-3 py-2">
                        <span class="font-semibold">סיבה לאי-המלצה/הסתייגות:</span>
                        <br>${safeNotRecommendedReason}
                    </div>
                ` : ''}
            </article>
        `;
    }

    // --- תצוגת תוצאות מלאה (כרטיסיות + טבלאות) ---
    function renderResults(data) {
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
            resultsSection.classList.remove('hidden');
            resultsSection.scrollIntoView({behavior: 'smooth', block: 'start'});
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
                לכל רכב מוצגת כרטיסייה נפרדת עם כל הפרמטרים, כולל השיטות שבהן חושבו הנתונים.
            </div>
            <div class="space-y-4">
                ${cardsHtml}
            </div>
        `;

        resultsSection.classList.remove('hidden');
        resultsSection.scrollIntoView({behavior: 'smooth', block: 'start'});
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

        const payload = { ...buildPayload(), legal_confirm: true };

        if (!payload.budget_max || payload.budget_max <= 0 || payload.budget_min > payload.budget_max) {
            if (errorEl) {
                errorEl.textContent =
                    'בדוק שהתקציב המינימלי קטן מהתקציב המקסימלי ושערכי התקציב תקינים.';
                errorEl.classList.remove('hidden');
            }
            return;
        }

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
            renderResults(payloadFromApi);
        } catch (err) {
            console.error(err);
            showRequestAwareError('שגיאה כללית בחיבור לשרת. נסה שוב מאוחר יותר.', null);
        } finally {
            hideTimingBanner(true);
            setSubmitting(false);
        }
    }

    form.addEventListener('submit', handleSubmit);
})();
