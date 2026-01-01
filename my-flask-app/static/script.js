/* static/script.js
   Car Reliability Analyzer – client
   - CSRF fetch & cache
   - Prevent double submit
   - Safe JSON POST to /analyze
   - Friendly error handling (401/403/429/500)
*/

"use strict";

const API = {
  csrf: "/api/csrf",
  analyze: "/analyze",
};

let _csrfToken = null;
let _csrfPromise = null;

function $(sel, root = document) {
  return root.querySelector(sel);
}

function ensureResultBox() {
  let box = $("#resultBox") || $("#results") || $("#result") || $("#output");
  if (!box) {
    box = document.createElement("div");
    box.id = "resultBox";
    box.style.marginTop = "16px";
    const anchor = $("#analyzeForm") || $("form") || document.body;
    anchor.parentNode.insertBefore(box, anchor.nextSibling);
  }
  return box;
}

function setStatus(html, kind = "info") {
  const box = ensureResultBox();
  const color =
    kind === "error" ? "#b00020" :
    kind === "success" ? "#0b6b2f" :
    "#1f2a37";
  box.innerHTML = `<div style="border:1px solid #e5e7eb;border-radius:12px;padding:12px;color:${color};background:#fff">
    ${html}
  </div>`;
}

async function getCSRFToken() {
  if (_csrfToken) return _csrfToken;
  if (_csrfPromise) return _csrfPromise;

  _csrfPromise = fetch(API.csrf, {
    method: "GET",
    credentials: "same-origin",
    headers: { "Accept": "application/json" },
  })
    .then(async (r) => {
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.csrf_token) {
        throw new Error("CSRF token fetch failed");
      }
      _csrfToken = data.csrf_token;
      return _csrfToken;
    })
    .finally(() => {
      _csrfPromise = null;
    });

  return _csrfPromise;
}

function readFormPayload(form) {
  const fd = new FormData(form);

  // Use "name" attributes primarily (best practice), fallback to IDs if needed.
  const get = (name, fallbackId = null) => {
    const v = fd.get(name);
    if (v !== null && v !== undefined) return String(v).trim();
    if (fallbackId) {
      const el = document.getElementById(fallbackId);
      if (el) return String(el.value || "").trim();
    }
    return "";
  };

  const make = get("make", "make");
  const model = get("model", "model");
  const sub_model = get("sub_model", "sub_model");
  const yearRaw = get("year", "year");
  const mileage_range = get("mileage_range", "mileage_range");
  const fuel_type = get("fuel_type", "fuel_type");
  const transmission = get("transmission", "transmission");

  const year = yearRaw ? Number(yearRaw) : 0;

  return {
    make,
    model,
    sub_model,
    year,
    mileage_range,
    fuel_type,
    transmission,
  };
}

function pretty(obj) {
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return String(obj);
  }
}

let _inFlight = null;

async function postAnalyze(payload) {
  const csrf = await getCSRFToken();

  // Abort previous request (prevents spam/double submit)
  if (_inFlight && _inFlight.abort) _inFlight.abort();
  const controller = new AbortController();
  _inFlight = controller;

  const resp = await fetch(API.analyze, {
    method: "POST",
    credentials: "same-origin",
    signal: controller.signal,
    headers: {
      "Content-Type": "application/json",
      "Accept": "application/json",
      "X-CSRFToken": csrf,
    },
    body: JSON.stringify(payload),
  });

  let data = null;
  const text = await resp.text().catch(() => "");
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { _raw: text };
  }

  if (!resp.ok) {
    const msg = (data && data.error) ? data.error : `שגיאה (${resp.status})`;
    const err = new Error(msg);
    err.status = resp.status;
    err.data = data;
    throw err;
  }

  return data;
}

function renderAnalyzeResult(data) {
  // Minimal, robust rendering without assuming schema beyond "error" absence.
  const tag = data.source_tag ? `<div style="opacity:.8;margin-bottom:6px">${data.source_tag}</div>` : "";
  const note = data.mileage_note ? `<div style="margin:8px 0;padding:8px;border-radius:10px;background:#f9fafb">${data.mileage_note}</div>` : "";

  // Show key values if exist
  const base = (data.base_score_calculated !== undefined && data.base_score_calculated !== null)
    ? `<div style="font-size:20px;font-weight:700;margin:8px 0">ציון בסיס: ${data.base_score_calculated}</div>`
    : "";

  const summary = data.reliability_summary
    ? `<div style="margin-top:8px;white-space:pre-wrap">${escapeHtml(String(data.reliability_summary))}</div>`
    : "";

  const sources = Array.isArray(data.sources) && data.sources.length
    ? `<div style="margin-top:10px"><b>מקורות:</b><ul>${data.sources.map(s => `<li>${escapeHtml(String(s))}</li>`).join("")}</ul></div>`
    : "";

  const raw = `<details style="margin-top:10px"><summary>JSON מלא</summary><pre style="white-space:pre-wrap">${escapeHtml(pretty(data))}</pre></details>`;

  setStatus(`${tag}${base}${note}${summary}${sources}${raw}`, "success");
}

function escapeHtml(s) {
  return s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function wire() {
  const form = $("#analyzeForm") || $("form[data-role='analyze']") || $("form");
  if (!form) return;

  // Prevent accidental multiple binding
  if (form.dataset.bound === "1") return;
  form.dataset.bound = "1";

  const submitBtn = form.querySelector("[type='submit']");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    const payload = readFormPayload(form);

    // Basic client validation (server still enforces)
    if (!payload.make || !payload.model || !payload.year) {
      setStatus("נא למלא יצרן, דגם ושנה.", "error");
      return;
    }

    try {
      if (submitBtn) submitBtn.disabled = true;
      setStatus("מנתח…", "info");

      const data = await postAnalyze(payload);
      renderAnalyzeResult(data);

    } catch (err) {
      const st = err && err.status ? err.status : 0;

      if (st === 401) {
        setStatus('נדרש להתחבר כדי להשתמש בשירות. <a href="/login">התחברות</a>', "error");
      } else if (st === 403) {
        setStatus(err.message || "חסימת אבטחה (403).", "error");
      } else if (st === 429) {
        setStatus(err.message || "נחסמת זמנית עקב מגבלות שימוש (429). נסה שוב מאוחר יותר.", "error");
      } else if (st === 0 && String(err).includes("Abort")) {
        // user spam-clicked; ignore
        setStatus("הבקשה הקודמת בוטלה (נשלחה בקשה חדשה).", "info");
      } else {
        setStatus(err.message || "שגיאה לא צפויה. נסה שוב.", "error");
      }
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });
}

document.addEventListener("DOMContentLoaded", wire);
