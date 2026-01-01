(() => {
    const form = document.getElementById('car-form');
    if (!form) return;

    // ---------------------------
    // Security helpers (XSS + CSRF)
    // ---------------------------
    function escapeHtml(str) {
        return String(str ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
    }

    function sanitizeObject(value) {
        if (value == null) return value;
        if (Array.isArray(value)) return value.map(sanitizeObject);
        if (typeof value === 'object') {
            const out = {};
            for (const [k, v] of Object.entries(value)) out[k] = sanitizeObject(v);
            return out;
        }
        if (typeof value === 'string') return escapeHtml(value);
        return value;
    }

    let _csrfToken = null;
    async function getCsrfToken() {
        if (_csrfToken) return _csrfToken;
        const res = await fetch('/api/csrf', { credentials: 'same-origin' });
        const data = await res.json();
        _csrfToken = data.csrf_token || '';
        return _csrfToken;
    }

    function showError(msg) {
        const errorBox = document.getElementById('error-box');
        if (!errorBox) return alert(msg);
        errorBox.textContent = msg;
        errorBox.classList.remove('hidden');
    }

    function hideError() {
        const errorBox = document.getElementById('error-box');
        if (!errorBox) return;
        errorBox.textContent = '';
        errorBox.classList.add('hidden');
    }

    function setLoading(on) {
        const btn = document.getElementById('submit-btn');
        if (btn) btn.disabled = !!on;
        const loader = document.getElementById('loading');
        if (loader) loader.classList.toggle('hidden', !on);
    }

    function renderResult(dataRaw) {
        const allowing = sanitizeObject(dataRaw || {});
        const resultBox = document.getElementById('result-box');
        if (!resultBox) return;

        // Example: show score + summaries safely
        const score = allowing.base_score_calculated ?? '';
        const summary = allowing.reliability_summary ?? '';
        const simple = allowing.reliability_summary_simple ?? '';
        const mileageNote = allowing.mileage_note ?? '';
        const sourceTag = allowing.source_tag ?? '';

        const issues = Array.isArray(allowing.common_issues) ? allowing.common_issues : [];
        const competitors = Array.isArray(allowing.common_competitors_brief) ? allowing.common_competitors_brief : [];
        const checks = Array.isArray(allowing.recommended_checks) ? allowing.recommended_checks : [];
        const sources = Array.isArray(allowing.sources) ? allowing.sources : [];

        // IMPORTANT: we build HTML, but everything inside is already escaped by sanitizeObject()
        resultBox.innerHTML = `
            <div class="space-y-3">
                ${sourceTag ? `<div class="text-xs text-slate-400">${sourceTag}</div>` : ''}

                <div class="flex items-center gap-3">
                    <div class="text-2xl font-bold">${escapeHtml(score)}</div>
                    <div class="text-sm text-slate-300">ציון אמינות (0–100)</div>
                </div>

                ${mileageNote ? `<div class="text-sm text-amber-300">${mileageNote}</div>` : ''}

                ${summary ? `
                    <div class="bg-slate-900/60 border border-slate-800 rounded-xl p-3">
                        <div class="text-sm font-semibold mb-1">סיכום מקצועי</div>
                        <div class="text-sm leading-relaxed">${summary}</div>
                    </div>
                ` : ''}

                ${simple ? `
                    <div class="bg-slate-900/60 border border-slate-800 rounded-xl p-3">
                        <div class="text-sm font-semibold mb-1">הסבר פשוט</div>
                        <div class="text-sm leading-relaxed">${simple}</div>
                    </div>
                ` : ''}

                ${issues.length ? `
                    <div class="bg-slate-900/60 border border-slate-800 rounded-xl p-3">
                        <div class="text-sm font-semibold mb-1">תקלות נפוצות</div>
                        <ul class="list-disc pr-6 text-sm space-y-1">
                            ${issues.map(x => `<li>${x}</li>`).join('')}
                        </ul>
                    </div>
                ` : ''}

                ${checks.length ? `
                    <div class="bg-slate-900/60 border border-slate-800 rounded-xl p-3">
                        <div class="text-sm font-semibold mb-1">בדיקות מומלצות</div>
                        <ul class="list-disc pr-6 text-sm space-y-1">
                            ${checks.map(x => `<li>${x}</li>`).join('')}
                        </ul>
                    </div>
                ` : ''}

                ${competitors.length ? `
                    <div class="bg-slate-900/60 border border-slate-800 rounded-xl p-3">
                        <div class="text-sm font-semibold mb-1">מתחרים נפוצים</div>
                        <ul class="list-disc pr-6 text-sm space-y-1">
                            ${competitors.map(c => `<li><b>${c.model || ''}</b>: ${c.brief_summary || ''}</li>`).join('')}
                        </ul>
                    </div>
                ` : ''}

                ${sources.length ? `
                    <div class="text-xs text-slate-400">
                        <div class="font-semibold mb-1">מקורות</div>
                        <ul class="list-disc pr-6 space-y-1">
                            ${sources.map(s => `<li>${s}</li>`).join('')}
                        </ul>
                    </div>
                ` : ''}
            </div>
        `;
        resultBox.classList.remove('hidden');
        resultBox.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    async function handleSubmit(e) {
        e.preventDefault();
        hideError();
        setLoading(true);

        try {
            const make = document.getElementById('make')?.value || '';
            const model = document.getElementById('model')?.value || '';
            const sub_model = document.getElementById('sub_model')?.value || '';
            const year = document.getElementById('year')?.value || '';
            const mileage_range = document.getElementById('mileage_range')?.value || '';
            const fuel_type = document.getElementById('fuel_type')?.value || '';
            const transmission = document.getElementById('transmission')?.value || '';

            const payload = { make, model, sub_model, year, mileage_range, fuel_type, transmission };

            const csrfToken = await getCsrfToken();

            const res = await fetch('/analyze', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify(payload)
            });

            const data = await res.json().catch(() => ({}));
            if (!res.ok || data.error) {
                showError(data.error || 'שגיאת שרת');
                return;
            }

            renderResult(data);
        } catch (err) {
            console.error(err);
            showError('שגיאה כללית בחיבור לשרת. נסה שוב מאוחר יותר.');
        } finally {
            setLoading(false);
        }
    }

    form.addEventListener('submit', handleSubmit);
})();
