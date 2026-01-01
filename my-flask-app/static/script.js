(() => {
  "use strict";

  // ---------------------------
  // Safe helpers
  // ---------------------------
  function escapeHtml(str) {
    return String(str ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function addOption(selectEl, value, label, selected = false) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    if (selected) opt.selected = true;
    selectEl.appendChild(opt);
  }

  function readJsonScriptTag(id) {
    const el = document.getElementById(id);
    if (!el) return {};
    const raw = (el.textContent || "").trim();
    if (!raw) return {};
    try {
      return JSON.parse(raw);
    } catch (e) {
      console.warn(`Failed to parse ${id}:`, e);
      return {};
    }
  }

  async function readJsonOrText(res) {
    const text = await res.text().catch(() => "");
    try {
      return text ? JSON.parse(text) : {};
    } catch {
      return { _raw: text };
    }
  }

  // ---------------------------
  // CSRF token helper (server-side)
  // ---------------------------
  const CSRF = {
    token: null,
    async ensure() {
      if (this.token) return this.token;
      try {
        const r = await fetch("/api/csrf", {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          headers: { "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" },
        });
        const data = await r.json().catch(() => null);
        if (r.ok && data && data.csrf_token) {
          this.token = data.csrf_token;
          return this.token;
        }
      } catch (e) {}
      return null;
    },
  };

  // ---------------------------
  // Read injected data from HTML
  // ---------------------------
  const CAR_DATA = readJsonScriptTag("car-data");
  const IS_AUTH = (() => {
    const raw = (document.getElementById("auth-data")?.textContent || "").trim();
    return raw === "true";
  })();

  // If user is not logged in, form doesn't exist anyway.
  const form = document.getElementById("car-form");
  if (!form || !IS_AUTH) return;

  // DOM elements (match your HTML)
  const makeSelect = document.getElementById("make");
  const modelSelect = document.getElementById("model");
  const yearSelect = document.getElementById("year");

  const mileageSelect = document.getElementById("mileage_range");
  const fuelSelect = document.getElementById("fuel_type");
  const transSelect = document.getElementById("transmission");

  const subModelInput = document.getElementById("sub_model"); // INPUT (text)
  const legalCheckbox = document.getElementById("legal-confirm");

  // Result DOM
  const resultsContainer = document.getElementById("results-container");
  const scoreContainer = document.getElementById("reliability-score-container");

  const summarySimpleEl = document.getElementById("summary-simple-text");
  const summaryDetailedEl = document.getElementById("summary-detailed-text");
  const summaryToggleBtn = document.getElementById("summary-toggle-btn");
  const summaryDetailedBlock = document.getElementById("summary-detailed-block");

  const faultsEl = document.getElementById("faults");
  const costsEl = document.getElementById("costs");
  const competitorsEl = document.getElementById("competitors");

  const submitBtn = document.getElementById("submit-button");
  const spinner = submitBtn?.querySelector(".spinner");
  const btnText = submitBtn?.querySelector(".button-text");

  // ---------------------------
  // Tabs (your HTML calls window.openTab)
  // ---------------------------
  window.openTab = (evt, tabId) => {
    const tabButtons = document.querySelectorAll(".tab-btn");
    const tabContents = document.querySelectorAll(".tab-content");

    tabButtons.forEach((b) => b.classList.remove("active"));
    tabContents.forEach((c) => c.classList.remove("active"));

    evt?.currentTarget?.classList.add("active");
    document.getElementById(tabId)?.classList.add("active");
  };

  // ---------------------------
  // Populate selects
  // ---------------------------
  function populateMakes() {
    if (!makeSelect) return;

    // In your HTML, makes are often rendered server-side.
    const hasServerOptions = makeSelect.options.length > 1;
    if (hasServerOptions) return;

    makeSelect.innerHTML = "";
    addOption(makeSelect, "", "Select Make...", true);
    Object.keys(CAR_DATA || {})
      .sort()
      .forEach((make) => addOption(makeSelect, make, make));
  }

  function getModelsForMake(make) {
    const entry = (CAR_DATA || {})[make];
    if (!entry) return [];
    if (Array.isArray(entry)) return entry.slice();
    if (typeof entry === "object") return Object.keys(entry);
    return [];
  }

  function getYearsForMakeModel(make, model) {
    const entry = (CAR_DATA || {})[make];
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) return null;
    const v = entry[model];

    if (Array.isArray(v) && v.length) {
      const years = v
        .map((x) => String(x).trim())
        .filter((x) => /^\d{4}$/.test(x))
        .map((x) => parseInt(x, 10))
        .filter((n) => n >= 1950 && n <= new Date().getFullYear() + 1)
        .sort((a, b) => b - a);
      return years.length ? years : null;
    }
    return null;
  }

  function populateModels(make) {
    if (!modelSelect) return;

    modelSelect.innerHTML = "";
    addOption(modelSelect, "", "-- Select Make First --", true);

    modelSelect.disabled = true;
    if (yearSelect) yearSelect.disabled = true;

    if (!make) return;

    const models = getModelsForMake(make).sort((a, b) =>
      String(a).localeCompare(String(b))
    );

    modelSelect.innerHTML = "";
    addOption(modelSelect, "", "בחר דגם...", true);
    models.forEach((m) => addOption(modelSelect, m, m));
    modelSelect.disabled = false;

    if (yearSelect) {
      yearSelect.innerHTML = "";
      addOption(yearSelect, "", "-- Select Model First --", true);
      yearSelect.disabled = true;
    }
  }

  function populateYears(make, model) {
    if (!yearSelect) return;

    yearSelect.innerHTML = "";
    addOption(yearSelect, "", "בחר שנה...", true);

    const yearsFromData = getYearsForMakeModel(make, model);
    if (yearsFromData) {
      yearsFromData.forEach((y) => addOption(yearSelect, String(y), String(y)));
      yearSelect.disabled = false;
      return;
    }

    // fallback
    const now = new Date().getFullYear();
    for (let y = now; y >= 1990; y--) addOption(yearSelect, String(y), String(y));
    yearSelect.disabled = false;
  }

  // ---------------------------
  // UI loading state
  // ---------------------------
  function setLoading(isLoading) {
    if (spinner) spinner.classList.toggle("hidden", !isLoading);
    if (btnText) btnText.style.opacity = isLoading ? "0.85" : "1";
    if (submitBtn) submitBtn.disabled = !!isLoading;
  }

  // ---------------------------
  // Render results safely
  // ---------------------------
  function renderScore(score, label = "ציון אמינות") {
    if (!scoreContainer) return;

    const s = Number(score);
    const safeScore = Number.isFinite(s) ? Math.round(s) : null;

    scoreContainer.innerHTML = `
      <div class="score-circle" style="background: rgba(99,102,241,0.35); border: 1px solid rgba(99,102,241,0.35);">
        <div style="font-size:14px; opacity:.9;">${escapeHtml(label)}</div>
        <div style="font-size:52px; line-height:1; margin-top:6px;">${safeScore ?? "—"}</div>
      </div>
    `;
  }

  function renderTextBlock(el, text) {
    if (!el) return;
    const safe = escapeHtml(text || "");
    el.innerHTML = safe.replaceAll("\n", "<br/>");
  }

  function showResults() {
    if (!resultsContainer) return;
    resultsContainer.classList.remove("hidden");
    resultsContainer.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------------------------
  // Submit
  // ---------------------------
  let inFlight = null;

  async function handleSubmit(e) {
    e.preventDefault();

    const legalError = document.getElementById("legal-error");

    if (legalCheckbox && !legalCheckbox.checked) {
      if (legalError) legalError.classList.remove("hidden");
      return;
    } else {
      if (legalError) legalError.classList.add("hidden");
    }

    const make = makeSelect?.value || "";
    const model = modelSelect?.value || "";
    const year = parseInt(yearSelect?.value || "0", 10);

    if (!make || !model || !year) {
      alert("נא למלא יצרן, דגם ושנה.");
      return;
    }

    const payload = {
      make,
      model,
      year,
      sub_model: (subModelInput?.value || "").trim(),
      mileage_range: mileageSelect?.value || "",
      fuel_type: fuelSelect?.value || "",
      transmission: transSelect?.value || "",
    };

    if (inFlight) inFlight.abort();
    inFlight = new AbortController();

    setLoading(true);

    try {
      const csrf = await CSRF.ensure();
      if (!csrf) {
        alert("שגיאת אבטחה: לא התקבל CSRF Token. רענן את הדף ונסה שוב.");
        return;
      }

      const res = await fetch("/analyze", {
        method: "POST",
        credentials: "same-origin",
        signal: inFlight.signal,
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "X-CSRFToken": csrf,
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify(payload),
      });

      if (res.status === 401) {
        window.location.href = "/login";
        return;
      }

      const data = await readJsonOrText(res);

      if (!res.ok || data?.error) {
        const msg =
          data?.error ||
          (res.status === 429
            ? "הגעת למגבלת שימוש (429). נסה שוב מאוחר יותר."
            : res.status === 403
            ? "חסימת אבטחה (403). רענן את הדף ונסה שוב."
            : "שגיאה בשרת");
        alert(msg);
        return;
      }

      const score = data.base_score_calculated ?? data.score ?? null;
      renderScore(score);

      renderTextBlock(
        summarySimpleEl,
        data.reliability_summary_simple ?? data.summary_simple ?? ""
      );
      renderTextBlock(
        summaryDetailedEl,
        data.reliability_summary ?? data.summary ?? ""
      );

      renderTextBlock(faultsEl, data.common_faults ?? data.faults ?? "");
      renderTextBlock(costsEl, data.maintenance_costs ?? data.costs ?? "");
      renderTextBlock(competitorsEl, data.competitors ?? "");

      showResults();
    } catch (err) {
      if (String(err).includes("AbortError")) return;
      console.error(err);
      alert("שגיאה לא צפויה. נסה שוב.");
    } finally {
      setLoading(false);
    }
  }

  // Toggle summary details
  if (summaryToggleBtn && summaryDetailedBlock) {
    summaryToggleBtn.addEventListener("click", () => {
      summaryDetailedBlock.classList.toggle("hidden");
      summaryToggleBtn.textContent = summaryDetailedBlock.classList.contains("hidden")
        ? "להרחבה מקצועית"
        : "להצגה מצומצמת";
    });
  }

  // Bind change listeners
  if (makeSelect) {
    makeSelect.addEventListener("change", () => populateModels(makeSelect.value));
  }

  if (modelSelect) {
    modelSelect.addEventListener("change", () =>
      populateYears(makeSelect?.value || "", modelSelect.value || "")
    );
  }

  if (!form.dataset.bound) {
    form.dataset.bound = "1";
    form.addEventListener("submit", handleSubmit);
  }

  // Init
  populateMakes();
  if (modelSelect) modelSelect.disabled = true;
  if (yearSelect) yearSelect.disabled = true;
})();
