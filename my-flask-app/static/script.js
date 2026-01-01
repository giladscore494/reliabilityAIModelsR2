(() => {
  "use strict";

  // CAR_DATA comes from server-side injection (JSON)
  let CAR_DATA = {};
  try {
    if (typeof window.CAR_DATA === "string") {
      CAR_DATA = window.CAR_DATA ? JSON.parse(window.CAR_DATA) : {};
    } else if (typeof window.CAR_DATA === "object" && window.CAR_DATA) {
      CAR_DATA = window.CAR_DATA;
    }
  } catch (e) {
    CAR_DATA = {};
    console.warn("Failed to parse CAR_DATA:", e);
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
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
          headers: { "X-Requested-With": "XMLHttpRequest" },
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

  async function readJsonOrText(res) {
    const text = await res.text().catch(() => "");
    try {
      return text ? JSON.parse(text) : {};
    } catch {
      return { _raw: text };
    }
  }

  // DOM elements
  const makeSelect = document.getElementById("make");
  const modelSelect = document.getElementById("model");
  const subModelSelect = document.getElementById("sub_model");

  const yearSelect = document.getElementById("year");
  const mileageSelect = document.getElementById("mileage_range");
  const fuelSelect = document.getElementById("fuel_type");
  const transSelect = document.getElementById("transmission");

  const form = document.getElementById("analyze-form");
  const resultContainer = document.getElementById("result-container");
  const loadingOverlay = document.getElementById("loading-overlay");
  const legalCheckbox = document.getElementById("legal-checkbox");

  if (!form) return;

  function addOption(selectEl, value, label, selected = false) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    if (selected) opt.selected = true;
    selectEl.appendChild(opt);
  }

  function populateMakes() {
    if (!makeSelect) return;
    makeSelect.innerHTML = "";
    addOption(makeSelect, "", "בחר יצרן...", true);
    Object.keys(CAR_DATA).sort().forEach((make) => addOption(makeSelect, make, make));
  }

  function populateModels(make) {
    if (!modelSelect || !subModelSelect) return;

    modelSelect.innerHTML = "";
    subModelSelect.innerHTML = "";

    addOption(modelSelect, "", "בחר דגם...", true);
    addOption(subModelSelect, "", "בחר תת-דגם (אופציונלי)...", true);

    if (!make || !CAR_DATA[make]) return;

    const entry = CAR_DATA[make];

    if (Array.isArray(entry)) {
      entry.forEach((m) => addOption(modelSelect, m, m));
    } else if (typeof entry === "object") {
      Object.keys(entry).sort().forEach((m) => addOption(modelSelect, m, m));
    }
  }

  function populateSubModels(make, model) {
    if (!subModelSelect) return;

    subModelSelect.innerHTML = "";
    addOption(subModelSelect, "", "בחר תת-דגם (אופציונלי)...", true);

    if (!make || !model) return;

    const entry = CAR_DATA[make];
    if (!entry || Array.isArray(entry)) return;

    const subs = entry[model];
    if (!subs || !Array.isArray(subs)) return;

    subs.forEach((s) => addOption(subModelSelect, s, s));
  }

  function populateYears() {
    if (!yearSelect) return;
    const nowYear = new Date().getFullYear();
    yearSelect.innerHTML = "";
    addOption(yearSelect, "", "בחר שנה...", true);
    for (let y = nowYear; y >= 1990; y--) addOption(yearSelect, String(y), String(y));
  }

  function showLoading(show) {
    if (!loadingOverlay) return;
    loadingOverlay.style.display = show ? "flex" : "none";
  }

  function renderResult(data) {
    if (!resultContainer) return;
    resultContainer.innerHTML = "";

    const score = data.base_score_calculated ?? "—";
    const summary = data.reliability_summary ?? "";
    const summarySimple = data.reliability_summary_simple ?? "";
    const sourceTag = data.source_tag ?? "";
    const mileageNote = data.mileage_note ?? "";

    const card = document.createElement("div");
    card.className = "result-card";

    card.innerHTML = `
      <h2>תוצאות</h2>
      <div class="score-line">ציון בסיס: <b>${escapeHtml(score)}</b></div>
      ${sourceTag ? `<div class="source-tag">${escapeHtml(sourceTag)}</div>` : ""}
      ${mileageNote ? `<div class="mileage-note">${escapeHtml(mileageNote)}</div>` : ""}
      <hr/>
      <h3>סיכום</h3>
      <p>${escapeHtml(summary)}</p>
      <h3>סיכום פשוט</h3>
      <p>${escapeHtml(summarySimple)}</p>
    `;

    resultContainer.appendChild(card);
    resultContainer.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  let inFlight = null;

  async function handleSubmit(e) {
    e.preventDefault();

    if (legalCheckbox && !legalCheckbox.checked) {
      alert("חובה לאשר את התנאים כדי להשתמש בשירות.");
      return;
    }

    const payload = {
      make: makeSelect?.value || "",
      model: modelSelect?.value || "",
      sub_model: subModelSelect?.value || "",
      year: parseInt(yearSelect?.value || "0", 10),
      mileage_range: mileageSelect?.value || "",
      fuel_type: fuelSelect?.value || "",
      transmission: transSelect?.value || "",
    };

    if (!payload.make || !payload.model || !payload.year) {
      alert("נא למלא יצרן, דגם ושנה.");
      return;
    }

    if (inFlight) inFlight.abort();
    inFlight = new AbortController();

    showLoading(true);

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

      if (!res.ok || data.error) {
        const msg =
          data.error ||
          (res.status === 429 ? "הגעת למגבלת שימוש (429). נסה שוב מאוחר יותר / מחר." :
           res.status === 403 ? "חסימת אבטחה (403). רענן את הדף ונסה שוב." :
           "שגיאה בשרת");
        alert(msg);
        return;
      }

      renderResult(data);
    } catch (err) {
      if (String(err).includes("AbortError")) return;
      console.error(err);
      alert("שגיאה לא צפויה. נסה שוב.");
    } finally {
      showLoading(false);
    }
  }

  if (makeSelect) {
    makeSelect.addEventListener("change", () => {
      populateModels(makeSelect.value);
      populateSubModels(makeSelect.value, modelSelect?.value || "");
    });
  }

  if (modelSelect) {
    modelSelect.addEventListener("change", () => {
      populateSubModels(makeSelect?.value || "", modelSelect.value);
    });
  }

  if (!form.dataset.bound) {
    form.dataset.bound = "1";
    form.addEventListener("submit", handleSubmit);
  }

  populateMakes();
  populateModels(makeSelect?.value || "");
  populateYears();
})();
