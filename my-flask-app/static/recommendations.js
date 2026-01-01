(() => {
  "use strict";

  const form = document.getElementById("advisor-form");
  if (!form) return;

  const submitBtn = document.getElementById("advisor-submit-btn");
  const loadingEl = document.getElementById("advisor-loading");
  const errorEl = document.getElementById("advisor-error");

  const resultsSection = document.getElementById("advisor-results");
  const profileSummaryEl = document.getElementById("profile-summary");
  const queriesEl = document.getElementById("search-queries");
  const highlightCardsEl = document.getElementById("highlight-cards");
  const tableWrapper = document.getElementById("advisor-results-wrapper");

  const consentCheckbox = document.getElementById("consent-checkbox");

  function escapeHtml(str) {
    return String(str ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function sanitizeObject(value) {
    if (value == null) return value;
    if (Array.isArray(value)) return value.map(sanitizeObject);
    if (typeof value === "object") {
      const out = {};
      for (const [k, v] of Object.entries(value)) out[k] = sanitizeObject(v);
      return out;
    }
    if (typeof value === "string") return escapeHtml(value);
    return value;
  }

  let _csrfToken = null;
  async function getCsrfToken() {
    if (_csrfToken) return _csrfToken;
    const res = await fetch("/api/csrf", {
      credentials: "same-origin",
      cache: "no-store",
      headers: { "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" },
    });
    const data = await res.json().catch(() => ({}));
    _csrfToken = data.csrf_token || "";
    return _csrfToken;
  }

  function setSubmitting(on) {
    if (submitBtn) submitBtn.disabled = !!on;
    if (loadingEl) loadingEl.classList.toggle("hidden", !on);
  }

  function showError(msg, meta = {}) {
    if (!errorEl) return;

    const reqId = meta.req_id || "";
    const evId = meta.debug_event_id || "";

    let extra = "";
    if (reqId) extra += `\nRequest ID: ${reqId}`;
    if (evId) extra += `\nDebug Event ID: ${evId}`;

    // Owner convenience (works only if endpoint accessible)
    if (evId) extra += `\n/open: /owner/debug/events/${evId}`;

    errorEl.textContent = (msg || "שגיאה לא צפויה") + (extra ? "\n" + extra : "");
    errorEl.classList.remove("hidden");
  }

  function clearError() {
    if (!errorEl) return;
    errorEl.textContent = "";
    errorEl.classList.add("hidden");
  }

  function safeNum(x, digits = 0) {
    const n = Number(x);
    if (!Number.isFinite(n)) return "";
    return n.toFixed(digits);
  }

  function isEVFuel(fuel) {
    const f = String(fuel || "").toLowerCase();
    return f.includes("חשמלי") || f.includes("electric");
  }

  function formatPriceRange(priceRange) {
    if (!priceRange) return "";
    if (Array.isArray(priceRange) && priceRange.length >= 2) {
      const a = safeNum(priceRange[0]);
      const b = safeNum(priceRange[1]);
      return `${a}–${b}`;
    }
    return String(priceRange);
  }

  function buildPayload() {
    const getVal = (id) => (document.getElementById(id)?.value || "").trim();
    const num = (v, d = 0) => {
      const n = Number(String(v ?? "").replaceAll(",", "").trim());
      return Number.isFinite(n) ? n : d;
    };

    const budget_min = num(getVal("budget_min"), 0);
    const budget_max = num(getVal("budget_max"), 0);
    const year_min = num(getVal("year_min"), 2000);
    const year_max = num(getVal("year_max"), 2026);

    const fuels_he = Array.from(document.querySelectorAll('input[name="fuels_he"]:checked')).map((x) => x.value);
    const gears_he = Array.from(document.querySelectorAll('input[name="gears_he"]:checked')).map((x) => x.value);

    const turbo_choice_he = getVal("turbo_choice_he") || "לא משנה";

    const main_use = getVal("main_use");
    const annual_km = num(getVal("annual_km"), 15000);
    const driver_age = num(getVal("driver_age"), 21);

    const license_years = num(getVal("license_years"), 0);
    const driver_gender = getVal("driver_gender") || "זכר";

    const body_style = getVal("body_style") || "כללי";
    const driving_style = getVal("driving_style") || "רגוע ונינוח";
    const seats_choice = getVal("seats_choice") || "5";

    let excluded_colors = getVal("excluded_colors") || "";
    excluded_colors = excluded_colors
      ? excluded_colors.split(",").map((s) => s.trim()).filter(Boolean)
      : [];

    const weights = {
      reliability: num(getVal("w_reliability"), 5),
      resale: num(getVal("w_resale"), 3),
      fuel: num(getVal("w_fuel"), 4),
      performance: num(getVal("w_performance"), 2),
      comfort: num(getVal("w_comfort"), 3),
    };

    const insurance_history = getVal("insurance_history");
    const violations = getVal("violations") || "אין";

    const family_size = getVal("family_size") || "1-2";
    const cargo_need = getVal("cargo_need") || "בינוני";

    const safety_required = getVal("safety_required") || getVal("safety_required_radio") || "כן";
    const trim_level = getVal("trim_level") || "סטנדרטי";

    const consider_supply = getVal("consider_supply") || "כן";

    const fuel_price = num(getVal("fuel_price"), 7.0);
    const electricity_price = num(getVal("electricity_price"), 0.65);

    return {
      budget_min,
      budget_max,
      year_min,
      year_max,
      fuels_he,
      gears_he,
      turbo_choice_he,
      main_use,
      annual_km,
      driver_age,
      license_years,
      driver_gender,
      body_style,
      driving_style,
      seats_choice,
      excluded_colors,
      weights,
      insurance_history,
      violations,
      family_size,
      cargo_need,
      safety_required,
      trim_level,
      consider_supply,
      fuel_price,
      electricity_price,
    };
  }

  function renderProfileSummary() {
    if (!profileSummaryEl) return;
    profileSummaryEl.innerHTML = "";
    const payload = buildPayload();

    const fuels = (payload.fuels_he || []).join(", ") || "לא צוין";
    const gears = (payload.gears_he || []).join(", ") || "לא צוין";

    profileSummaryEl.innerHTML = `
      <div class="bg-slate-900/50 border border-slate-800 rounded-2xl p-3 md:p-4">
        <div class="text-xs text-slate-400 mb-2">סיכום הפרופיל שלך</div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px] md:text-xs">
          <div><span class="text-slate-400">תקציב:</span> <span class="text-slate-100 font-semibold">${safeNum(payload.budget_min)}–${safeNum(payload.budget_max)} ₪</span></div>
          <div><span class="text-slate-400">שנים:</span> <span class="text-slate-100 font-semibold">${payload.year_min}–${payload.year_max}</span></div>
          <div><span class="text-slate-400">דלק:</span> <span class="text-slate-100 font-semibold">${escapeHtml(fuels)}</span></div>
          <div><span class="text-slate-400">גיר:</span> <span class="text-slate-100 font-semibold">${escapeHtml(gears)}</span></div>
          <div><span class="text-slate-400">שימוש עיקרי:</span> <span class="text-slate-100 font-semibold">${escapeHtml(payload.main_use || "לא צוין")}</span></div>
          <div><span class="text-slate-400">ק״מ שנתי:</span> <span class="text-slate-100 font-semibold">${safeNum(payload.annual_km)} ק״מ</span></div>
        </div>
      </div>
    `;
  }

  function getReliabilityScore(car) {
    const v = Number(car?.reliability_score);
    return Number.isFinite(v) ? v : null;
  }

  function renderHighlightCards(cars) {
    if (!highlightCardsEl) return;
    highlightCardsEl.innerHTML = "";

    const byFit = [...cars].sort((a, b) => (b.fit_score || 0) - (a.fit_score || 0));
    const byAnnualCost = [...cars]
      .filter((c) => c.total_annual_cost != null)
      .sort((a, b) => (a.total_annual_cost || 0) - (b.total_annual_cost || 0));
    const byReliability = [...cars].sort((a, b) => (getReliabilityScore(b) || 0) - (getReliabilityScore(a) || 0));

    const bestFit = byFit[0] || null;
    const cheapest = byAnnualCost[0] || null;
    const mostReliable = byReliability[0] || null;

    const cards = [];

    if (bestFit) {
      cards.push({
        label: "התאמה כללית הכי גבוהה",
        badge: "המלצה ראשית",
        car: bestFit,
        chip: bestFit.fit_score != null ? `${Math.round(bestFit.fit_score)}% Fit` : "",
        text: "מבוסס על כל הפרמטרים שהזנת: תקציב, שימוש, משפחה והעדפות. זה הדגם שהכי מתאים לפרופיל הכולל שלך.",
      });
    }

    if (cheapest) {
      cards.push({
        label: "הכי זול להחזקה שנתי",
        badge: "עלות שנתית",
        car: cheapest,
        chip: cheapest.total_annual_cost != null ? `${safeNum(cheapest.total_annual_cost)} ₪ בשנה` : "",
        text: "מתוך כל הדגמים שהוצגו – זה הדגם עם העלות השנתית המוערכת הנמוכה ביותר (דלק/חשמל + תחזוקה בסיסית).",
      });
    }

    if (mostReliable && mostReliable !== bestFit) {
      const relScore = getReliabilityScore(mostReliable);
      cards.push({
        label: "הכי חזק באמינות",
        badge: "אמינות",
        car: mostReliable,
        chip: relScore != null ? `ציון אמינות ${safeNum(relScore, 1)}` : "",
        text: "דגש על מינימום תקלות לאור נתוני אמינות והיסטוריית תקלות ביחס לשאר הדגמים שהוצגו.",
      });
    }

    if (!cards.length) return;

    highlightCardsEl.innerHTML = cards
      .map((card) => {
        const title = `${card.car.brand || ""} ${card.car.model || ""}`.trim();
        const year = card.car.year || "";
        return `
          <article class="bg-slate-900/60 border border-slate-800 rounded-xl p-3 md:p-4 flex flex-col justify-between">
            <div class="flex items-center justify-between mb-2">
              <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-800 text-[10px] font-semibold text-slate-100 border border-slate-700">
                ${escapeHtml(card.badge)}
              </span>
              <span class="text-[11px] text-slate-400">${escapeHtml(card.label)}</span>
            </div>
            <div class="mb-2">
              <div class="text-sm md:text-base font-bold text-slate-100">
                ${escapeHtml(title)} ${year ? "· " + escapeHtml(year) : ""}
              </div>
              ${
                card.chip
                  ? `
                <div class="mt-1 inline-flex items-center px-2 py-0.5 rounded-full bg-primary/15 text-[11px] text-primary border border-primary/40">
                  ${escapeHtml(card.chip)}
                </div>
              `
                  : ""
              }
            </div>
            <p class="mt-1 text-[11px] md:text-xs text-slate-300 leading-relaxed">
              ${escapeHtml(card.text)}
            </p>
          </article>
        `;
      })
      .join("");
  }

  const methodLabelMap = {
    fuel_method: "שיטת חישוב צריכת דלק/חשמל",
    fee_method: "שיטת חישוב אגרת רישוי",
    reliability_method: "שיטת חישוב אמינות",
    maintenance_method: "שיטת חישוב תחזוקה",
    safety_method: "שיטת חישוב בטיחות",
    insurance_method: "שיטת חישוב ביטוח",
    resale_method: "שיטת חישוב שמירת ערך",
    performance_method: "שיטת חישוב ביצועים",
    comfort_method: "שיטת חישוב נוחות ואבזור",
    suitability_method: "שיטת חישוב התאמה לנהג",
    supply_method: "שיטת היצע בשוק",
  };

  function renderCarCard(car) {
    const title = `${car.brand || ""} ${car.model || ""}`.trim();
    const year = car.year || "";
    const fuel = car.fuel || "";
    const gear = car.gear || "";
    const turbo = car.turbo != null ? String(car.turbo) : "";

    const engineCc = car.engine_cc != null ? `${safeNum(car.engine_cc)} סמ״ק` : "";
    const priceRange = formatPriceRange(car.price_range_nis);

    const isEv = isEVFuel(fuel);
    const avgFuel =
      car.avg_fuel_consumption != null
        ? isEv
          ? `${safeNum(car.avg_fuel_consumption, 1)} קוט״ש ל-100 ק״מ`
          : `${safeNum(car.avg_fuel_consumption, 1)} ק״מ לליטר`
        : "";

    const annualFee = car.annual_fee != null ? `${safeNum(car.annual_fee)} ₪` : "";
    const reliabilityScore = car.reliability_score != null ? safeNum(car.reliability_score, 1) : "";
    const maintenanceCost = car.maintenance_cost != null ? `${safeNum(car.maintenance_cost)} ₪` : "";
    const safetyRating = car.safety_rating != null ? safeNum(car.safety_rating, 1) : "";
    const insuranceCost = car.insurance_cost != null ? `${safeNum(car.insurance_cost)} ₪` : "";
    const resaleValue = car.resale_value != null ? safeNum(car.resale_value, 1) : "";
    const performanceScore = car.performance_score != null ? safeNum(car.performance_score, 1) : "";
    const comfortFeatures = car.comfort_features != null ? safeNum(car.comfort_features, 1) : "";
    const suitability = car.suitability != null ? safeNum(car.suitability, 1) : "";
    const marketSupply = car.market_supply || "";

    const fit = car.fit_score != null ? Math.round(car.fit_score) : null;
    let fitClass = "bg-slate-800 text-slate-100";
    if (fit !== null) {
      if (fit >= 85) fitClass = "bg-emerald-500/90 text-white";
      else if (fit >= 70) fitClass = "bg-amber-500/90 text-slate-900";
      else fitClass = "bg-slate-700 text-slate-100";
    }

    const comparisonComment = car.comparison_comment || "";
    const notRecommendedReason = car.not_recommended_reason || "";

    const fuelMethod = car.fuel_method || "";
    const feeMethod = car.fee_method || "";
    const reliabilityMethod = car.reliability_method || "";
    const maintenanceMethod = car.maintenance_method || "";
    const safetyMethod = car.safety_method || "";
    const insuranceMethod = car.insurance_method || "";
    const resaleMethod = car.resale_method || "";
    const performanceMethod = car.performance_method || "";
    const comfortMethod = car.comfort_method || "";
    const suitabilityMethod = car.suitability_method || "";
    const supplyMethod = car.supply_method || "";

    return `
      <article class="bg-slate-900/70 border border-slate-800 rounded-2xl p-4 md:p-5 space-y-3">
        <div class="flex items-start justify-between gap-3">
          <div>
            <div class="text-sm md:text-base font-bold text-slate-100">
              ${title || "דגם לא ידוע"} ${year ? "· " + year : ""}
            </div>
            <div class="text-[11px] md:text-xs text-slate-400 mt-0.5">
              דלק: ${fuel || "לא צוין"} · גיר: ${gear || "לא צוין"}${turbo ? " · טורבו: " + turbo : ""}
            </div>
          </div>
          <div class="flex flex-col items-end gap-1">
            <span class="inline-flex items-center justify-center min-w-[52px] px-2 py-1 rounded-full text-[11px] font-bold ${fitClass}">
              ${fit !== null ? fit + "% Fit" : "?"}
            </span>
            ${
              marketSupply
                ? `
              <span class="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-800 text-[10px] text-slate-100 border border-slate-700">
                היצע בשוק: ${marketSupply}
              </span>
            `
                : ""
            }
          </div>
        </div>

        <div class="overflow-x-auto mt-2">
          <table class="min-w-full text-right text-[11px] md:text-xs border-separate border-spacing-y-1">
            <tbody>
              <tr><th class="px-2 py-1 font-semibold text-slate-300 w-40">מותג / דגם</th><td class="px-2 py-1 text-slate-100">${title || "-"}</td></tr>
              <tr><th class="px-2 py-1 font-semibold text-slate-300">שנה</th><td class="px-2 py-1 text-slate-100">${year || "-"}</td></tr>
              <tr><th class="px-2 py-1 font-semibold text-slate-300">נפח מנוע</th><td class="px-2 py-1 text-slate-100">${engineCc || "-"}</td></tr>
              <tr><th class="px-2 py-1 font-semibold text-slate-300">טווח מחיר משוער (₪)</th><td class="px-2 py-1 text-slate-100">${priceRange || "-"}</td></tr>

              <tr><th class="px-2 py-1 font-semibold text-slate-300">צריכת דלק/חשמל ממוצעת</th><td class="px-2 py-1 text-slate-100">${avgFuel || "-"}</td></tr>
              ${fuelMethod ? `<tr><th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.fuel_method}</th><td class="px-2 py-1 text-slate-200">${fuelMethod}</td></tr>` : ""}

              <tr><th class="px-2 py-1 font-semibold text-slate-300">אגרת רישוי שנתית (₪)</th><td class="px-2 py-1 text-slate-100">${annualFee || "-"}</td></tr>
              ${feeMethod ? `<tr><th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.fee_method}</th><td class="px-2 py-1 text-slate-200">${feeMethod}</td></tr>` : ""}

              <tr><th class="px-2 py-1 font-semibold text-slate-300">ציון אמינות (1–10)</th><td class="px-2 py-1 text-slate-100">${reliabilityScore || "-"}</td></tr>
              ${reliabilityMethod ? `<tr><th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.reliability_method}</th><td class="px-2 py-1 text-slate-200">${reliabilityMethod}</td></tr>` : ""}

              <tr><th class="px-2 py-1 font-semibold text-slate-300">עלות אחזקה שנתית (₪)</th><td class="px-2 py-1 text-slate-100">${maintenanceCost || "-"}</td></tr>
              ${maintenanceMethod ? `<tr><th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.maintenance_method}</th><td class="px-2 py-1 text-slate-200">${maintenanceMethod}</td></tr>` : ""}

              <tr><th class="px-2 py-1 font-semibold text-slate-300">ציון בטיחות (1–10)</th><td class="px-2 py-1 text-slate-100">${safetyRating || "-"}</td></tr>
              ${safetyMethod ? `<tr><th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.safety_method}</th><td class="px-2 py-1 text-slate-200">${safetyMethod}</td></tr>` : ""}

              <tr><th class="px-2 py-1 font-semibold text-slate-300">עלות ביטוח שנתית (₪)</th><td class="px-2 py-1 text-slate-100">${insuranceCost || "-"}</td></tr>
              ${insuranceMethod ? `<tr><th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.insurance_method}</th><td class="px-2 py-1 text-slate-200">${insuranceMethod}</td></tr>` : ""}

              <tr><th class="px-2 py-1 font-semibold text-slate-300">שמירת ערך (1–10)</th><td class="px-2 py-1 text-slate-100">${resaleValue || "-"}</td></tr>
              ${resaleMethod ? `<tr><th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.resale_method}</th><td class="px-2 py-1 text-slate-200">${resaleMethod}</td></tr>` : ""}

              <tr><th class="px-2 py-1 font-semibold text-slate-300">ביצועים (1–10)</th><td class="px-2 py-1 text-slate-100">${performanceScore || "-"}</td></tr>
              ${performanceMethod ? `<tr><th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.performance_method}</th><td class="px-2 py-1 text-slate-200">${performanceMethod}</td></tr>` : ""}

              <tr><th class="px-2 py-1 font-semibold text-slate-300">נוחות ואבזור (1–10)</th><td class="px-2 py-1 text-slate-100">${comfortFeatures || "-"}</td></tr>
              ${comfortMethod ? `<tr><th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.comfort_method}</th><td class="px-2 py-1 text-slate-200">${comfortMethod}</td></tr>` : ""}

              <tr><th class="px-2 py-1 font-semibold text-slate-300">התאמה לנהג (1–10)</th><td class="px-2 py-1 text-slate-100">${suitability || "-"}</td></tr>
              ${suitabilityMethod ? `<tr><th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.suitability_method}</th><td class="px-2 py-1 text-slate-200">${suitabilityMethod}</td></tr>` : ""}

              ${supplyMethod ? `<tr><th class="px-2 py-1 font-semibold text-slate-300">${methodLabelMap.supply_method}</th><td class="px-2 py-1 text-slate-200">${supplyMethod}</td></tr>` : ""}
            </tbody>
          </table>
        </div>

        ${
          comparisonComment
            ? `
          <div class="mt-2 text-[11px] md:text-xs text-slate-300 leading-relaxed">
            <span class="font-semibold text-slate-100">הסבר כללי:</span><br>${comparisonComment}
          </div>
        `
            : ""
        }

        ${
          notRecommendedReason
            ? `
          <div class="mt-2 text-[11px] md:text-xs text-red-300 leading-relaxed border border-red-500/40 bg-red-900/20 rounded-xl px-3 py-2">
            <span class="font-semibold">סיבה לאי-המלצה/הסתייגות:</span><br>${notRecommendedReason}
          </div>
        `
            : ""
        }
      </article>
    `;
  }

  function renderResults(dataRaw) {
    if (!resultsSection || !tableWrapper) return;

    const data = sanitizeObject(dataRaw || {});
    const queries = Array.isArray(data.search_queries) ? data.search_queries : [];

    if (queriesEl) {
      if (queries.length) {
        queriesEl.innerHTML = `
          <div class="text-[11px] text-slate-400">
            <span class="font-semibold text-slate-300">שאילתות חיפוש שבוצעו:</span>
            <ul class="mt-1 space-y-0.5">
              ${queries.map((q) => `<li>• ${q}</li>`).join("")}
            </ul>
          </div>
        `;
      } else {
        queriesEl.textContent = "";
      }
    }

    const cars = Array.isArray(data.recommended_cars) ? data.recommended_cars : [];
    if (!cars.length) {
      if (profileSummaryEl) profileSummaryEl.innerHTML = "";
      if (highlightCardsEl) highlightCardsEl.innerHTML = "";
      tableWrapper.innerHTML = '<p class="text-sm text-slate-400">לא התקבלו המלצות. ייתכן שהגבלות התקציב/שנים קשיחות מדי.</p>';
      resultsSection.classList.remove("hidden");
      resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }

    renderProfileSummary();
    renderHighlightCards(cars);

    cars.sort((a, b) => (b.fit_score || 0) - (a.fit_score || 0));
    const cardsHtml = cars.map((car) => renderCarCard(car)).join("");

    tableWrapper.innerHTML = `
      <div class="mb-2 text-[11px] text-slate-400">
        לכל רכב מוצגת כרטיסייה נפרדת עם כל הפרמטרים, כולל השיטות שבהן חושבו הנתונים.
      </div>
      <div class="space-y-4">${cardsHtml}</div>
    `;

    resultsSection.classList.remove("hidden");
    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  let inFlight = null;

  async function readJsonOrText(res) {
    const text = await res.text().catch(() => "");
    try {
      return text ? JSON.parse(text) : {};
    } catch {
      return { _raw: text };
    }
  }

  async function handleSubmit(e) {
    e.preventDefault();
    clearError();

    if (consentCheckbox && !consentCheckbox.checked) {
      showError("יש לאשר שאתה מעל גיל 18 ומסכים לתקנון ולמדיניות הפרטיות לפני הפעלת מנוע ההמלצות.");
      return;
    }

    const payload = buildPayload();

    if (!payload.budget_max || payload.budget_max <= 0 || payload.budget_min > payload.budget_max) {
      showError("בדוק שהתקציב המינימלי קטן מהתקציב המקסימלי ושערכי התקציב תקינים.");
      return;
    }

    if (inFlight) inFlight.abort();
    inFlight = new AbortController();

    setSubmitting(true);

    try {
      const csrfToken = await getCsrfToken();
      if (!csrfToken) {
        showError("שגיאת אבטחה: לא התקבל CSRF Token. רענן את הדף ונסה שוב.");
        return;
      }

      const res = await fetch("/advisor_api", {
        method: "POST",
        credentials: "same-origin",
        signal: inFlight.signal,
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "X-CSRFToken": csrfToken,
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify(payload),
      });

      if (res.status === 401) {
        window.location.href = "/login";
        return;
      }

      const data = await readJsonOrText(res);
      const meta = {
        req_id: data.req_id || res.headers.get("X-Request-ID") || "",
        debug_event_id: data.debug_event_id || res.headers.get("X-Debug-Event-ID") || "",
      };

      if (!res.ok || data.error) {
        if (res.status === 429) {
          showError(data.error || "הגעת למגבלת שימוש (429). נסה שוב מאוחר יותר / מחר.", meta);
        } else if (res.status === 403) {
          showError(data.error || "חסימת אבטחה (403). רענן את הדף ונסה שוב.", meta);
        } else {
          showError(data.error || "שגיאת שרת בעת הפעלת מנוע ההמלצות.", meta);
        }
        return;
      }

      renderResults(data);
    } catch (err) {
      if (String(err).includes("AbortError")) return;
      console.error(err);
      showError("שגיאה כללית בחיבור לשרת. נסה שוב מאוחר יותר.");
    } finally {
      setSubmitting(false);
    }
  }

  if (!form.dataset.bound) {
    form.dataset.bound = "1";
    form.addEventListener("submit", handleSubmit);
  }
})();
