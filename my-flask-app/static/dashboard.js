// static/js/dashboard.js
// -----------------------------------------
// אוברליי פרטי חיפוש לדשבורד – מסך מלא במובייל
// דורש אלמנטים עם class="js-history-card" ו- data-search-id
// -----------------------------------------

(function () {
    const BODY = document.body;
    let overlayEl = null;
    let overlayTitleEl = null;
    let overlaySubtitleEl = null;
    let overlayContentEl = null;
    let closeBtnEl = null;
    let previousBodyOverflow = null;

    // יצירת האוברליי פעם אחת
    function createOverlay() {
        if (overlayEl) return;

        overlayEl = document.createElement("div");
        overlayEl.id = "history-overlay";
        overlayEl.className =
            "fixed inset-0 bg-slate-900/60 z-50 hidden";

        overlayEl.innerHTML = `
            <div class="absolute inset-0 flex items-stretch sm:items-center justify-center">
                <div class="bg-white w-full sm:max-w-2xl sm:rounded-2xl sm:shadow-xl sm:m-4 overflow-y-auto max-h-screen flex flex-col">
                    <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
                        <div class="flex flex-col gap-0.5">
                            <h2 id="overlay-title" class="text-sm sm:text-base font-semibold text-slate-900">
                                פרטי חיפוש
                            </h2>
                            <p id="overlay-subtitle" class="text-[11px] sm:text-xs text-slate-500"></p>
                        </div>
                        <button
                            id="history-overlay-close"
                            type="button"
                            class="inline-flex items-center justify-center rounded-full border border-slate-200 w-8 h-8 text-slate-600 hover:bg-slate-100 hover:text-slate-900 text-sm"
                            aria-label="סגור">
                            ✕
                        </button>
                    </div>
                    <div id="overlay-content" class="p-4 text-[11px] sm:text-sm text-slate-800 space-y-3"></div>
                </div>
            </div>
        `;

        document.body.appendChild(overlayEl);

        overlayTitleEl = overlayEl.querySelector("#overlay-title");
        overlaySubtitleEl = overlayEl.querySelector("#overlay-subtitle");
        overlayContentEl = overlayEl.querySelector("#overlay-content");
        closeBtnEl = overlayEl.querySelector("#history-overlay-close");

        if (closeBtnEl) {
            closeBtnEl.addEventListener("click", hideOverlay);
        }

        // סגירה בלחיצה בחוץ (אבל לא על הקונטיינר הפנימי)
        overlayEl.addEventListener("click", function (e) {
            if (e.target === overlayEl) {
                hideOverlay();
            }
        });

        // סגירה ב-ESC
        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape" && !overlayEl.classList.contains("hidden")) {
                hideOverlay();
            }
        });
    }

    function showOverlay() {
        if (!overlayEl) createOverlay();
        previousBodyOverflow = BODY.style.overflow;
        BODY.style.overflow = "hidden"; // ביטול גלילה מתחת
        overlayEl.classList.remove("hidden");
    }

    function hideOverlay() {
        if (!overlayEl) return;
        overlayEl.classList.add("hidden");
        BODY.style.overflow = previousBodyOverflow || "";
    }

    function setOverlayMeta(meta) {
        if (!meta) return;
        if (overlayTitleEl) {
            const titleText = `${(meta.make || "").toString()} ${(meta.model || "").toString()} ${(meta.year || "").toString()}`.trim();
            overlayTitleEl.textContent = titleText || "פרטי חיפוש";
        }
        if (overlaySubtitleEl) {
            const ts = meta.timestamp || "";
            const km = meta.mileage_range || "";
            const fuel = meta.fuel_type || "";
            const gear = meta.transmission || "";
            overlaySubtitleEl.textContent =
                `תאריך: ${ts} · ק״מ: ${km} · דלק: ${fuel} · גיר: ${gear}`;
        }
    }

    // רינדור הנתונים המלאים מה־JSON של ה-AI
    function renderData(meta, data) {
        if (!overlayContentEl) return;
        overlayContentEl.innerHTML = "";

        if (!data || typeof data !== "object") {
            overlayContentEl.innerHTML =
                `<div class="text-red-600 text-xs">לא התקבלו נתונים מהחיפוש.</div>`;
            return;
        }

        const sections = [];

        // תג מקור (cache/AI חדש)
        if (data.source_tag) {
            sections.push(`
                <div class="text-[11px] px-3 py-2 rounded-xl bg-slate-50 border border-slate-200 text-slate-700">
                    ${escapeHtml(data.source_tag)}
                </div>
            `);
        }

        // אזהרת ק"מ / הערת ק"מ
        if (data.mileage_note) {
            sections.push(`
                <div class="text-[11px] px-3 py-2 rounded-xl bg-amber-50 border border-amber-200 text-amber-800">
                    ${escapeHtml(data.mileage_note)}
                </div>
            `);
        }

        // ציון בסיסי
        if (data.base_score_calculated !== undefined) {
            sections.push(`
                <div class="border border-slate-100 rounded-2xl p-3 bg-slate-50 flex items-center justify-between">
                    <div class="text-[11px] text-slate-600">
                        ציון אמינות כללי (0–100)
                    </div>
                    <div class="font-semibold text-sm text-slate-900">
                        ${escapeHtml(data.base_score_calculated)}
                    </div>
                </div>
            `);
        }

        // פירוט ציון – score_breakdown
        if (data.score_breakdown && typeof data.score_breakdown === "object") {
            const sb = data.score_breakdown;
            const rows = [];
            Object.entries(sb).forEach(([k, v]) => {
                const label = breakdownLabel(k);
                rows.push(`
                    <div class="flex items-center justify-between text-[11px]">
                        <span class="text-slate-600">${escapeHtml(label)}</span>
                        <span class="font-medium text-slate-900">${escapeHtml(v)}</span>
                    </div>
                `);
            });

            if (rows.length) {
                sections.push(`
                    <div class="border border-slate-100 rounded-2xl p-3">
                        <div class="text-[11px] font-semibold text-slate-800 mb-2">
                            פירוט תתי-ציונים
                        </div>
                        <div class="space-y-1">
                            ${rows.join("")}
                        </div>
                    </div>
                `);
            }
        }

        // תקלות נפוצות
        if (Array.isArray(data.common_issues) && data.common_issues.length) {
            const items = data.common_issues
                .filter(Boolean)
                .map((issue) => `<li class="ml-4 list-disc">${escapeHtml(issue)}</li>`)
                .join("");

            sections.push(`
                <div class="border border-slate-100 rounded-2xl p-3">
                    <div class="text-[11px] font-semibold text-slate-800 mb-1.5">
                        תקלות נפוצות
                    </div>
                    <ul class="text-[11px] text-slate-700 space-y-0.5">
                        ${items}
                    </ul>
                </div>
            `);
        }

        // תקלות עם עלויות
        if (Array.isArray(data.issues_with_costs) && data.issues_with_costs.length) {
            const rows = data.issues_with_costs.map((row) => {
                const issue = row.issue || "";
                const cost = row.avg_cost_ILS || "";
                const src = row.source || "";
                const sev = row.severity || "";
                return `
                    <tr class="border-t border-slate-100">
                        <td class="px-2 py-1 align-top">${escapeHtml(issue)}</td>
                        <td class="px-2 py-1 align-top text-center">${escapeHtml(cost)}</td>
                        <td class="px-2 py-1 align-top text-center">${escapeHtml(sev)}</td>
                        <td class="px-2 py-1 align-top text-[10px] text-slate-500">${escapeHtml(src)}</td>
                    </tr>
                `;
            }).join("");

            sections.push(`
                <div class="border border-slate-100 rounded-2xl p-3">
                    <div class="text-[11px] font-semibold text-slate-800 mb-1.5">
                        תקלות עם עלות משוערת
                    </div>
                    <div class="overflow-x-auto">
                        <table class="min-w-full border-collapse text-[11px]">
                            <thead>
                                <tr class="bg-slate-50 text-slate-600">
                                    <th class="px-2 py-1 text-right font-medium">תקלה</th>
                                    <th class="px-2 py-1 text-center font-medium">עלות ממוצעת (₪)</th>
                                    <th class="px-2 py-1 text-center font-medium">חומרה</th>
                                    <th class="px-2 py-1 text-right font-medium">מקור</th>
                                </tr>
                            </thead>
                            <tbody class="text-slate-700">
                                ${rows}
                            </tbody>
                        </table>
                    </div>
                </div>
            `);
        }

        // עלות תיקון ממוצעת
        if (data.avg_repair_cost_ILS !== undefined) {
            sections.push(`
                <div class="border border-slate-100 rounded-2xl p-3 flex items-center justify-between">
                    <div class="text-[11px] text-slate-600">
                        עלות תיקון ממוצעת לרכב (הערכה)
                    </div>
                    <div class="font-semibold text-sm text-slate-900">
                        ${escapeHtml(data.avg_repair_cost_ILS)} ₪
                    </div>
                </div>
            `);
        }

        // סיכום מקצועי
        if (data.reliability_summary) {
            sections.push(`
                <div class="border border-slate-100 rounded-2xl p-3">
                    <div class="text-[11px] font-semibold text-slate-800 mb-1.5">
                        סיכום מקצועי
                    </div>
                    <p class="text-[11px] text-slate-700 leading-relaxed whitespace-pre-line">
                        ${escapeHtml(data.reliability_summary)}
                    </p>
                </div>
            `);
        }

        // סיכום פשוט לנהג צעיר
        if (data.reliability_summary_simple) {
            sections.push(`
                <div class="border border-emerald-100 rounded-2xl p-3 bg-emerald-50/60">
                    <div class="text-[11px] font-semibold text-emerald-900 mb-1.5">
                        הסבר פשוט לנהג צעיר
                    </div>
                    <p class="text-[11px] text-emerald-900 leading-relaxed whitespace-pre-line">
                        ${escapeHtml(data.reliability_summary_simple)}
                    </p>
                </div>
            `);
        }

        // מקורות
        if (Array.isArray(data.sources) && data.sources.length) {
            const items = data.sources
                .filter(Boolean)
                .map((src) => `<li class="ml-4 list-disc">${escapeHtml(src)}</li>`)
                .join("");
            sections.push(`
                <div class="border border-slate-100 rounded-2xl p-3">
                    <div class="text-[11px] font-semibold text-slate-800 mb-1.5">
                        מקורות עיקריים
                    </div>
                    <ul class="text-[11px] text-slate-700 space-y-0.5">
                        ${items}
                    </ul>
                </div>
            `);
        }

        // בדיקות מומלצות
        if (Array.isArray(data.recommended_checks) && data.recommended_checks.length) {
            const items = data.recommended_checks
                .filter(Boolean)
                .map((c) => `<li class="ml-4 list-disc">${escapeHtml(c)}</li>`)
                .join("");
            sections.push(`
                <div class="border border-slate-100 rounded-2xl p-3">
                    <div class="text-[11px] font-semibold text-slate-800 mb-1.5">
                        בדיקות מומלצות לפני קנייה
                    </div>
                    <ul class="text-[11px] text-slate-700 space-y-0.5">
                        ${items}
                    </ul>
                </div>
            `);
        }

        // מתחרים
        if (Array.isArray(data.common_competitors_brief) && data.common_competitors_brief.length) {
            const rows = data.common_competitors_brief.map((c) => {
                const model = c.model || "";
                const brief = c.brief_summary || "";
                return `
                    <div class="border border-slate-100 rounded-xl px-3 py-2 text-[11px]">
                        <div class="font-semibold text-slate-900 mb-0.5">${escapeHtml(model)}</div>
                        <div class="text-slate-700">${escapeHtml(brief)}</div>
                    </div>
                `;
            }).join("");

            sections.push(`
                <div class="border border-slate-100 rounded-2xl p-3">
                    <div class="text-[11px] font-semibold text-slate-800 mb-1.5">
                        מתחרים עיקריים בקצרה
                    </div>
                    <div class="grid grid-cols-1 gap-1.5">
                        ${rows}
                    </div>
                </div>
            `);
        }

        if (!sections.length) {
            overlayContentEl.innerHTML =
                `<div class="text-[11px] text-slate-600">לא נמצאו פרטים להצגה.</div>`;
        } else {
            overlayContentEl.innerHTML = sections.join("\n");
        }
    }

    function breakdownLabel(key) {
        const map = {
            "engine_transmission_score": "מנוע וגיר",
            "electrical_score": "מערכות חשמל ואלקטרוניקה",
            "suspension_brakes_score": "מתלים ובלמים",
            "maintenance_cost_score": "עלות אחזקה",
            "satisfaction_score": "שביעות רצון בעלי רכב",
            "recalls_score": "ריקולים ותקלות יצרן"
        };
        return map[key] || key;
    }

    function escapeHtml(value) {
        if (value === null || value === undefined) return "";
        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function fetchDetails(searchId) {
        if (!searchId) return;
        createOverlay();
        overlayContentEl.innerHTML =
            `<div class="text-[11px] text-slate-600">טוען נתוני חיפוש...</div>`;
        showOverlay();

        fetch(`/search-details/${encodeURIComponent(searchId)}`, {
            method: "GET",
            credentials: "same-origin"
        })
            .then(async (res) => {
                let data = null;
                try {
                    data = await res.json();
                } catch (e) {
                    throw new Error("שגיאה בקריאת ה-JSON מהשרת");
                }

                if (!res.ok || !data || data.error) {
                    const msg = (data && data.error) || "שגיאת שרת כללית.";
                    overlayContentEl.innerHTML =
                        `<div class="text-red-600 text-[11px]">${escapeHtml(msg)}</div>`;
                    return;
                }

                const meta = data.meta || {};
                const details = data.data || {};
                setOverlayMeta(meta);
                renderData(meta, details);
            })
            .catch((err) => {
                console.error(err);
                if (overlayContentEl) {
                    overlayContentEl.innerHTML =
                        `<div class="text-red-600 text-[11px]">שגיאה ברמת רשת/דפדפן. נסה לרענן את הדף.</div>`;
                }
            });
    }

    function bindHistoryCards() {
        const cards = document.querySelectorAll(".js-history-card[data-search-id]");
        if (!cards || !cards.length) return;

        cards.forEach((card) => {
            card.addEventListener("click", function () {
                const id = this.getAttribute("data-search-id");
                if (!id) return;
                fetchDetails(id);
            });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        bindHistoryCards();
    });
})();
