/* Comparison V2 UI: exact-variant picker, the personalization step
 * (buyer-profile/2), staged progress and the result renderers.
 *
 * "comparison-v2/2" results carry a decision composed in code from validated
 * facts, the user's declared priorities and narrow JEV judgments; they are
 * rendered by renderV22. Stored "comparison-v2/1" rows keep their original
 * renderer. There is no quality score and no "correctness %" anywhere here.
 */
(function (root) {
    'use strict';

    var ENGINE_VERSION = 'comparison-v2/2';
    var ENGINE_VERSION_V21 = 'comparison-v2/1';
    var SLOTS = ['car_1', 'car_2', 'car_3'];

    var STAGE_LABELS = {
        resolving_vehicles: 'מזהה גרסאות',
        loading_government_data: 'טוען נתונים רשמיים',
        enriching: 'משלים מידע מאתרי היצרן',
        validating_sources: 'מאמת את המקורות',
        comparing_facts: 'משווה את הנתונים',
        evaluating_decision: 'שוקל את ההבדלים לפי הצרכים שלך',
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
            (isRealDecision(result, overall) ? confidenceHtml(overall.confidence, 'overall') : '') +
            '<section aria-label="כיסוי נתונים"><h5 class="text-sm font-bold text-primary mb-2">כיסוי נתונים</h5>' +
            '<div class="v2-coverage-grid">' + coverage + '</div></section>' +
            '</div>';
    }

    // ------------------------------------------------------------------
    // category cards
    // ------------------------------------------------------------------
    // JEV confidence is confidence in its returned choice — shown only for a
    // real car_N / tie decision, never for insufficient_evidence.
    function isRealDecision(result, decision) {
        var choice = (decision || {}).choice;
        return decision && decision.decision_source !== 'cross_powertrain' &&
            (choice === 'tie' || !!(result.cars || {})[choice]);
    }

    function decisionBadgeHtml(result, cat) {
        var decision = cat.decision || {};
        var choice = decision.choice;
        if (cat.status === 'cross_powertrain_descriptive') {
            return '<span class="v2-status v2-status-muted" data-badge="no_direct">ללא הכרעה ישירה</span>';
        }
        if ((result.cars || {})[choice]) {
            return '<span class="v2-badge-lead" data-badge="lead">' + carChip(result, choice) + ' <span class="text-sm font-bold text-primary">מוביל</span></span>';
        }
        if (choice === 'tie') return '<span class="v2-status" data-badge="tie">ללא יתרון משמעותי</span>';
        if (choice === 'insufficient_evidence') return '<span class="v2-status v2-status-muted" data-badge="insufficient">אין מספיק מידע</span>';
        return '<span class="v2-status v2-status-muted" data-badge="unavailable">הכרעה לא זמינה</span>';
    }

    function labelFor(evidence, metric) {
        var hit = (evidence.atomic_results || []).filter(function (r) { return r.metric === metric; })[0];
        return hit ? hit.label_he : metric;
    }

    function hasAnyValue(r, slots) {
        return slots.some(function (s) { return (r.values || {})[s] !== null && (r.values || {})[s] !== undefined; });
    }

    function keyFactRows(result, evidence) {
        var slots = slotsOf(result);
        var rows = (evidence.atomic_results || []).filter(function (r) { return r.status === 'compared'; });
        if (!rows.length) {
            rows = (evidence.atomic_results || []).filter(function (r) {
                // No direct comparison: still show the values that exist (one car only, etc.).
                return r.status !== 'compared' && hasAnyValue(r, slots);
            });
        }
        return rows.slice(0, 4);
    }

    function keyFactsHtml(result, evidence) {
        var rows = keyFactRows(result, evidence);
        if (!rows.length) return '';
        var slots = slotsOf(result);
        var body = rows.map(function (r) {
            var cells = slots.map(function (slot) {
                var shown = (r.display || {})[slot];
                var value = shown ? escapeHtml(shown) : '<span class="text-primary/60" title="לא נמצא מקור רשמי מדויק לגרסה הזו">—</span>';
                var lead = r.leader === slot ? '<span class="sr-only">(מוביל)</span><span aria-hidden="true" class="v2-lead-dot">●</span> ' : '';
                return '<span class="v2-fact-cell"><span class="v2-fact-car">' + escapeHtml(carName(result, slot)) + ':</span> ' + lead +
                    '<span class="font-bold text-primary">' + value + '</span> ' + (shown ? provenanceBadge((r.provenance || {})[slot]) : '') + '</span>';
            }).join('');
            return '<li class="v2-fact-row"><span class="v2-fact-label">' + escapeHtml(r.label_he) + '</span><span class="v2-fact-values">' + cells + '</span></li>';
        }).join('');
        return '<section class="v2-key-facts" aria-label="הנתונים המרכזיים"><h5 class="text-sm font-bold text-primary mb-1">הנתונים המרכזיים</h5><ul class="space-y-1.5">' + body + '</ul></section>';
    }

    function adasDiffHtml(result, evidence) {
        var adas = (evidence.atomic_results || []).filter(function (r) { return r.metric === 'adas_systems_count' && r.details; })[0];
        if (!adas) return '';
        return slotsOf(result).map(function (slot) {
            var only = ((adas.details || {}).systems_only_in || {})[slot] || [];
            if (!only.length) return '';
            return '<p class="text-xs text-primary/80">רק ב־' + escapeHtml(carName(result, slot)) + ': ' + only.map(escapeHtml).join(', ') + '</p>';
        }).join('');
    }

    function coverageText(result, evidence) {
        var parts = slotsOf(result).map(function (slot) {
            var c = (evidence.coverage || {})[slot] || {};
            if (!c.applicable) return null;
            return escapeHtml(carName(result, slot)) + ' ' + escapeHtml(c.available) + ' מתוך ' + escapeHtml(c.applicable);
        }).filter(Boolean);
        return parts.length ? 'כיסוי ראיות בתחום: ' + parts.join(' · ') : '';
    }

    function gapsList(result, cat) {
        var evidence = cat.evidence || {};
        if (cat.explanation && Array.isArray(cat.explanation.gaps)) return cat.explanation.gaps;
        // Rows stored before explanations existed.
        var out = [];
        var missing = (evidence.missing_metrics || []).map(function (m) { return labelFor(evidence, m); });
        if (missing.length) out.push('חסרים נתונים מאומתים עבור: ' + missing.slice(0, 5).join(', ') + '. נתון חסר אינו נחשב לחיסרון.');
        var conflicts = (evidence.conflicted_metrics || []).map(function (m) { return labelFor(evidence, m); });
        if (conflicts.length) out.push('נמצאה סתירה בין מקורות רשמיים ולכן הנתון לא השתתף בהכרעה: ' + conflicts.join(', ') + '.');
        return out;
    }

    function categorySourcesHtml(result, evidence) {
        var seen = {};
        var items = [];
        (evidence.atomic_results || []).forEach(function (r) {
            Object.keys(r.provenance || {}).forEach(function (slot) {
                var prov = (r.provenance || {})[slot];
                ((prov && prov.sources) || []).forEach(function (src) {
                    var url = safeUrl(src.source_url);
                    if (!url || seen[url]) return;
                    seen[url] = true;
                    var badge = src.source_type === 'official_importer' ? '<span class="v2-prov v2-prov-importer">יבואן רשמי</span>' : '<span class="v2-prov v2-prov-maker">יצרן</span>';
                    items.push('<li>' + badge + ' <a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" class="underline">' + escapeHtml(src.source_title || url) + '</a></li>');
                });
            });
        });
        var gov = '<li><span class="v2-prov v2-prov-gov">משרד התחבורה</span> מאגר הדגמים (WLTP) — Level 1.5</li>';
        return '<ul class="text-xs space-y-1 mt-2">' + gov + items.join('') + '</ul>';
    }

    function detailsTable(result, evidence) {
        var slots = slotsOf(result);
        var rows = (evidence.atomic_results || []).filter(function (r) {
            return hasAnyValue(r, slots) || r.status === 'compared';
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

    // For V2 rows stored before server-side explanations existed.
    function fallbackExplanation(result, cat) {
        var choice = (cat.decision || {}).choice;
        if (cat.status === 'cross_powertrain_descriptive') return 'הנתונים בתחום זה אינם בני-השוואה ישירה בין סוגי ההנעה שנבחרו, ולכן לא ניתנה הכרעה בתחום.';
        if (choice === 'insufficient_evidence') return 'אין מספיק מידע להשוואה אמינה בתחום הזה.';
        if (choice === 'decision_unavailable') return 'מנוע ההכרעה לא היה זמין, ולכן מוצגים כאן הנתונים בלבד ללא הכרעה.';
        return '';
    }

    function buildCategoryCardHtml(result, cat) {
        var evidence = cat.evidence || {};
        var decision = cat.decision || {};
        var explanation = (cat.explanation && cat.explanation.text) || fallbackExplanation(result, cat);
        var confidence = '';
        if (isRealDecision(result, decision) && pct(decision.confidence)) {
            confidence = confidenceHtml(decision.confidence, 'cat_' + cat.key);
        }
        var gaps = gapsList(result, cat);
        var coverage = coverageText(result, evidence);
        var muted = gaps.slice();
        if (decision.choice === 'insufficient_evidence' && coverage) muted.unshift(coverage);
        return '<article class="yr-compare-result-card v2-category-card rounded-2xl border border-slate-200 bg-white p-4 md:p-5 space-y-3" data-category="' + escapeHtml(cat.key) + '" data-status="' + escapeHtml(cat.status) + '" data-choice="' + escapeHtml(decision.choice || '') + '">' +
            '<div class="flex flex-wrap items-center justify-between gap-2">' +
            '<h4 class="text-lg font-bold text-primary">' + escapeHtml(cat.label_he) + '</h4>' +
            '<div class="flex flex-wrap items-center gap-2">' + decisionBadgeHtml(result, cat) + '</div></div>' +
            confidence +
            (explanation ? '<p class="v2-explanation text-sm leading-7 text-primary" data-v2-explanation>' + escapeHtml(explanation) + '</p>' : '') +
            keyFactsHtml(result, evidence) +
            adasDiffHtml(result, evidence) +
            (muted.length ? '<div class="v2-gaps" data-v2-gaps><ul class="space-y-1">' + muted.map(function (g) { return '<li>' + escapeHtml(g) + '</li>'; }).join('') + '</ul></div>' : '') +
            '<details class="v2-details"><summary>כל הנתונים והמקורות</summary>' + detailsTable(result, evidence) + categorySourcesHtml(result, evidence) + '</details>' +
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
            (skipped.length ? '<p class="text-xs text-primary/70">לא רלוונטי לאף אחד מהרכבים שנבחרו: ' + skipped.map(function (c) { return escapeHtml(c.label_he); }).join(', ') + '.</p>' : '') +
            '</section>' +
            buildSourcesHtml(result) +
            '<section class="yr-compare-result-card p-4 md:p-5"><h4 class="yr-card-title text-lg mb-3 text-primary">הערה</h4><p class="text-primary/80">ההשוואה היא כלי עזר בלבד. לפני רכישה מומלץ לבדוק היסטוריית טיפולים, בדיקת מוסך ומסמכי רכב.</p></section>';
    }

    // ==================================================================
    // V2/2 renderer (composed decision)
    // ==================================================================
    var INFLUENCE_BADGES = {
        influenced: null,
        balanced: ['ללא יתרון משמעותי', ''],
        zero_weight: ['לא השפיע — סומן כלא חשוב', 'v2-status-muted'],
        no_towing_need: ['לא השפיע — לא נדרשת גרירה', 'v2-status-muted'],
        no_declared_features: ['לא השפיע — לא הוגדר אבזור רצוי', 'v2-status-muted'],
        decided_by_requirements: ['ההכרעה נקבעה לפי דרישות החובה', 'v2-status-muted'],
        judgment_unavailable: ['שיפוט לא זמין', 'v2-status-muted'],
        insufficient_data: ['אין מספיק מידע', 'v2-status-muted'],
        not_directly_comparable: ['לא בר-השוואה ישירה', 'v2-status-muted'],
        not_applicable: ['לא רלוונטי', 'v2-status-muted']
    };

    function profileSummaryHtml(result) {
        var ps = result.profile_summary || {};
        var general = (result.buyer_profile || {}).mode === 'general';
        var headline = (ps.headline || []).map(escapeHtml).join(' · ');
        var priorities = general ? '' : (ps.priorities || []).map(function (p) {
            return '<li class="v2-chip' + (p.value === 0 ? ' v2-chip-muted' : '') + '">' + escapeHtml(p.name_he) + ': <strong>' + escapeHtml(p.label_he) + '</strong></li>';
        }).join('');
        return '<section class="v2-profile-summary" aria-label="הצרכים שהגדרת" data-v2-profile-summary>' +
            '<h5 class="text-sm font-bold text-primary/80">' + (general ? 'סוג ההשוואה' : 'השימוש שלך') + '</h5>' +
            '<p class="text-primary font-bold">' + headline + '</p>' +
            (priorities ? '<ul class="flex flex-wrap gap-2 mt-2" aria-label="מה חשוב לך">' + priorities + '</ul>' : '') +
            '</section>';
    }

    function constraintNotesHtml(result) {
        var notes = ((result.hard_constraints || {}).notes) || [];
        if (!notes.length) return '';
        var fails = notes.filter(function (n) { return n.level === 'fail'; });
        var unknown = notes.filter(function (n) { return n.level === 'unknown'; });
        return '<section class="space-y-2" aria-label="דרישות החובה שהגדרת" data-v2-constraints>' +
            (fails.length ? '<div class="v2-warning" role="alert"><h5 class="font-bold">אי-עמידה בדרישות חובה</h5><ul class="list-disc list-inside">' +
                fails.map(function (n) { return '<li>' + escapeHtml(n.text_he) + '</li>'; }).join('') + '</ul></div>' : '') +
            (unknown.length ? '<details class="v2-unknown-note"><summary>דרישות שלא ניתן היה לאמת (' + unknown.length + ')</summary><ul class="list-disc list-inside text-sm">' +
                unknown.map(function (n) { return '<li>' + escapeHtml(n.text_he) + '</li>'; }).join('') + '</ul></details>' : '') +
            '</section>';
    }

    function reasonsHtml(result) {
        var rec = result.recommendation || {};
        var reasons = rec.reasons_he || {};
        var pros = reasons['for'] || [];
        var cons = reasons.against || [];
        if (!pros.length && !cons.length) return '';
        return '<section aria-label="הסיבות המרכזיות" data-v2-reasons>' +
            (pros.length ? '<h5 class="text-sm font-bold text-primary mb-1">הסיבות המרכזיות</h5><ul class="v2-reasons">' +
                pros.map(function (t) { return '<li>' + escapeHtml(t) + '</li>'; }).join('') + '</ul>' : '') +
            (cons.length ? '<h5 class="text-sm font-bold text-primary mt-3 mb-1">איפה רכב אחר חזק יותר</h5><ul class="v2-reasons v2-reasons-against">' +
                cons.map(function (t) { return '<li>' + escapeHtml(t) + '</li>'; }).join('') + '</ul>' : '') +
            '</section>';
    }

    function buildHeroV22Html(result) {
        var rec = result.recommendation || {};
        var outcome = rec.outcome || '';
        var title = '<p class="text-sm font-bold text-primary/80">' + escapeHtml(rec.title_he || '') + '</p>';
        var main;
        if ((result.cars || {})[outcome]) {
            main = title + '<h4 class="text-2xl md:text-3xl font-black text-primary">' + escapeHtml(carName(result, outcome)) + '</h4>' +
                (rec.strength_label_he ? '<p class="text-sm text-primary"><span class="v2-status">' + escapeHtml(rec.strength_label_he) + '</span></p>' : '') +
                (rec.subtitle_he ? '<p class="text-sm text-primary">' + escapeHtml(rec.subtitle_he) + '</p>' : '');
        } else {
            main = '<h4 class="text-2xl md:text-3xl font-black text-primary">' + escapeHtml(rec.title_he || '') + '</h4>';
        }
        var coverage = rec.evidence_coverage_label_he ?
            '<p class="text-xs text-primary/80">כיסוי הראיות ביחס למה שחשוב לך: <strong>' + escapeHtml(rec.evidence_coverage_label_he) + '</strong>' +
            ' <span class="v2-tip-wrap"><button type="button" class="v2-info" aria-describedby="v2CovTip" aria-label="מה זה כיסוי הראיות?">i</button>' +
            '<span role="tooltip" id="v2CovTip" class="v2-tooltip">איזה חלק מהחשיבות שהגדרת נשען על נתונים מאומתים בני-השוואה. זה אינו סיכוי שההמלצה נכונה.</span></span></p>' : '';
        return '<div class="space-y-4" data-v2-hero data-engine="' + ENGINE_VERSION + '" data-outcome="' + escapeHtml(outcome) + '">' +
            profileSummaryHtml(result) +
            '<div class="space-y-1" data-v2-recommendation>' + main + '</div>' +
            coverage +
            constraintNotesHtml(result) +
            reasonsHtml(result) +
            '</div>';
    }

    function influenceBadgeHtml(result, card) {
        if (card.influence_status === 'influenced') {
            if (card.favoured_slot && (result.cars || {})[card.favoured_slot]) {
                return '<span class="v2-badge-lead" data-badge="influenced">השפיע לטובת ' + carChip(result, card.favoured_slot) + '</span>';
            }
            return '<span class="v2-status" data-badge="influenced">השפיע על ההכרעה</span>';
        }
        var b = INFLUENCE_BADGES[card.influence_status] || ['', 'v2-status-muted'];
        return '<span class="v2-status ' + b[1] + '" data-badge="' + escapeHtml(card.influence_status) + '">' + escapeHtml(b[0]) + '</span>';
    }

    function judgmentDetailsHtml(result, card) {
        var rows = [];
        (card.pairs || []).forEach(function (pv) {
            (pv.signals || []).forEach(function (s) {
                var pair = pv.pair.map(function (slot) { return carName(result, slot); }).join(' מול ');
                var what;
                if (s.source === 'objective') {
                    what = s.favoured_name ? 'הנתונים מעדיפים את ' + escapeHtml(s.favoured_name) : (s.status === 'tie' ? 'ללא הבדל משמעותי' : 'מדדים מעורבים');
                    if (s.materiality_label_he) what += ' · משמעות הפער לשימוש שלך: <strong>' + escapeHtml(s.materiality_label_he) + '</strong>';
                } else if (s.source === 'contextual') {
                    what = Object.keys(s.fit || {}).map(function (slot) { return escapeHtml(carName(result, slot)) + ': ' + escapeHtml(s.fit[slot].label_he); }).join(' · ');
                } else {
                    what = s.favoured_name ? 'לטובת ' + escapeHtml(s.favoured_name) : 'ללא הבדל';
                }
                if (!s.usable) what += ' · <span class="text-primary/60">לא השתתף בשקלול</span>';
                var certainty = (typeof s.model_certainty === 'number') ?
                    '<div class="text-[11px] text-primary/60">' + escapeHtml((result.model_certainty || {}).label_he || 'ודאות מודל בשיפוט הזה') + ': ' + escapeHtml(pct(s.model_certainty)) + '</div>' : '';
                rows.push('<li><span class="font-bold">' + escapeHtml(s.label_he) + '</span>' +
                    ((card.pairs || []).length > 1 ? ' <span class="text-primary/60">(' + escapeHtml(pair) + ')</span>' : '') +
                    '<div>' + what + '</div>' + certainty + '</li>');
            });
        });
        if (!rows.length) return '';
        var note = (result.model_certainty || {}).note_he;
        return '<details class="v2-details"><summary>איך זה נשקל</summary><ul class="v2-judgments space-y-2 mt-2 text-sm">' + rows.join('') + '</ul>' +
            (note ? '<p class="text-[11px] text-primary/60 mt-2">' + escapeHtml(note) + '</p>' : '') + '</details>';
    }

    function buildCategoryCardV22Html(result, card) {
        var evidence = card.evidence || {};
        var layers = card.layers || {};
        var gaps = card.gaps || [];
        function layer(title, text, attr) {
            return text ? '<div class="v2-layer"' + (attr ? ' ' + attr : '') + '><dt>' + title + '</dt><dd>' + escapeHtml(text) + '</dd></div>' : '';
        }
        return '<article class="yr-compare-result-card v2-category-card rounded-2xl border border-slate-200 bg-white p-4 md:p-5 space-y-3" data-category="' + escapeHtml(card.key) + '" data-influence="' + escapeHtml(card.influence_status) + '" data-evidence-status="' + escapeHtml(card.evidence_status) + '">' +
            '<div class="flex flex-wrap items-center justify-between gap-2">' +
            '<h4 class="text-lg font-bold text-primary">' + escapeHtml(card.label_he) + '</h4>' +
            '<div class="flex flex-wrap items-center gap-2">' + influenceBadgeHtml(result, card) + '</div></div>' +
            '<dl class="v2-layers">' +
            layer('מה הקטגוריה בודקת', layers.what, 'data-layer="what"') +
            layer('מה חשוב לך כאן', layers.importance, 'data-layer="importance"') +
            layer('מה הנתונים אומרים', layers.data, 'data-layer="data"') +
            layer('למה זה השפיע או לא השפיע', layers.influence, 'data-layer="influence"') +
            '</dl>' +
            keyFactsHtml(result, evidence) +
            adasDiffHtml(result, evidence) +
            (gaps.length ? '<div class="v2-gaps" data-v2-gaps><ul class="space-y-1">' + gaps.map(function (g) { return '<li>' + escapeHtml(g) + '</li>'; }).join('') + '</ul></div>' : '') +
            judgmentDetailsHtml(result, card) +
            '<details class="v2-details"><summary>כל הנתונים והמקורות</summary>' + detailsTable(result, evidence) + categorySourcesHtml(result, evidence) + '</details>' +
            '</article>';
    }

    function buildResultBodyV22Html(result) {
        var order = result.category_order || Object.keys(result.categories || {});
        var cards = order.map(function (k) { return (result.categories || {})[k]; }).filter(Boolean);
        var shown = cards.filter(function (c) { return c.evidence_status !== 'not_applicable'; });
        var skipped = cards.filter(function (c) { return c.evidence_status === 'not_applicable'; });
        var unsupported = (result.unsupported_concepts || []).map(function (u) { return escapeHtml(u.label_he); });
        return buildSummaryHtml(result) +
            buildVehiclesHtml(result) +
            '<section class="space-y-3"><h4 class="text-xl font-black text-primary">למה — לפי תחומים</h4>' +
            '<div class="v2-category-grid">' + shown.map(function (c) { return buildCategoryCardV22Html(result, c); }).join('') + '</div>' +
            (skipped.length ? '<p class="text-xs text-primary/70">לא רלוונטי לאף אחד מהרכבים שנבחרו: ' + skipped.map(function (c) { return escapeHtml(c.label_he); }).join(', ') + '.</p>' : '') +
            (unsupported.length ? '<p class="text-xs text-primary/70">עדיין לא נכלל בגרסה הזו: ' + unsupported.join(', ') + '.</p>' : '') +
            '</section>' +
            buildLimitationsHtml(result) +
            buildSourcesHtml(result) +
            '<section class="yr-compare-result-card p-4 md:p-5"><h4 class="yr-card-title text-lg mb-3 text-primary">הערה</h4><p class="text-primary/80">ההשוואה היא כלי עזר בלבד. לפני רכישה מומלץ לבדוק היסטוריית טיפולים, בדיקת מוסך ומסמכי רכב.</p></section>';
    }

    // ==================================================================
    // personalization step (buyer-profile/2)
    // ==================================================================
    var PRIORITY_KEYS = ['safety', 'performance', 'efficiency', 'practicality', 'purchase_price', 'warranty', 'equipment', 'environment'];
    var EV_PRIORITY_KEY = 'ev_convenience';

    function byId(id) { return document.getElementById(id); }

    function selectedMode() {
        var checked = document.querySelector('input[name="v2_mode"]:checked');
        return checked ? checked.value : '';
    }

    function pluginSelected() {
        return getSelectedCars().some(function (c) {
            var v = variantByKey(c.variant_identity_key);
            return v && (v.powertrain_family === 'ev' || v.powertrain_family === 'phev');
        });
    }

    function syncPersonalization() {
        var fields = byId('v2PersonalizedFields');
        if (fields) fields.classList.toggle('hidden', selectedMode() !== 'personalized');
        var plugin = pluginSelected();
        Array.prototype.forEach.call(document.querySelectorAll('#v2Personalization [data-ev-only]'), function (el) {
            el.classList.toggle('hidden', !plugin);
        });
        var toggle = byId('v2_towing_toggle');
        var kg = byId('v2_towing_braked_required_kg');
        if (toggle && kg) kg.disabled = !toggle.checked;
        if (selectedMode()) showProfileError('');
    }

    function numberValue(id) {
        var el = byId(id);
        if (!el || el.disabled) return null;
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
        var mode = selectedMode();
        if (!mode) return { ok: false, error: 'יש לבחור „השוואה מותאמת אליי” או „השוואה כללית”.', focusId: null };
        if (mode === 'general') return { ok: true, profile: { mode: 'general' } };
        var profile = { mode: 'personalized' };
        profile.main_use = selectValue('v2_main_use');
        if (!profile.main_use) return { ok: false, error: 'יש לבחור את השימוש העיקרי ברכב.', focusId: 'v2_main_use' };
        var numbers = [
            ['annual_km', 'v2_annual_km', 0, 150000],
            ['regular_passengers', 'v2_regular_passengers', 1, 9],
            ['budget_max_ils', 'v2_budget_max_ils', 10000, 5000000]
        ];
        var plugin = pluginSelected();
        if (byId('v2_towing_toggle') && byId('v2_towing_toggle').checked) {
            numbers.push(['towing_braked_required_kg', 'v2_towing_braked_required_kg', 100, 5000]);
        }
        if (plugin) {
            numbers.push(['typical_daily_km', 'v2_typical_daily_km', 0, 1000]);
            numbers.push(['frequent_long_trip_km', 'v2_frequent_long_trip_km', 0, 3000]);
        }
        for (var i = 0; i < numbers.length; i++) {
            var n = numbers[i];
            var value = numberValue(n[1]);
            if (value === null) continue;
            if (isNaN(value) || value < n[2] || value > n[3]) {
                return { ok: false, error: 'ערך לא תקין: יש להזין מספר בין ' + n[2].toLocaleString('he-IL') + ' ל־' + n[3].toLocaleString('he-IL') + '.', focusId: n[1] };
            }
            profile[n[0]] = Math.round(value);
        }
        if (byId('v2_towing_toggle') && byId('v2_towing_toggle').checked && !profile.towing_braked_required_kg) {
            return { ok: false, error: 'סימנת שאתה צריך לגרור — יש להזין משקל גרירה נדרש עם בלמים.', focusId: 'v2_towing_braked_required_kg' };
        }
        ['parking_constraint', 'awd_requirement', 'road_conditions'].forEach(function (k) {
            var v = selectValue('v2_' + k);
            if (v) profile[k] = v;
        });
        var cargo = document.querySelector('input[name="v2_cargo_need"]:checked');
        if (cargo) profile.cargo_need = cargo.value;
        if (plugin) {
            var charging = selectValue('v2_charging_access');
            if (charging) profile.charging_access = charging;
        }
        var must = checkedValues('v2_must');
        profile.must_have_features = must;
        profile.nice_to_have_features = checkedValues('v2_nice').filter(function (f) { return must.indexOf(f) === -1; });
        var keys = PRIORITY_KEYS.concat(plugin ? [EV_PRIORITY_KEY] : []);
        profile.priorities = {};
        keys.forEach(function (k) {
            var picked = document.querySelector('input[name="v2_pri_' + k + '"]:checked');
            profile.priorities[k] = picked ? parseInt(picked.value, 10) : 2;
        });
        return { ok: true, profile: profile };
    }

    function showProfileError(message, focusId) {
        var box = byId('v2ModeError');
        if (box) {
            box.textContent = message || '';
            box.classList.toggle('hidden', !message);
        }
        if (message) {
            var section = byId('v2Personalization');
            var target = focusId ? byId(focusId) : document.querySelector('input[name="v2_mode"]');
            if (section && section.scrollIntoView) section.scrollIntoView({ behavior: 'smooth', block: 'start' });
            if (target && target.focus) target.focus();
        }
    }

    function initPersonalization() {
        var section = byId('v2Personalization');
        if (!section) return;
        section.addEventListener('change', function (event) {
            var t = event.target;
            // a feature is either required or nice-to-have, never both
            if (t && t.name === 'v2_must' && t.checked) {
                var twin = section.querySelector('input[name="v2_nice"][value="' + t.value + '"]');
                if (twin) twin.checked = false;
            }
            if (t && t.name === 'v2_nice' && t.checked) {
                var req = section.querySelector('input[name="v2_must"][value="' + t.value + '"]');
                if (req && req.checked) t.checked = false;
            }
            syncPersonalization();
        });
        syncPersonalization();
    }

    function isV2Result(result) {
        return !!(result && (result.engine_version === ENGINE_VERSION || result.engine_version === ENGINE_VERSION_V21));
    }

    function renderResult(result, els) {
        els = els || {};
        var winner = els.winnerDisplay || document.getElementById('winnerDisplay');
        var cats = els.categoriesSection || document.getElementById('categoriesSection');
        var v22 = result && result.engine_version === ENGINE_VERSION;
        if (winner) winner.innerHTML = v22 ? buildHeroV22Html(result) : buildHeroHtml(result);
        if (cats) cats.innerHTML = v22 ? buildResultBodyV22Html(result) : buildResultBodyHtml(result);
        if (typeof document === 'undefined') return;
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
        syncPersonalization();
        if (onChangeCallback) onChangeCallback();
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
                    syncPersonalization();
                    if (onChangeCallback) onChangeCallback();
                });
            })(n);
        }
        initPersonalization();
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
        ENGINE_VERSION_V21: ENGINE_VERSION_V21,
        collectProfile: collectProfile,
        showProfileError: showProfileError,
        buildHeroV22Html: buildHeroV22Html,
        buildCategoryCardV22Html: buildCategoryCardV22Html,
        buildResultBodyV22Html: buildResultBodyV22Html,
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
