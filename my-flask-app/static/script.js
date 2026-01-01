/* global window, document, fetch, navigator */
(() => {
  "use strict";

  // -----------------------------
  // CSRF helper (cached)
  // -----------------------------
  let _csrfToken = null;

  async function getCsrfToken() {
    if (_csrfToken) return _csrfToken;
    const r = await fetch("/api/csrf", { headers: { "Accept": "application/json" } });
    const j = await r.json().catch(() => ({}));
    _csrfToken = j.csrf_token || j.token || null;
    return _csrfToken;
  }

  async function postJSON(url, payload, extraHeaders = {}) {
    const token = await getCsrfToken();
    const requestId = cryptoRandomId();
    const headers = Object.assign(
      {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-CSRFToken": token || "",
        "X-Request-Id": requestId,
      },
      extraHeaders
    );

    const res = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(payload || {}),
    });

    let bodyText = null;
    let bodyJson = null;
    const ct = (res.headers.get("content-type") || "").toLowerCase();

    if (ct.includes("application/json")) {
      bodyJson = await res.json().catch(() => null);
    } else {
      bodyText = await res.text().catch(() => null);
    }

    return {
      ok: res.ok,
      status: res.status,
      requestId: res.headers.get("X-Request-Id") || requestId,
      json: bodyJson,
      text: bodyText,
    };
  }

  function cryptoRandomId() {
    try {
      if (window.crypto && crypto.getRandomValues) {
        const a = new Uint32Array(2);
        crypto.getRandomValues(a);
        return (a[0].toString(16) + a[1].toString(16)).slice(0, 12);
      }
    } catch (_) {}
    return Math.random().toString(16).slice(2, 14);
  }

  // -----------------------------
  // Client error reporting
  // -----------------------------
  function sendClientError(payload) {
    const data = Object.assign({}, payload, {
      url: window.location.href,
      ts: new Date().toISOString(),
    });

    try {
      const blob = new Blob([JSON.stringify(data)], { type: "application/json" });
      if (navigator.sendBeacon) {
        navigator.sendBeacon("/api/client-error", blob);
        return;
      }
    } catch (_) {}

    fetch("/api/client-error", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json", "X-Client-Path": window.location.pathname },
      body: JSON.stringify(data),
    }).catch(() => {});
  }

  window.addEventListener("error", (e) => {
    sendClientError({
      type: "WindowError",
      message: e.message || "Script error",
      name: "Error",
      stack: (e.error && e.error.stack) ? String(e.error.stack) : null,
      file: e.filename,
      line: e.lineno,
      col: e.colno,
    });
  });

  window.addEventListener("unhandledrejection", (e) => {
    const reason = e.reason || {};
    sendClientError({
      type: "UnhandledRejection",
      message: (reason && reason.message) ? String(reason.message) : String(reason),
      name: (reason && reason.name) ? String(reason.name) : "PromiseRejection",
      stack: (reason && reason.stack) ? String(reason.stack) : null,
    });
  });

  // -----------------------------
  // History click fix:
  // - מונע refresh של <form> / <a> ומנסה למשוך פרטים
  // - אם אין endpoint JSON, נופל לניווט רגיל /search-details/<id>
  // -----------------------------
  async function tryOpenHistoryDetails(id) {
    // נסה JSON endpoint (אם קיים אצלך)
    const candidates = [
      `/api/history/${encodeURIComponent(id)}`,
      `/api/search/${encodeURIComponent(id)}`,
      `/search-details/${encodeURIComponent(id)}` // לפעמים מחזיר HTML, זה fallback
    ];

    for (const url of candidates) {
      try {
        const r = await fetch(url, { headers: { "Accept": "application/json" } });
        const ct = (r.headers.get("content-type") || "").toLowerCase();

        if (r.ok && ct.includes("application/json")) {
          const j = await r.json().catch(() => null);
          if (j) {
            showModalJson(`Search #${id}`, j);
            return;
          }
        }

        // אם זה HTML או 404 — ממשיכים
      } catch (err) {
        // נרשום לקוחית
        sendClientError({ type: "HistoryFetchError", message: String(err), name: "HistoryFetchError", stack: err && err.stack ? String(err.stack) : null });
      }
    }

    // fallback: ניווט רגיל
    window.location.href = `/search-details/${encodeURIComponent(id)}`;
  }

  function showModalJson(title, obj) {
    const overlay = document.createElement("div");
    overlay.style.position = "fixed";
    overlay.style.inset = "0";
    overlay.style.zIndex = "99999";
    overlay.style.background = "rgba(0,0,0,0.65)";
    overlay.style.display = "flex";
    overlay.style.alignItems = "center";
    overlay.style.justifyContent = "center";
    overlay.style.padding = "20px";

    const card = document.createElement("div");
    card.style.maxWidth = "900px";
    card.style.width = "100%";
    card.style.maxHeight = "80vh";
    card.style.overflow = "auto";
    card.style.background = "#111";
    card.style.color = "#fff";
    card.style.borderRadius = "14px";
    card.style.padding = "14px";
    card.style.boxShadow = "0 10px 40px rgba(0,0,0,0.5)";

    const h = document.createElement("div");
    h.style.display = "flex";
    h.style.justifyContent = "space-between";
    h.style.alignItems = "center";
    h.style.gap = "10px";

    const t = document.createElement("div");
    t.textContent = title;
    t.style.fontWeight = "700";

    const x = document.createElement("button");
    x.textContent = "✕";
    x.type = "button";
    x.style.cursor = "pointer";
    x.style.border = "0";
    x.style.background = "transparent";
    x.style.color = "#fff";
    x.style.fontSize = "18px";
    x.onclick = () => overlay.remove();

    h.appendChild(t);
    h.appendChild(x);

    const pre = document.createElement("pre");
    pre.style.whiteSpace = "pre-wrap";
    pre.style.wordBreak = "break-word";
    pre.style.fontSize = "12px";
    pre.style.marginTop = "10px";
    pre.textContent = JSON.stringify(obj, null, 2);

    card.appendChild(h);
    card.appendChild(pre);
    overlay.appendChild(card);

    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.remove();
    });

    document.body.appendChild(overlay);
  }

  // Event delegation: כל אלמנט עם data-history-id
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-history-id]");
    if (!el) return;

    // חשוב: מונע refresh
    e.preventDefault();
    e.stopPropagation();

    const id = el.getAttribute("data-history-id");
    if (!id) return;
    tryOpenHistoryDetails(id);
  });

  // אופציונלי: אם יש לך כפתורים בתוך form, תן להם type=button אוטומטי
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("button[data-history-id]").forEach((btn) => {
      try { btn.type = "button"; } catch (_) {}
    });
  });

})();
