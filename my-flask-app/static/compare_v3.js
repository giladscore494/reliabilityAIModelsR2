/* Comparison V3 UI ("comparison-v3/1"): the TRIPY picker cascade with the user's asking price, the
 * personalization step (buyer-profile/3), staged progress and the result table.
 *
 * The table has at most 3 car columns, one section per category that has rows, and a chevron per row that opens
 * that row's explanation. Only data every car has is shown: there is no empty or placeholder cell.
 * Stored V2 / V1 rows keep their own renderer (compare_v2.js), selected by engine_version.
 */
(function (root) {
    'use strict';

    var ENGINE_VERSION = 'comparison-v3/1';
    var SLOTS = ['car_1', 'car_2', 'car_3'];
    var STAGE_LABELS = {
        resolving_vehicles: 'מזהה גרסאות',
        loading_facts: 'טוען את נתוני הרכבים',
        comparing_facts: 'משווה את הנתונים',
        evaluating_decision: 'שוקל את ההבדלים לפי הצרכים שלך',
        writing_explanations: 'מנסח הסברים לשורות',
        writing_summary: 'מנסח את הסיכום'
    };
    var STEP_ORDER = ['resolving_vehicles', 'loading_facts', 'comparing_facts', 'evaluating_decision', 'writing_explanations', 'writing_summary'];
    var PRIORITY_KEYS = ['purchase_price', 'safety', 'performance', 'efficiency_environment', 'practicality'];
    var EV_PRIORITY_KEY = 'ev_convenience';
    var BUDGET_NEEDS_PRICES = 'כדי לבדוק תקציב יש להזין מחיר לכל רכב';
    var PRICE_MIN = 1000, PRICE_MAX = 3000000;

    function escapeHtml(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function isV3Result(result) {
        return !!(result && result.engine_version === ENGINE_VERSION);
    }

    function slotsOf(result) {
        var table = result.table || {};
        return (table.slots || SLOTS.filter(function (s) { return (result.cars || {})[s]; })).slice(0, 3);
    }

    // ==================================================================
    // result: hero + table
    // ==================================================================
    function buildHeroHtml(result) {
        var rec = result.recommendation || {};
        var profile = result.profile_summary || {};
        var html = '<div class="v3-hero" data-engine="' + ENGINE_VERSION + '">';
        html += '<p class="v3-hero-title">' + escapeHtml(rec.title_he || '') + '</p>';
        if (rec.name) html += '<p class="v3-hero-name"><strong>' + escapeHtml(rec.name) + '</strong>' +
            (rec.strength_label_he ? ' <span class="v3-chip">' + escapeHtml(rec.strength_label_he) + '</span>' : '') + '</p>';
        if (rec.subtitle_he) html += '<p class="v3-hero-sub">' + escapeHtml(rec.subtitle_he) + '</p>';
        var chips = (profile.headline || []).map(function (h) { return '<span class="v3-chip">' + escapeHtml(h) + '</span>'; });
        (profile.priorities || []).forEach(function (p) {
            chips.push('<span class="v3-chip v3-chip-muted">' + escapeHtml(p.name_he) + ': <strong>' + escapeHtml(p.label_he) + '</strong></span>');
        });
        if (chips.length) html += '<div class="v3-chips" data-v3-profile-summary>' + chips.join(' ') + '</div>';
        var notes = ((result.hard_constraints || {}).notes || []);
        if (notes.length) {
            html += '<ul class="v3-notes">' + notes.map(function (n) {
                return '<li class="v3-note-' + escapeHtml(n.level) + '">' + escapeHtml(n.text_he) + '</li>';
            }).join('') + '</ul>';
        }
        var reasons = (rec.reasons_he || {});
        if ((reasons['for'] || []).length) {
            html += '<div class="v3-reasons"><p><strong>הסיבות המרכזיות</strong></p><ul>' +
                reasons['for'].map(function (r) { return '<li>' + escapeHtml(r) + '</li>'; }).join('') + '</ul></div>';
        }
        if (result.summary) html += '<p class="v3-summary">' + escapeHtml(result.summary) + '</p>';
        return html + '</div>';
    }

    function headerCellHtml(car) {
        var bits = [car.year, car.trim].filter(function (x) { return x !== undefined && x !== null && x !== ''; });
        var html = '<div class="v3-cell v3-car" role="columnheader"><strong>' + escapeHtml(car.display_name || '') + '</strong>';
        if (bits.length) html += '<span>' + bits.map(escapeHtml).join(' · ') + '</span>';
        if (car.asking_price_text) html += '<span class="v3-price">' + escapeHtml(car.asking_price_text) + '</span>';
        return html + '</div>';
    }

    function rowHtml(row, slots, idx) {
        var panelId = 'v3-exp-' + idx;
        var unit = row.unit_he ? ' <span class="v3-unit">(' + escapeHtml(row.unit_he) + ')</span>' : '';
        var html = '<div class="v3-row" role="row" data-row="' + escapeHtml(row.row_id) + '">';
        html += '<div class="v3-label" role="rowheader"><button type="button" class="v3-chevron" aria-expanded="false" aria-controls="' +
            panelId + '" data-v3-toggle><span class="v3-chevron-icon" aria-hidden="true"></span>' + escapeHtml(row.label_he) + unit + '</button></div>';
        slots.forEach(function (slot) {
            var cell = (row.cells || {})[slot] || {};
            var lead = !!cell.leader && !row.display_only;
            html += '<div class="v3-cell' + (lead ? ' v3-lead' : '') + '" role="cell" data-slot="' + slot + '">' + escapeHtml(cell.text) +
                (lead ? ' <span class="v3-lead-mark" aria-hidden="true">●</span><span class="sr-only">(מוביל בשורה)</span>' : '') + '</div>';
        });
        html += '</div>';
        html += '<div id="' + panelId + '" class="v3-panel" role="region" hidden><p>' + escapeHtml(row.explanation_he || '') + '</p>';
        var details = [];
        slots.forEach(function (slot) {
            ((row.cells || {})[slot] || {}).details && ((row.cells || {})[slot].details || []).forEach(function (d) {
                var range = d.production_range || {};
                var built = (range.from || range.to) ? 'ייצור ' + [range.from, range.to].filter(Boolean).join('–') : '';
                details.push('<li>' + [d.recall_year, d.affected_system, d.fault_description, d.repair_method, built]
                    .filter(function (v) { return v !== undefined && v !== null && v !== ''; }).map(escapeHtml).join(' · ') + '</li>');
            });
        });
        if (details.length) html += '<ul class="v3-details">' + details.join('') + '</ul>';
        return html + '</div>';
    }

    function buildTableHtml(result) {
        var table = result.table || {};
        var slots = slotsOf(result);
        var cars = table.cars || result.cars || {};
        var html = '<div class="v3-table" role="table" aria-label="השוואת הנתונים" data-v3-table style="--v3-cols:' + slots.length + '">';
        html += '<div class="v3-row v3-head" role="row"><div class="v3-label" role="columnheader">נתון</div>' +
            slots.map(function (s) { return headerCellHtml(cars[s] || {}); }).join('') + '</div>';
        var idx = 0;
        (table.sections || []).forEach(function (section) {
            var secId = 'v3-sec-' + escapeHtml(section.key);
            html += '<div class="v3-section" role="rowgroup" data-section="' + escapeHtml(section.key) + '">';
            html += '<div class="v3-section-head" role="row"><div role="cell" class="v3-section-cell"><button type="button" class="v3-chevron v3-section-title" aria-expanded="false" aria-controls="' +
                secId + '" data-v3-toggle><span class="v3-chevron-icon" aria-hidden="true"></span>' + escapeHtml(section.label_he) + '</button>' +
                '<div id="' + secId + '" class="v3-panel" hidden><p>' + escapeHtml(section.influence_he || '') + '</p></div></div></div>';
            (section.rows || []).forEach(function (row) { html += rowHtml(row, slots, idx++); });
            html += '</div>';
        });
        html += '</div>';
        if (table.attribution_he) html += '<p class="v3-attribution">' + escapeHtml(table.attribution_he) + '</p>';
        return html;
    }

    function bindToggles(container) {
        if (!container || container.__v3Bound) return;
        container.__v3Bound = true;
        container.addEventListener('click', function (event) {
            var button = event.target && event.target.closest ? event.target.closest('[data-v3-toggle]') : null;
            if (!button) return;
            var panel = document.getElementById(button.getAttribute('aria-controls'));
            var open = button.getAttribute('aria-expanded') !== 'true';
            button.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (panel) panel.hidden = !open;
        });
    }

    function renderResult(result, els) {
        els = els || {};
        var winner = els.winnerDisplay || (typeof document !== 'undefined' ? document.getElementById('winnerDisplay') : null);
        var cats = els.categoriesSection || (typeof document !== 'undefined' ? document.getElementById('categoriesSection') : null);
        if (winner) winner.innerHTML = buildHeroHtml(result);
        if (cats) {
            cats.innerHTML = buildTableHtml(result);
            if (cats.addEventListener) bindToggles(cats);
        }
        if (typeof document === 'undefined') return;
        ['assumptionsDisplay', 'topReasons', 'compareApiDisclaimers'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.classList.add('hidden');
        });
    }

    // ==================================================================
    // picker (TRIPY cascade through the server) + asking price
    // ==================================================================
    var opts = {};
    var availabilityToken = 0;

    function byId(id) { return document.getElementById(id); }

    function fillSelect(select, items, valueKey, labelFn, placeholder) {
        select.innerHTML = '<option value="">' + escapeHtml(placeholder) + '</option>' + items.map(function (item) {
            return '<option value="' + escapeHtml(item[valueKey]) + '">' + escapeHtml(labelFn(item)) + '</option>';
        }).join('');
        select.disabled = !items.length;
    }

    function resetSelect(select, placeholder) {
        if (!select) return;
        select.innerHTML = '<option value="">' + escapeHtml(placeholder) + '</option>';
        select.disabled = true;
    }

    async function getJson(url) {
        var resp = await fetch(url, { headers: { 'Accept': 'application/json' }, credentials: 'include' });
        var data = await resp.json();
        if (!resp.ok || data.ok === false) throw new Error((data.error && data.error.message) || 'שגיאה');
        return data.data || {};
    }

    function catalogUrl(kind, params) {
        var q = Object.keys(params || {}).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); }).join('&');
        return '/api/compare/v3/catalog/' + kind + (q ? '?' + q : '');
    }

    function showPickerError(n, message) {
        var box = byId('v3_picker_error_' + n);
        if (!box) return;
        box.textContent = message || '';
        box.classList.toggle('hidden', !message);
    }

    function changed() {
        refreshAvailability();
        if (opts.onChange) opts.onChange();
    }

    function initSlot(n, manufacturers) {
        var make = byId('v3_make_' + n), model = byId('v3_model_' + n), year = byId('v3_year_' + n), trim = byId('v3_trim_' + n);
        var price = byId('v3_price_' + n);
        if (!make) return;
        fillSelect(make, manufacturers, 'manufacturer', function (m) { return m.display && m.display !== m.manufacturer ? m.display + ' (' + m.manufacturer + ')' : m.manufacturer; }, 'בחרו יצרן...');
        make.addEventListener('change', async function () {
            resetSelect(model, 'בחרו דגם...'); resetSelect(year, 'בחרו שנה...'); resetSelect(trim, 'בחרו גרסה...');
            changed();
            if (!make.value) return;
            try {
                var data = await getJson(catalogUrl('models', { manufacturer: make.value }));
                fillSelect(model, data.models || [], 'model', function (m) { return m.model; }, 'בחרו דגם...');
                showPickerError(n, '');
            } catch (e) { showPickerError(n, e.message); }
        });
        model.addEventListener('change', async function () {
            resetSelect(year, 'בחרו שנה...'); resetSelect(trim, 'בחרו גרסה...');
            changed();
            if (!model.value) return;
            try {
                var data = await getJson(catalogUrl('years', { manufacturer: make.value, model: model.value }));
                fillSelect(year, data.years || [], 'year', function (y) { return String(y.year); }, 'בחרו שנה...');
            } catch (e) { showPickerError(n, e.message); }
        });
        year.addEventListener('change', async function () {
            resetSelect(trim, 'בחרו גרסה...');
            changed();
            if (!year.value) return;
            try {
                var data = await getJson(catalogUrl('trims', { manufacturer: make.value, model: model.value, year: year.value }));
                fillSelect(trim, data.trims || [], 'variant_identity_key', function (t) { return t.label || t.trim || ''; }, 'בחרו גרסה...');
            } catch (e) { showPickerError(n, e.message); }
        });
        trim.addEventListener('change', changed);
        if (price) price.addEventListener('change', changed);
    }

    function priceValue(n) {
        var el = byId('v3_price_' + n);
        var raw = el ? String(el.value || '').trim() : '';
        if (!raw) return null;
        var num = Number(raw);
        return isFinite(num) ? num : NaN;
    }

    function getSelectedCars() {
        var cars = [];
        for (var n = 1; n <= 3; n++) {
            var trim = byId('v3_trim_' + n);
            if (!trim || !trim.value) continue;
            var car = { variant_identity_key: trim.value };
            var price = priceValue(n);
            if (price !== null && !isNaN(price)) car.asking_price_ils = Math.round(price);
            cars.push(car);
        }
        return cars;
    }

    function setSelected() { /* a stored comparison is shown as stored; the picker is not re-filled */ }

    async function refreshAvailability() {
        var cars = getSelectedCars();
        var token = ++availabilityToken;
        if (cars.length < 2) { applyAvailability(null); return; }
        try {
            var resp = await fetch('/api/compare/v3/availability', {
                method: 'POST', credentials: 'include',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json',
                           'X-CSRF-Token': opts.getCsrfToken ? opts.getCsrfToken() : '' },
                body: JSON.stringify({ cars: cars })
            });
            var data = await resp.json();
            if (token !== availabilityToken) return;
            applyAvailability(resp.ok && data.ok !== false ? data.data : null);
        } catch (e) {
            if (token === availabilityToken) applyAvailability(null);
        }
    }

    // A slider is hidden when its category has no row for the selected cars.
    function applyAvailability(info) {
        var available = info ? (info.available_dimensions || []) : null;
        Array.prototype.forEach.call(document.querySelectorAll('#v3Personalization [data-v3-priority-key]'), function (row) {
            var key = row.getAttribute('data-v3-priority-key');
            var hide = available ? available.indexOf(key) === -1 : key === EV_PRIORITY_KEY;
            row.classList.toggle('hidden', hide);
        });
        var plugin = !!(info && info.plugin_selected);
        Array.prototype.forEach.call(document.querySelectorAll('#v3Personalization [data-ev-only]'), function (el) {
            if (!el.hasAttribute('data-v3-priority-key')) el.classList.toggle('hidden', !plugin);
        });
    }

    // ==================================================================
    // personalization (buyer-profile/3)
    // ==================================================================
    function selectedMode() {
        var checked = document.querySelector('input[name="v3_mode"]:checked');
        return checked ? checked.value : '';
    }

    function numberValue(id) {
        var el = byId(id);
        if (!el || el.disabled || (el.closest && el.closest('.hidden'))) return null;
        var raw = String(el.value || '').trim();
        if (!raw) return null;
        var num = Number(raw);
        return isFinite(num) ? num : NaN;
    }

    function selectValue(id) {
        var el = byId(id);
        return el && el.value ? el.value : null;
    }

    function checkedValues(name) {
        return Array.prototype.map.call(document.querySelectorAll('input[name="' + name + '"]:checked'), function (el) { return el.value; });
    }

    function collectProfile() {
        for (var n = 1; n <= 3; n++) {
            var price = priceValue(n);
            if (price !== null && (isNaN(price) || price < PRICE_MIN || price > PRICE_MAX || Math.round(price) !== price)) {
                return { ok: false, error: 'מחיר מבוקש: מספר שלם בין ₪1,000 ל־₪3,000,000.', focusId: 'v3_price_' + n };
            }
        }
        var mode = selectedMode();
        if (!mode) return { ok: false, error: 'יש לבחור „השוואה מותאמת אליי” או „השוואה כללית”.', focusId: null };
        if (mode === 'general') return { ok: true, profile: { schema: 'buyer-profile/3', mode: 'general' } };
        var profile = { schema: 'buyer-profile/3', mode: 'personalized' };
        profile.main_use = selectValue('v3_main_use');
        if (!profile.main_use) return { ok: false, error: 'יש לבחור את השימוש העיקרי ברכב.', focusId: 'v3_main_use' };
        var numbers = [['annual_km', 'v3_annual_km', 0, 150000], ['regular_passengers', 'v3_regular_passengers', 1, 9],
                       ['budget_max_ils', 'v3_budget_max_ils', 10000, 5000000],
                       ['typical_daily_km', 'v3_typical_daily_km', 0, 1000], ['frequent_long_trip_km', 'v3_frequent_long_trip_km', 0, 3000]];
        var towing = byId('v3_towing_toggle');
        if (towing && towing.checked) numbers.push(['towing_braked_required_kg', 'v3_towing_braked_required_kg', 100, 5000]);
        for (var i = 0; i < numbers.length; i++) {
            var spec = numbers[i];
            var value = numberValue(spec[1]);
            if (value === null) continue;
            if (isNaN(value) || value < spec[2] || value > spec[3]) {
                return { ok: false, error: 'ערך לא תקין: יש להזין מספר בין ' + spec[2].toLocaleString('he-IL') + ' ל־' + spec[3].toLocaleString('he-IL') + '.', focusId: spec[1] };
            }
            profile[spec[0]] = Math.round(value);
        }
        if (towing && towing.checked && !profile.towing_braked_required_kg) {
            return { ok: false, error: 'סימנת שאתה צריך לגרור — יש להזין משקל גרירה נדרש עם בלמים.', focusId: 'v3_towing_braked_required_kg' };
        }
        if (profile.budget_max_ils) {
            var cars = getSelectedCars();
            if (cars.some(function (c) { return !c.asking_price_ils; })) return { ok: false, error: BUDGET_NEEDS_PRICES, focusId: 'v3_budget_max_ils' };
        }
        ['parking_constraint', 'awd_requirement', 'charging_access'].forEach(function (k) {
            var v = selectValue('v3_' + k);
            var el = byId('v3_' + k);
            if (v && !(el && el.closest && el.closest('.hidden'))) profile[k] = v;
        });
        var must = checkedValues('v3_must');
        profile.must_have_features = must;
        profile.nice_to_have_features = checkedValues('v3_nice').filter(function (f) { return must.indexOf(f) === -1; });
        profile.priorities = {};
        PRIORITY_KEYS.concat([EV_PRIORITY_KEY]).forEach(function (k) {
            var row = document.querySelector('#v3Personalization [data-v3-priority-key="' + k + '"]');
            if (!row || row.classList.contains('hidden')) return;     // a hidden slider is not sent
            var picked = document.querySelector('input[name="v3_pri_' + k + '"]:checked');
            profile.priorities[k] = picked ? parseInt(picked.value, 10) : 2;
        });
        return { ok: true, profile: profile };
    }

    function showProfileError(message, focusId) {
        var box = byId('v3ProfileError');
        if (box) {
            box.textContent = message || '';
            box.classList.toggle('hidden', !message);
        }
        if (message) {
            var target = focusId ? byId(focusId) : document.querySelector('input[name="v3_mode"]');
            if (target && target.scrollIntoView) target.scrollIntoView({ behavior: 'smooth', block: 'center' });
            if (target && target.focus) target.focus();
        }
    }

    function syncPersonalization() {
        var fields = byId('v3PersonalizedFields');
        if (fields) fields.classList.toggle('hidden', selectedMode() !== 'personalized');
        var toggle = byId('v3_towing_toggle'), kg = byId('v3_towing_braked_required_kg');
        if (toggle && kg) kg.disabled = !toggle.checked;
        if (selectedMode()) showProfileError('');
    }

    async function init(options) {
        opts = options || {};
        var section = byId('v3Personalization');
        if (section) {
            section.addEventListener('change', function (event) {
                var t = event.target;
                if (t && t.name === 'v3_must' && t.checked) {
                    var twin = section.querySelector('input[name="v3_nice"][value="' + t.value + '"]');
                    if (twin) twin.checked = false;
                }
                if (t && t.name === 'v3_nice' && t.checked) {
                    var req = section.querySelector('input[name="v3_must"][value="' + t.value + '"]');
                    if (req && req.checked) t.checked = false;
                }
                syncPersonalization();
            });
            syncPersonalization();
            applyAvailability(null);
        }
        if (!byId('v3_make_1')) return;
        try {
            var data = await getJson(catalogUrl('manufacturers'));
            for (var n = 1; n <= 3; n++) initSlot(n, data.manufacturers || []);
        } catch (e) {
            for (var m = 1; m <= 3; m++) showPickerError(m, e.message);
        }
    }

    // ==================================================================
    // progress + NDJSON
    // ==================================================================
    function resetSteps() {
        var list = byId('compareV2Steps');
        if (!list) return;
        list.innerHTML = STEP_ORDER.map(function (key) {
            return '<li data-step="' + key + '" class="v2-step">' + escapeHtml(STAGE_LABELS[key]) + '</li>';
        }).join('');
        list.classList.remove('hidden');
    }

    function markStage(stage, labelHe) {
        var list = byId('compareV2Steps');
        var status = byId('compareStatusText');
        if (status && stage !== 'complete') status.textContent = labelHe || STAGE_LABELS[stage] || '';
        if (!list) return;
        var reached = stage === 'complete' ? STEP_ORDER.length : STEP_ORDER.indexOf(stage);
        Array.prototype.forEach.call(list.querySelectorAll('[data-step]'), function (li) {
            var idx = STEP_ORDER.indexOf(li.getAttribute('data-step'));
            li.classList.toggle('v2-step-done', idx < reached);
            li.classList.toggle('v2-step-active', idx === reached);
        });
    }

    function hideSteps() {
        var list = typeof document !== 'undefined' ? byId('compareV2Steps') : null;
        if (list) list.classList.add('hidden');
    }

    async function readCompareResponse(resp, onEvent) {
        var type = (resp.headers.get('Content-Type') || '').toLowerCase();
        if (type.indexOf('application/x-ndjson') === -1 || !resp.body || !resp.body.getReader) {
            var data = await resp.json();
            return { ok: resp.ok && data.ok !== false, data: data.data, error: data.error, raw: data };
        }
        var reader = resp.body.getReader();
        var decoder = new TextDecoder('utf-8');
        var buffer = '', final = null;
        function handle(line) {
            if (!line.trim()) return;
            var event;
            try { event = JSON.parse(line); } catch (e) { return; }
            if (event.type === 'progress') { if (onEvent) onEvent(event); } else final = event;
        }
        while (true) {
            var chunk = await reader.read();
            if (chunk.done) break;
            buffer += decoder.decode(chunk.value, { stream: true });
            var lines = buffer.split('\n');
            buffer = lines.pop();
            lines.forEach(handle);
        }
        handle(buffer);
        if (!final) return { ok: false, error: { code: 'stream_incomplete', message: 'ההשוואה לא הושלמה' } };
        return { ok: final.type === 'result' && final.ok !== false, data: final.data, error: final.error, raw: final };
    }

    var api = {
        ENGINE_VERSION: ENGINE_VERSION,
        isV3Result: isV3Result,
        buildHeroHtml: buildHeroHtml,
        buildTableHtml: buildTableHtml,
        renderResult: renderResult,
        bindToggles: bindToggles,
        init: init,
        getSelectedCars: getSelectedCars,
        setSelected: setSelected,
        collectProfile: collectProfile,
        showProfileError: showProfileError,
        applyAvailability: applyAvailability,
        resetSteps: resetSteps,
        markStage: markStage,
        hideSteps: hideSteps,
        readCompareResponse: readCompareResponse,
        escapeHtml: escapeHtml
    };
    root.YedaCompareV3 = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
