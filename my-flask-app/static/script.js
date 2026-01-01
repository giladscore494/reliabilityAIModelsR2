(() => {
    "use strict";

    // CAR_DATA comes from server-side injection (JSON)
    let CAR_DATA = {};
    try {
        CAR_DATA = window.CAR_DATA ? JSON.parse(window.CAR_DATA) : {};
    } catch (e) {
        CAR_DATA = {};
        console.warn("Failed to parse CAR_DATA:", e);
    }

    // ---------------------------
    // CSRF token helper (server-side)
    // ---------------------------
    const CSRF = {
        token: null,
        async ensure() {
            if (this.token) return this.token;
            try {
                const r = await fetch('/api/csrf', {
                    method: 'GET',
                    credentials: 'same-origin',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
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

    async function safeJson(res) {
        try { return await res.json(); } catch (e) { return {}; }
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

    // Helper: create <option>
    function addOption(selectEl, value, label, selected = false) {
        const opt = document.createElement("option");
        opt.value = value;
        opt.textContent = label;
        if (selected) opt.selected = true;
        selectEl.appendChild(opt);
    }

    // Populate makes on load
    function populateMakes() {
        makeSelect.innerHTML = "";
        addOption(makeSelect, "", "בחר יצרן...", true);

        Object.keys(CAR_DATA).sort().forEach((make) => {
            addOption(makeSelect, make, make);
        });
    }

    // Populate models for a make
    function populateModels(make) {
        modelSelect.innerHTML = "";
        subModelSelect.innerHTML = "";

        addOption(modelSelect, "", "בחר דגם...", true);
        addOption(subModelSelect, "", "בחר תת-דגם (אופציונלי)...", true);

        if (!make || !CAR_DATA[make]) return;

        // CAR_DATA[make] can be:
        // - array of model strings
        // - object: model -> submodels array
        const entry = CAR_DATA[make];

        if (Array.isArray(entry)) {
            entry.forEach((m) => addOption(modelSelect, m, m));
        } else if (typeof entry === "object") {
            Object.keys(entry).sort().forEach((m) => addOption(modelSelect, m, m));
        }
    }

    // Populate submodels (optional)
    function populateSubModels(make, model) {
        subModelSelect.innerHTML = "";
        addOption(subModelSelect, "", "בחר תת-דגם (אופציונלי)...", true);

        if (!make || !model) return;

        const entry = CAR_DATA[make];
        if (!entry || Array.isArray(entry)) return;

        const subs = entry[model];
        if (!subs || !Array.isArray(subs)) return;

        subs.forEach((s) => addOption(subModelSelect, s, s));
    }

    // Populate years (simple)
    function populateYears() {
        const nowYear = new Date().getFullYear();
        yearSelect.innerHTML = "";
        addOption(yearSelect, "", "בחר שנה...", true);

        for (let y = nowYear; y >= 1990; y--) {
            addOption(yearSelect, String(y), String(y));
        }
    }

    function showLoading(show) {
        if (!loadingOverlay) return;
        loadingOverlay.style.display = show ? "flex" : "none";
    }

    function renderResult(data) {
        // Basic rendering (keep your existing UI if you want)
        resultContainer.innerHTML = "";

        const score = data.base_score_calculated ?? "—";
        const summary = data.reliability_summary ?? "";
        const summarySimple = data.reliability_summary_simple ?? "";
        const sourceTag = data.source_tag ?? "";

        const card = document.createElement("div");
        card.className = "result-card";

        card.innerHTML = `
      <h2>תוצאות</h2>
      <div class="score-line">ציון בסיס: <b>${score}</b></div>
      ${sourceTag ? `<div class="source-tag">${sourceTag}</div>` : ""}
      ${data.mileage_note ? `<div class="mileage-note">${data.mileage_note}</div>` : ""}
      <hr/>
      <h3>סיכום</h3>
      <p>${summary}</p>
      <h3>סיכום פשוט</h3>
      <p>${summarySimple}</p>
    `;

        resultContainer.appendChild(card);
        resultContainer.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    async function handleSubmit(e) {
        e.preventDefault();

        if (legalCheckbox && !legalCheckbox.checked) {
            alert("חובה לאשר את התנאים כדי להשתמש בשירות.");
            return;
        }

        const payload = {
            make: makeSelect.value,
            model: modelSelect.value,
            sub_model: subModelSelect.value || "",
            year: parseInt(yearSelect.value || "0", 10),
            mileage_range: mileageSelect.value,
            fuel_type: fuelSelect.value,
            transmission: transSelect.value
        };

        showLoading(true);

        try {
            const csrf = await CSRF.ensure();
            if (!csrf) {
                alert('שגיאת אבטחה: לא התקבל CSRF Token. רענן את הדף ונסה שוב.');
                return;
            }

            const res = await fetch('/analyze', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrf,
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify(payload)
            });

            if (res.status === 401) {
                window.location.href = '/login';
                return;
            }

            const data = await safeJson(res);
            if (!res.ok || data.error) {
                alert(data.error || 'שגיאה בשרת');
                return;
            }

            renderResult(data);

        } catch (err) {
            console.error(err);
            alert("שגיאה לא צפויה. נסה שוב.");
        } finally {
            showLoading(false);
        }
    }

    // Events
    makeSelect.addEventListener("change", () => {
        populateModels(makeSelect.value);
        populateSubModels(makeSelect.value, modelSelect.value);
    });

    modelSelect.addEventListener("change", () => {
        populateSubModels(makeSelect.value, modelSelect.value);
    });

    form.addEventListener("submit", handleSubmit);

    // Init
    populateMakes();
    populateModels(makeSelect.value);
    populateYears();
})();
