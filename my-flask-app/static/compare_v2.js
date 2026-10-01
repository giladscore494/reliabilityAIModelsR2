/* Comparison V2 UI: exact-variant picker, staged progress and the
 * evidence-based result renderer (engine_version "comparison-v2/1").
 *
 * Three separate concepts are always shown separately:
 *   what we know (facts + provenance), what the evidence says (category
 *   decisions), and how confident the decision engine is.
 * There is no quality score anywhere in this file.
 */
(function (root) {
    'use strict';

    var ENGINE_VERSION = 'comparison-v2/1';
    var SLOTS = ['car_1', 'car_2', 'car_3'];

    var STAGE_LABELS = {
        resolving_vehicles: 'מזהה גרסאות',
        loading_government_data: 'טוען נתונים רשמיים',
        enriching: 'משלים מידע מאתרי היצרן',
        validating_sources: 'מאמת את המקורות',
        comparing_facts: 'משווה את הנתונים',
        evaluating_decision: 'מחשב את ההכרעה',
        writing_summary: 'מנסח את הסיכום'
    };
    var STEP_ORDER = ['resolving_vehicles', 'loading_government_data', 'enriching', 'validating_sources', 'comparing_facts', 'evaluating_decision', 'writing_summary'];

    var REJECTION_LABELS = {
        SOURCE_DOMAIN_NOT_ALLOWED: 'מקור שאינו ברשימת האתרים הרשמיים',
        SOURCE_URL_INVALID: 'כתובת מקור לא תקינה',
        SOURCE_NOT_GROUNDED: 'מקור שלא אומת בתוצאות החיפוש',
        VARIANT_SCOPE_AMBIGUOUS: 'לא ניתן לאמת שהנתון שייך לגרסה המדויקת',
        MODEL_GENERIC_NOT_VARIANT: 'נתון כללי לדגם ולא לגרסה',
        ISRAELI_OFFICIAL_SOURCE_REQUIRED: 'מחיר/אחריות ממקור שאינו ישראלי רשמי',
        FIELD_NOT_APPLICABLE: 'נתון שאינו רלוונטי לסוג ההנעה',
        VALUE_OUT_OF_RANGE: 'ערך מחוץ לטווח סביר',
        UNIT_NOT_ALLOWED: 'יחידת מידה שאינה נתמכת',
        MEASUREMENT_STANDARD_MISSING: 'טווח ללא תקן מדידה',
        CURRENCY_NOT_ILS: 'מחיר שאינו בשקלים'
    };

    function escapeHtml(value) {
        if (value === null || value === undefined) return '';
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function safeUrl(url) {
        var text = String(url || '').trim();
        return /^https:\/\//i.test(text) ? text : '';
    }

    function pct(value) {
        return (typeof value === 'number' && isFinite(value)) ? Math.round(value * 100) + '%' : null;
    }

    function slotIndex(slot) {
        return parseInt(String(slot || '').replace('car_', ''), 10) || 1;
    }

    function carName(result, slot) {
        var car = (result.cars || {})[slot] || {};
        return car.display_name || ('רכב ' + slotIndex(slot));
    }

    function slotsOf(result) {
        return SLOTS.filter(function (s) { return (result.cars || {})[s]; });
    }

    function carChip(result, slot) {
        return '<span class="car-result-chip car-chip-' + slotIndex(slot) + '">' + escapeHtml(carName(result, slot)) + '</span>';
    }

    function provenanceBadge(prov) {
        if (!prov) return '';
        if (prov.source_level === '1.5') return '<span class="v2-prov v2-prov-gov">משרד התחבורה</span>';
        if (prov.source_type === 'official_importer') return '<span class="v2-prov v2-prov-importer">יבואן רשמי</span>';
        if (prov.source_type === 'manufacturer') return '<span class="v2-prov v2-prov-maker">יצרן</span>';
        return '';
    }

    function levelBadges() {
        return '<div class="flex flex-wrap gap-2" aria-label="רמות מקור הנתונים">' +
            '<span class="v2-level-badge v2-level-15">Level 1.5 — ממשלתי</span>' +
            '<span class="v2-level-badge v2-level-2">Level 2 — יצרן/יבואן רשמי</span>' +
            '</div>';
    }

    function confidenceHtml(confidence, idSuffix) {
        var value = pct(confidence);
        if (!value) return '';
        var tipId = 'v2ConfTip_' + idSuffix;
        return '<div class="v2-confidence">' +
            '<span>ביטחון ההכרעה: <strong>' + escapeHtml(value) + '</strong></span>' +
            '<span class="v2-tip-wrap"><button type="button" class="v2-info" aria-describedby="' + tipId + '" aria-label="מה זה ביטחון ההכרעה?">i</button>' +
            '<span role="tooltip" id="' + tipId + '" class="v2-tooltip">מדד ניסיוני של מנוע ההכרעה; אינו ציון איכות של הרכב.</span></span>' +
            '</div>';
    }

    // ------------------------------------------------------------------
    // hero
    // ------------------------------------------------------------------
    function buildHeroHtml(result) {
        var overall = result.overall || {};
        var choice = overall.choice;
        var headline;
        var line;
        if (choice && (result.cars || {})[choice]) {
            headline = '<p class="text-sm font-bold text-primary/80">היתרון הכולל כרגע</p>' +
                '<h4 class="text-2xl md:text-3xl font-black text-primary">' + escapeHtml(carName(result, choice)) + '</h4>';
            line = 'לפי הנתונים הזמינים כרגע, ל־' + escapeHtml(carName(result, choice)) + ' יש יתרון כולל.';
        } else if (choice === 'tie') {
            headline = '<h4 class="text-2xl md:text-3xl font-black text-primary">אין כרגע יתרון משמעותי</h4>';
            line = 'ההבדלים בנתונים שנבדקו מאוזנים או אינם משמעותיים.';
        } else if (choice === 'insufficient_evidence') {
            headline = '<h4 class="text-2xl md:text-3xl font-black text-primary">אין מספיק מידע להכרעה כוללת</h4>';
            line = 'הנתונים המאומתים הזמינים אינם מספיקים כדי לקבוע יתרון כולל.';
        } else {
            headline = '<h4 class="text-2xl md:text-3xl font-black text-primary">ההכרעה הכוללת אינה זמינה כרגע</h4>';
            line = 'מוצגת השוואת הנתונים בלבד — העובדות למטה עדיין מלאות ושימושיות.';
        }
        var coverage = slotsOf(result).map(function (slot) {
            var cov = (result.coverage || {})[slot] || {};
            var gov = (cov.government || {}).label_he || '—';
            var off = (cov.official || {}).label_he || '—';
            return '<div class="v2-coverage-item">' + carChip(result, slot) +
                '<dl class="mt-2 text-sm text-primary space-y-1">' +
                '<div><dt class="inline text-primary/70">ממשלה:</dt> <dd class="inline font-bold">' + escapeHtml(gov) + '</dd></div>' +
                '<div><dt class="inline text-primary/70">מקורות יצרן:</dt> <dd class="inline font-bold">' + escapeHtml(off) + '</dd></div>' +
                '</dl></div>';
        }).join('');
        return '<div class="space-y-4" data-v2-hero data-overall-choice="' + escapeHtml(choice || '') + '">' +
            levelBadges() +
            '<div class="space-y-1">' + headline + '<p class="text-primary leading-7">' + line + '</p></div>' +
            confidenceHtml(overall.confidence, 'overall') +
            '<section aria-label="כיסוי נתונים"><h5 class="text-sm font-bold text-primary mb-2">כיסוי נתונים</h5>' +
            '<div class="v2-coverage-grid">' + coverage + '</div></section>' +
            '</div>';
    }

    // ------------------------------------------------------------------
    // category cards
    // ------------------------------------------------------------------
    function categoryStatusHtml(result, cat) {
        var decision = cat.decision || {};
        var choice = decision.choice;
        if (cat.status === 'insufficient_evidence' || choice === 'insufficient_evidence') {
            return '<span class="v2-status v2-status-muted">אין מספיק מידע להשוואה אמינה בתחום הזה</span>';
        }
        if (cat.status === 'decision_unavailable' || choice === 'decision_unavailable') {
            if ((cat.evidence || {}).status === 'no_comparable_evidence') {
                return '<span class="v2-status v2-status-muted">אין מספיק מידע להשוואה אמינה בתחום הזה</span>';
            }
            return '<span class="v2-status v2-status-muted">ההכרעה בתחום זה אינה זמינה — מוצגים נתונים בלבד</span>';
        }
        if (choice === 'tie') {
            return '<span class="v2-status">אין יתרון משמעותי</span>';
        }
        if ((result.cars || {})[choice]) {
            return carChip(result, choice) + ' <span class="text-sm font-bold text-primary">מוביל</span>';
        }
        return '';
    }

    function metricRows(result, evidence) {
        var rows = (evidence.atomic_results || []).filter(function (r) { return r.status === 'compared'; });
        if (!rows.length) {
            rows = (evidence.atomic_results || []).filter(function (r) {
                return r.status === 'descriptive' && Object.keys(r.values || {}).some(function (k) { return r.values[k] !== null; });
            });
        }
        return rows.slice(0, 4);
    }

    function perCarFacts(result, evidence) {
        var rows = metricRows(result, evidence);
        if (!rows.length) return '';
        return '<div class="v2-car-facts grid grid-cols-1 sm:grid-cols-' + Math.min(slotsOf(result).length, 3) + ' gap-3">' +
            slotsOf(result).map(function (slot) {
                var items = rows.map(function (r) {
                    var shown = (r.display || {})[slot];
                    var prov = (r.provenance || {})[slot];
                    var value = shown ? escapeHtml(shown) : '<span class="text-primary/60" title="לא נמצא מקור רשמי מדויק לגרסה הזו">—</span>';
                    var lead = r.leader === slot ? ' <span class="sr-only">(מוביל)</span><span aria-hidden="true" class="v2-lead-dot">●</span>' : '';
                    return '<li><span class="text-primary/80">' + escapeHtml(r.label_he) + ':</span> <span class="font-bold text-primary">' + value + '</span>' + lead + ' ' + provenanceBadge(prov) + '</li>';
                }).join('');
                return '<div><div class="mb-1">' + carChip(result, slot) + '</div><ul class="text-sm leading-7 space-y-0.5">' + items + '</ul></div>';
            }).join('') + '</div>';
    }

    function labelFor(evidence, metric) {
        var hit = (evidence.atomic_results || []).filter(function (r) { return r.metric === metric; })[0];
        return hit ? hit.label_he : metric;
    }

    function coverageLine(result, evidence) {
        var parts = slotsOf(result).map(function (slot) {
            var c = (evidence.coverage || {})[slot] || {};
            if (!c.applicable) return null;
            return escapeHtml(carName(result, slot)) + ' ' + escapeHtml(c.available) + ' מתוך ' + escapeHtml(c.applicable);
        }).filter(Boolean);
        return parts.length ? '<p class="text-xs text-primary/70">כיסוי ראיות: ' + parts.join(' · ') + '</p>' : '';
    }

    function detailsTable(result, evidence) {
        var slots = slotsOf(result);
        var rows = (evidence.atomic_results || []).filter(function (r) {
            return slots.some(function (s) { return (r.values || {})[s] !== null && (r.values || {})[s] !== undefined; }) || r.status === 'compared';
        });
        if (!rows.length) return '<p class="text-sm text-primary/70">אין נתונים להצגה בתחום זה.</p>';
        var head = '<tr><th scope="col">מדד</th>' + slots.map(function (s) { return '<th scope="col">' + escapeHtml(carName(result, s)) + '</th>'; }).join('') + '<th scope="col">תוצאה</th></tr>';
        var body = rows.map(function (r) {
            var outcome;
            if (r.status === 'compared') {
                outcome = r.leader === 'tie' ? 'ללא הבדל משמעותי' : ((result.cars || {})[r.leader] ? escapeHtml(carName(result, r.leader)) + ' מוביל' : '');
            } else if (r.status === 'not_comparable') {
                outcome = r.reason === 'NOT_CROSS_POWERTRAIN_COMPARABLE' ? 'לא בר-השוואה בין סוגי הנעה' : 'לא בר-השוואה ישירה';
            } else if (r.status === 'conflict') {
                outcome = 'סתירה בין מקורות';
            } else if (r.status === 'insufficient_data') {
                outcome = 'אין מספיק נתונים';
            } else {
                outcome = 'מידע הקשרי';
            }
            var cells = slots.map(function (s) {
                var shown = (r.display || {})[s];
                return '<td>' + (shown ? escapeHtml(shown) + ' ' + provenanceBadge((r.provenance || {})[s]) : '<span class="text-primary/60">—</span>') + '</td>';
            }).join('');
            var note = r.note_he ? '<div class="text-[11px] text-primary/60">' + escapeHtml(r.note_he) + '</div>' : '';
            return '<tr><th scope="row">' + escapeHtml(r.label_he) + note + '</th>' + cells + '<td>' + outcome + '</td></tr>';
        }).join('');
        return '<div class="overflow-x-auto"><table class="v2-table"><thead>' + head + '</thead><tbody>' + body + '</tbody></table></div>';
    }

    function buildCategoryCardHtml(result, cat) {
        var evidence = cat.evidence || {};
        var decision = cat.decision || {};
        var missing = (evidence.missing_metrics || []).map(function (m) { return labelFor(evidence, m); });
        var conflicts = (evidence.conflicted_metrics || []).map(function (m) { return labelFor(evidence, m); });
        var notComparable = (evidence.not_comparable || []).filter(function (n) { return n.reason === 'NOT_CROSS_POWERTRAIN_COMPARABLE'; });
        var adas = (evidence.atomic_results || []).filter(function (r) { return r.metric === 'adas_systems_count' && r.details; })[0];
        var adasHtml = '';
        if (adas) {
            adasHtml = slotsOf(result).map(function (slot) {
                var only = ((adas.details || {}).systems_only_in || {})[slot] || [];
                if (!only.length) return '';
                return '<p class="text-xs text-primary/80">רק ב־' + escapeHtml(carName(result, slot)) + ': ' + only.map(escapeHtml).join(', ') + '</p>';
            }).join('');
        }
        return '<article class="yr-compare-result-card v2-category-card rounded-2xl border border-slate-200 bg-white p-4 md:p-5 space-y-3" data-category="' + escapeHtml(cat.key) + '" data-status="' + escapeHtml(cat.status) + '">' +
            '<div class="flex flex-wrap items-center justify-between gap-2">' +
            '<h4 class="text-lg font-bold text-primary">' + escapeHtml(cat.label_he) + '</h4>' +
            '<div class="flex flex-wrap items-center gap-2">' + categoryStatusHtml(result, cat) + '</div></div>' +
            (decision.decision_source === 'jev' && pct(decision.confidence) ? '<p class="text-sm text-primary">ביטחון: <strong>' + escapeHtml(pct(decision.confidence)) + '</strong></p>' : '') +
            coverageLine(result, evidence) +
            perCarFacts(result, evidence) +
            adasHtml +
            (notComparable.length ? '<p class="text-xs text-primary/80">' + notComparable.map(function (n) { return escapeHtml(labelFor(evidence, n.metric)); }).join(', ') + ' — נמדדים ביחידות שונות לפי סוג ההנעה ולכן לא הושוו ישירות.</p>' : '') +
            (conflicts.length ? '<p class="text-xs v2-conflict">נמצאה סתירה בין מקורות רשמיים ולכן הנתון לא השתתף בהכרעה: ' + conflicts.map(escapeHtml).join(', ') + '</p>' : '') +
            (missing.length ? '<p class="text-xs text-primary/70">מידע חסר: ' + missing.slice(0, 6).map(escapeHtml).join(', ') + (missing.length > 6 ? ' ועוד' : '') + ' — לא נמצא מקור רשמי מדויק לגרסה הזו. נתון חסר אינו נחשב לחיסרון.</p>' : '') +
            '<details class="v2-details"><summary>פרטים ומקורות</summary>' + detailsTable(result, evidence) + '</details>' +
            '</article>';
    }

    // ------------------------------------------------------------------
    // vehicles, sources, summary
    // ------------------------------------------------------------------
    function buildVehiclesHtml(result) {
        return '<section class="grid grid-cols-1 md:grid-cols-' + Math.min(slotsOf(result).length, 3) + ' gap-3" aria-label="הגרסאות שהושוו">' +
            slotsOf(result).map(function (slot) {
                var car = result.cars[slot];
                var bits = [car.year, car.trim, car.horsepower ? car.horsepower + ' כ״ס' : null, car.drivetrain, car.fuel_label].filter(Boolean);
                return '<article class="yr-compare-result-card rounded-2xl border border-slate-200 bg-white p-4 space-y-2">' +
                    carChip(result, slot) +
                    '<p class="text-sm text-primary">' + bits.map(escapeHtml).join(' · ') + '</p>' +
                    '<p class="text-[11px] text-primary/60">קוד דגם: ' + escapeHtml(car.official_model_code || '—') + '</p>' +
                    '</article>';
            }).join('') + '</section>';
    }

    function buildSourcesHtml(result) {
        var blocks = slotsOf(result).map(function (slot) {
            var sources = (result.sources || {})[slot] || [];
            var diag = (result.diagnostics || {})[slot] || {};
            var list = sources.map(function (src) {
                var url = safeUrl(src.source_url);
                var badge = src.source_type === 'official_importer' ? '<span class="v2-prov v2-prov-importer">יבואן רשמי</span>' : '<span class="v2-prov v2-prov-maker">יצרן</span>';
                var title = escapeHtml(src.source_title || src.source_url || '');
                return '<li>' + badge + ' ' + (url ? '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" class="underline">' + title + '</a>' : title) + '</li>';
            }).join('');
            var rejected = diag.rejected_claims || [];
            var reasons = {};
            rejected.forEach(function (r) {
                var key = /^VARIANT_.*MISMATCH$/.test(r.reason || '') ? 'VARIANT_MISMATCH' : r.reason;
                reasons[key] = (reasons[key] || 0) + 1;
            });
            var reasonList = Object.keys(reasons).map(function (k) {
                var label = k === 'VARIANT_MISMATCH' ? 'הנתון שייך לגרסה אחרת' : (REJECTION_LABELS[k] || 'נתון שלא עבר אימות');
                return '<li>' + escapeHtml(label) + ': ' + reasons[k] + '</li>';
            }).join('');
            var generic = (diag.model_generic_claims || []).length;
            var govConflicts = (diag.government_conflicts || []).length;
            return '<div class="space-y-2">' + carChip(result, slot) +
                '<ul class="text-sm space-y-1"><li><span class="v2-prov v2-prov-gov">משרד התחבורה</span> מאגר הדגמים (WLTP) — Level 1.5</li>' + list + '</ul>' +
                (!sources.length ? '<p class="text-xs text-primary/70">לא נמצא מקור רשמי מדויק לגרסה הזו.</p>' : '') +
                (reasonList ? '<p class="text-xs font-bold text-primary/80">נתונים שנדחו ולא השתתפו בהשוואה:</p><ul class="text-xs text-primary/70 space-y-0.5">' + reasonList + '</ul>' : '') +
                (generic ? '<p class="text-xs text-primary/70">' + generic + ' נתונים כלליים לדגם (לא לגרסה) נשמרו לבדיקה בלבד.</p>' : '') +
                (govConflicts ? '<p class="text-xs v2-conflict">' + govConflicts + ' נתונים ממקור רשמי סתרו את נתוני משרד התחבורה — הנתון הממשלתי נשמר.</p>' : '') +
                '</div>';
        }).join('');
        return '<details class="yr-compare-result-card v2-details rounded-2xl border border-slate-200 bg-white p-4 md:p-5"><summary class="text-lg font-bold text-primary">מקורות ופרטי אימות</summary>' +
            '<div class="mt-3 grid grid-cols-1 md:grid-cols-' + Math.min(slotsOf(result).length, 3) + ' gap-4">' + blocks + '</div></details>';
    }

    function buildSummaryHtml(result) {
        if (!result.summary) return '';
        return '<section class="rounded-2xl border border-primary/30 bg-primary/10 p-4 md:p-5 yr-compare-result-card" aria-labelledby="v2SummaryTitle">' +
            '<h4 id="v2SummaryTitle" class="text-lg font-bold text-primary mb-2">סיכום</h4>' +
            '<p class="text-sm md:text-base leading-8 text-primary" data-v2-summary>' + escapeHtml(result.summary) + '</p></section>';
    }

    function buildLimitationsHtml(result) {
        var items = (result.limitations || []).filter(Boolean);
        if (!items.length) return '';
        return '<section class="yr-disclaimer--info p-4 text-sm rounded-2xl" aria-label="מגבלות ההשוואה"><ul class="list-disc list-inside space-y-1 text-primary/90">' +
            items.map(function (t) { return '<li>' + escapeHtml(t) + '</li>'; }).join('') + '</ul></section>';
    }

    function buildResultBodyHtml(result) {
        var order = result.category_order || Object.keys(result.categories || {});
        var cats = order.map(function (k) { return (result.categories || {})[k]; }).filter(Boolean);
        var shown = cats.filter(function (c) { return c.status !== 'not_applicable'; });
        var skipped = cats.filter(function (c) { return c.status === 'not_applicable'; });
        return buildSummaryHtml(result) +
            buildLimitationsHtml(result) +
            buildVehiclesHtml(result) +
            '<section class="space-y-3"><h4 class="text-xl font-black text-primary">השוואה לפי תחומים</h4>' +
            '<div class="v2-category-grid">' + shown.map(function (c) { return buildCategoryCardHtml(result, c); }).join('') + '</div>' +
            (skipped.length ? '<p class="text-xs text-primary/70">לא רלוונטי להשוואה הזו: ' + skipped.map(function (c) { return escapeHtml(c.label_he); }).join(', ') + '.</p>' : '') +
            '</section>' +
            buildSourcesHtml(result) +
            '<section class="yr-compare-result-card p-4 md:p-5"><h4 class="yr-card-title text-lg mb-3 text-primary">הערה</h4><p class="text-primary/80">ההשוואה היא כלי עזר בלבד. לפני רכישה מומלץ לבדוק היסטוריית טיפולים, בדיקת מוסך ומסמכי רכב.</p></section>';
    }

    function isV2Result(result) {
        return !!(result && result.engine_version === ENGINE_VERSION);
    }

    function renderResult(result, els) {
        els = els || {};
        var winner = els.winnerDisplay || document.getElementById('winnerDisplay');
        var cats = els.categoriesSection || document.getElementById('categoriesSection');
        if (winner) winner.innerHTML = buildHeroHtml(result);
        if (cats) cats.innerHTML = buildResultBodyHtml(result);
        ['assumptionsDisplay', 'topReasons', 'compareApiDisclaimers'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.classList.add('hidden');
        });
    }

    // ------------------------------------------------------------------
    // picker
    // ------------------------------------------------------------------
    var catalog = [];
    var onChangeCallback = null;

    function variantByKey(key) {
        return catalog.filter(function (v) { return v.variant_identity_key === key; })[0] || null;
    }

    function variantCardHtml(v) {
        var bits = [v.year, v.trim, v.horsepower ? v.horsepower + ' כ״ס' : null, v.drivetrain, v.fuel_label].filter(Boolean);
        return '<div class="font-bold text-primary">' + escapeHtml(v.make + ' ' + v.model) + '</div>' +
            '<div class="text-sm text-primary">' + bits.map(escapeHtml).join(' · ') + '</div>' +
            '<div class="mt-1"><span class="v2-level-badge v2-level-15">Level 1.5 — ממשלתי</span></div>';
    }

    function renderVariantCard(n) {
        var select = document.getElementById('v2_variant_' + n);
        var card = document.getElementById('v2_variant_card_' + n);
        if (!select || !card) return;
        var v = variantByKey(select.value);
        if (!v) { card.classList.add('hidden'); card.innerHTML = ''; return; }
        card.innerHTML = variantCardHtml(v);
        card.classList.remove('hidden');
    }

    function getSelectedCars() {
        var cars = [];
        for (var n = 1; n <= 3; n++) {
            var select = document.getElementById('v2_variant_' + n);
            if (select && select.value) cars.push({ variant_identity_key: select.value });
        }
        return cars;
    }

    function setSelected(keys) {
        (keys || []).slice(0, 3).forEach(function (key, idx) {
            var select = document.getElementById('v2_variant_' + (idx + 1));
            if (select && variantByKey(key)) { select.value = key; renderVariantCard(idx + 1); }
        });
        if (onChangeCallback) onChangeCallback();
    }

    function syncReliabilityNote() {
        var box = document.querySelector('.buyer-priority[data-priority="reliability"]');
        var note = document.getElementById('v2ReliabilityNote');
        if (note) note.classList.toggle('hidden', !(box && box.checked));
    }

    function init(options) {
        options = options || {};
        onChangeCallback = options.onChange || null;
        var data = document.getElementById('compare-v2-catalog');
        try { catalog = JSON.parse((data && data.textContent) || '[]') || []; } catch (e) { catalog = []; }
        for (var n = 1; n <= 3; n++) {
            (function (slotN) {
                var select = document.getElementById('v2_variant_' + slotN);
                if (!select) return;
                select.addEventListener('change', function () {
                    renderVariantCard(slotN);
                    if (onChangeCallback) onChangeCallback();
                });
            })(n);
        }
        var box = document.querySelector('.buyer-priority[data-priority="reliability"]');
        if (box) box.addEventListener('change', syncReliabilityNote);
        syncReliabilityNote();
    }

    // ------------------------------------------------------------------
    // progress
    // ------------------------------------------------------------------
    function stepKey(stage) {
        return /^enriching_car_/.test(stage) ? 'enriching' : stage;
    }

    function resetSteps() {
        var list = document.getElementById('compareV2Steps');
        if (!list) return;
        list.innerHTML = STEP_ORDER.map(function (key) {
            return '<li data-step="' + key + '" class="v2-step">' + escapeHtml(STAGE_LABELS[key]) + '</li>';
        }).join('');
        list.classList.remove('hidden');
    }

    function markStage(stage, labelHe) {
        var key = stepKey(stage);
        var list = document.getElementById('compareV2Steps');
        var status = document.getElementById('compareStatusText');
        if (status && stage !== 'complete') status.textContent = labelHe || STAGE_LABELS[key] || '';
        if (!list) return;
        var reached = STEP_ORDER.indexOf(key);
        if (stage === 'complete') reached = STEP_ORDER.length;
        Array.prototype.forEach.call(list.querySelectorAll('[data-step]'), function (li) {
            var idx = STEP_ORDER.indexOf(li.getAttribute('data-step'));
            li.classList.toggle('v2-step-done', idx < reached);
            li.classList.toggle('v2-step-active', idx === reached);
            if (idx === reached) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current');
        });
    }

    function hideSteps() {
        var list = document.getElementById('compareV2Steps');
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
        var buffer = '';
        var final = null;
        function handle(line) {
            if (!line.trim()) return;
            var event;
            try { event = JSON.parse(line); } catch (e) { return; }
            if (event.type === 'progress') { if (onEvent) onEvent(event); }
            else final = event;
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
        init: init,
        getSelectedCars: getSelectedCars,
        setSelected: setSelected,
        isV2Result: isV2Result,
        renderResult: renderResult,
        resetSteps: resetSteps,
        markStage: markStage,
        hideSteps: hideSteps,
        readCompareResponse: readCompareResponse,
        buildHeroHtml: buildHeroHtml,
        buildCategoryCardHtml: buildCategoryCardHtml,
        buildResultBodyHtml: buildResultBodyHtml,
        escapeHtml: escapeHtml
    };
    root.YedaCompareV2 = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
