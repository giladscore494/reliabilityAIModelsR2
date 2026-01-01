// static/script.js
(() => {
  "use strict";

  const SEL = {
    form: "#reliabilityForm",
    btn: "#analyzeBtn",
    results: "#resultsBox",
    error: "#errorBox",
    loading: "#loadingBox",

    make: "#make",
    model: "#model",
    subModel: "#sub_model",
    year: "#year",
    mileage: "#mileage_range",
    fuel: "#fuel_type",
    trans: "#transmission",
  };

  let CSRF_TOKEN = null;

  function $(q) { return document.querySelector(q); }

  function setVisible(el, show) {
    if (!el) return;
    el.style.display = show ? "" : "none";
  }

  function setText(el, text) {
    if (!el) return;
    el.textContent = text ?? "";
  }

  async function fetchCsrfToken() {
    if (CSRF_TOKEN) return CSRF_TOKEN;
    const r = await fetch("/api/csrf", { method: "GET", credentials: "same-origin" });
    const j = await r.json();
    CSRF_TOKEN = j.csrf_token || null;
    return CSRF_TOKEN;
  }

  function clearUI() {
    const results = $(SEL.results);
    const error = $(SEL.error);
    if (results) results.innerHTML = "";
    setText(error, "");
    setVisible(error, false);
  }

  function showError(msg) {
    const error = $(SEL.error);
    setText(error, msg || "שגיאה לא ידועה");
    setVisible(error, true);
  }

  function safeNumber(x) {
    const n = Number(x);
    return Number.isFinite(n) ? n : null;
  }

  function el(tag, cls) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    return node;
  }

  function renderResult(data) {
    const box = $(SEL.results);
    if (!box) return;

    const title = el("h3");
    title.textContent = "תוצאות ניתוח אמינות";
    box.appendChild(title);

    // Score
    const score = safeNumber(data.base_score_calculated);
    const scoreLine = el("div");
    scoreLine.textContent = (score !== null)
      ? `ציון בסיס: ${score}/100`
      : `ציון בסיס: לא זמין`;
    box.appendChild(scoreLine);

    // Source tag / mileage note
    if (data.source_tag) {
      const st = el("div");
      st.textContent = String(data.source_tag);
      box.appendChild(st);
    }
    if (data.mileage_note) {
      const mn = el("div");
      mn.textContent = String(data.mileage_note);
      box.appendChild(mn);
    }

    // Breakdown
    if (data.score_breakdown && typeof data.score_breakdown === "object") {
      const h = el("h4");
      h.textContent = "פירוט ציונים";
      box.appendChild(h);

      const ul = el("ul");
      const map = {
        engine_transmission_score: "מנוע/גיר",
        electrical_score: "חשמל",
        suspension_brakes_score: "מתלים/בלמים",
        maintenance_cost_score: "עלות תחזוקה",
        satisfaction_score: "שביעות רצון",
        recalls_score: "ריקולים",
      };

      for (const [k, label] of Object.entries(map)) {
        if (k in data.score_breakdown) {
          const li = el("li");
          li.textContent = `${label}: ${String(data.score_breakdown[k])}`;
          ul.appendChild(li);
        }
      }
      box.appendChild(ul);
    }

    // Summary
    if (data.reliability_summary) {
      const h = el("h4");
      h.textContent = "סיכום מקצועי";
      box.appendChild(h);

      const p = el("p");
      p.textContent = String(data.reliability_summary);
      box.appendChild(p);
    }

    if (data.reliability_summary_simple) {
      const h = el("h4");
      h.textContent = "סיכום קצר";
      box.appendChild(h);

      const p = el("p");
      p.textContent = String(data.reliability_summary_simple);
      box.appendChild(p);
    }

    // Common issues
    if (Array.isArray(data.common_issues) && data.common_issues.length) {
      const h = el("h4");
      h.textContent = "תקלות נפוצות";
      box.appendChild(h);

      const ul = el("ul");
      data.common_issues.slice(0, 12).forEach((x) => {
        const li = el("li");
        li.textContent = String(x);
        ul.appendChild(li);
      });
      box.appendChild(ul);
    }

    // Issues with costs
    if (Array.isArray(data.issues_with_costs) && data.issues_with_costs.length) {
      const h = el("h4");
      h.textContent = "תקלות + עלויות";
      box.appendChild(h);

      const table = el("table");
      const thead = el("thead");
      const trh = el("tr");
      ["תקלה", "עלות ממוצעת (₪)", "חומרה", "מקור"].forEach((t) => {
        const th = el("th");
        th.textContent = t;
        trh.appendChild(th);
      });
      thead.appendChild(trh);
      table.appendChild(thead);

      const tbody = el("tbody");
      data.issues_with_costs.slice(0, 12).forEach((row) => {
        if (!row || typeof row !== "object") return;
        const tr = el("tr");

        const td1 = el("td"); td1.textContent = String(row.issue ?? "");
        const td2 = el("td"); td2.textContent = String(row.avg_cost_ILS ?? "");
        const td3 = el("td"); td3.textContent = String(row.severity ?? "");
        const td4 = el("td"); td4.textContent = String(row.source ?? "");

        tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(td3); tr.appendChild(td4);
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      box.appendChild(table);
    }

    // Recommended checks
    if (Array.isArray(data.recommended_checks) && data.recommended_checks.length) {
      const h = el("h4");
      h.textContent = "בדיקות מומלצות";
      box.appendChild(h);

      const ul = el("ul");
      data.recommended_checks.slice(0, 12).forEach((x) => {
        const li = el("li");
        li.textContent = String(x);
        ul.appendChild(li);
      });
      box.appendChild(ul);
    }

    // Competitors
    if (Array.isArray(data.common_competitors_brief) && data.common_competitors_brief.length) {
      const h = el("h4");
      h.textContent = "מתחרים נפוצים (בקצרה)";
      box.appendChild(h);

      const ul = el("ul");
      data.common_competitors_brief.slice(0, 8).forEach((c) => {
        if (!c || typeof c !== "object") return;
        const li = el("li");
        li.textContent = `${String(c.model ?? "")}: ${String(c.brief_summary ?? "")}`;
        ul.appendChild(li);
      });
      box.appendChild(ul);
    }

    // Sources
    if (Array.isArray(data.sources) && data.sources.length) {
      const h = el("h4");
      h.textContent = "מקורות";
      box.appendChild(h);

      const ul = el("ul");
      data.sources.slice(0, 10).forEach((x) => {
        const li = el("li");
        li.textContent = String(x);
        ul.appendChild(li);
      });
      box.appendChild(ul);
    }
  }

  async function submitAnalyze(e) {
    e.preventDefault();
    clearUI();

    const btn = $(SEL.btn);
    const loading = $(SEL.loading);

    setVisible(loading, true);
    if (btn) btn.disabled = true;

    try {
      const csrf = await fetchCsrfToken();
      if (!csrf) throw new Error("CSRF token missing");

      const payload = {
        make: ($(SEL.make)?.value || "").trim(),
        model: ($(SEL.model)?.value || "").trim(),
        sub_model: ($(SEL.subModel)?.value || "").trim(),
        year: Number(($(SEL.year)?.value || "").trim()),
        mileage_range: ($(SEL.mileage)?.value || "").trim(),
        fuel_type: ($(SEL.fuel)?.value || "").trim(),
        transmission: ($(SEL.trans)?.value || "").trim(),
      };

      const r = await fetch("/analyze", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrf,
        },
        body: JSON.stringify(payload),
      });

      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        showError(data.error || `שגיאה (${r.status})`);
        return;
      }
      renderResult(data);
    } catch (err) {
      showError(err?.message || "שגיאה לא ידועה");
    } finally {
      setVisible(loading, false);
      if (btn) btn.disabled = false;
    }
  }

  function init() {
    const form = $(SEL.form);
    if (!form) return;

    // preload csrf (reduces first-click failures)
    fetchCsrfToken().catch(() => {});

    form.addEventListener("submit", submitAnalyze);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
