(() => {
  "use strict";

  const form = document.getElementById("analyze-form");
  if (!form) return;

  const submitBtn = document.getElementById("submit-btn");
  const loadingEl = document.getElementById("loading");
  const errorEl = document.getElementById("error");
  const resultsEl = document.getElementById("results");

  function showLoading(on) {
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
    if (evId) extra += `\n/open: /owner/debug/events/${evId}`;

    errorEl.textContent = (msg || "שגיאה לא צפויה") + (extra ? "\n" + extra : "");
    errorEl.classList.remove("hidden");
  }

  function clearError() {
    if (!errorEl) return;
    errorEl.textContent = "";
    errorEl.classList.add("hidden");
  }

  let _csrf = null;
  async function getCsrf() {
    if (_csrf) return _csrf;
    const res = await fetch("/api/csrf", {
      credentials: "same-origin",
      cache: "no-store",
      headers: { "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" },
    });
    const data = await res.json().catch(() => ({}));
    _csrf = data.csrf_token || "";
    return _csrf;
  }

  function val(id) {
    return (document.getElementById(id)?.value || "").trim();
  }

  function buildPayload() {
    return {
      make: val("make"),
      model: val("model"),
      sub_model: val("sub_model"),
      year: Number(val("year") || 0),
      mileage_range: val("mileage_range"),
      fuel_type: val("fuel_type"),
      transmission: val("transmission"),
    };
  }

  function renderResults(data) {
    if (!resultsEl) return;
    resultsEl.textContent = "";
    resultsEl.classList.remove("hidden");

    // פשוט — הדפסה יפה של JSON (אתה כבר כנראה מעצב ב-HTML שלך)
    const pre = document.createElement("pre");
    pre.style.whiteSpace = "pre-wrap";
    pre.style.wordBreak = "break-word";
    pre.textContent = JSON.stringify(data, null, 2);
    resultsEl.appendChild(pre);
    resultsEl.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function readJsonOrText(res) {
    const text = await res.text().catch(() => "");
    try {
      return text ? JSON.parse(text) : {};
    } catch {
      return { _raw: text };
    }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearError();
    showLoading(true);

    try {
      const csrf = await getCsrf();
      if (!csrf) {
        showError("שגיאת אבטחה: לא התקבל CSRF Token. רענן את הדף ונסה שוב.");
        return;
      }

      const payload = buildPayload();

      const res = await fetch("/analyze", {
        method: "POST",
        credentials: "same-origin",
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
      const meta = {
        req_id: data.req_id || res.headers.get("X-Request-ID") || "",
        debug_event_id: data.debug_event_id || res.headers.get("X-Debug-Event-ID") || "",
      };

      if (!res.ok || data.error) {
        showError(data.error || "שגיאה בעת ניתוח אמינות.", meta);
        return;
      }

      renderResults(data);
    } catch (err) {
      console.error(err);
      showError("שגיאת רשת / שרת. נסה שוב.");
    } finally {
      showLoading(false);
    }
  });
})();
