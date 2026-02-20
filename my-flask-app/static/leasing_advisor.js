/* leasing_advisor.js – Leasing Advisor UI logic */
(function () {
  "use strict";

  // ── Helpers ──────────────────────────────────────────────
  function escapeHtml(str) {
    const d = document.createElement("div");
    d.textContent = str || "";
    return d.innerHTML;
  }

  function $(sel) { return document.querySelector(sel); }
  function $$(sel) { return document.querySelectorAll(sel); }

  // ── State ───────────────────────────────────────────────
  let currentCandidates = [];
  let currentFrame = {};
  let lastResult = null;

  // ── Explainer modal ─────────────────────────────────────
  const explainerModal = $("#explainerModal");
  const EXPLAINER_KEY = "leasing_explainer_dismissed";

  function showExplainer() {
    if (explainerModal) explainerModal.classList.remove("hidden");
  }
  function hideExplainer() {
    if (explainerModal) explainerModal.classList.add("hidden");
    try { localStorage.setItem(EXPLAINER_KEY, "1"); } catch (e) {}
  }

  // Show on first visit
  if (explainerModal) {
    try {
      if (!localStorage.getItem(EXPLAINER_KEY)) showExplainer();
    } catch (e) { showExplainer(); }
  }

  if ($("#closeExplainer")) $("#closeExplainer").addEventListener("click", hideExplainer);
  if ($("#closeExplainerBtn")) $("#closeExplainerBtn").addEventListener("click", hideExplainer);
  if ($("#helpBtn")) $("#helpBtn").addEventListener("click", function (e) {
    e.preventDefault();
    showExplainer();
  });

  // ── Legal acceptance ────────────────────────────────────
  if ($("#acceptLegalBtn")) {
    $("#acceptLegalBtn").addEventListener("click", function () {
      fetch("/api/legal/accept", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ legal_confirm: true }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            var banner = $("#legalBanner");
            if (banner) banner.remove();
            var btn = $("#recommendBtn");
            if (btn) { btn.disabled = false; }
            var warn = $("p.text-warning");
            if (warn) warn.remove();
          }
        })
        .catch(function () {});
    });
  }

  // ── Mode toggle ─────────────────────────────────────────
  var modeUpload = $("#modeUpload");
  var modeManual = $("#modeManual");
  var uploadSection = $("#uploadSection");
  var manualSection = $("#manualSection");

  function setMode(mode) {
    if (mode === "upload") {
      uploadSection.classList.remove("hidden");
      manualSection.classList.add("hidden");
      modeUpload.classList.add("border-primary", "text-primary");
      modeUpload.classList.remove("border-slate-600", "text-slate-300");
      modeManual.classList.remove("border-primary", "text-primary");
      modeManual.classList.add("border-slate-600", "text-slate-300");
    } else {
      uploadSection.classList.add("hidden");
      manualSection.classList.remove("hidden");
      modeManual.classList.add("border-primary", "text-primary");
      modeManual.classList.remove("border-slate-600", "text-slate-300");
      modeUpload.classList.remove("border-primary", "text-primary");
      modeUpload.classList.add("border-slate-600", "text-slate-300");
    }
  }

  if (modeUpload) modeUpload.addEventListener("click", function () { setMode("upload"); });
  if (modeManual) modeManual.addEventListener("click", function () { setMode("manual"); });

  // ── Fuel toggle ─────────────────────────────────────────
  var fuelToggle = $("#fuelToggle");
  var fuelSection = $("#fuelSection");
  if (fuelToggle && fuelSection) {
    fuelToggle.addEventListener("change", function () {
      fuelSection.classList.toggle("hidden", !fuelToggle.checked);
    });
  }

  // ── Compute Frame ───────────────────────────────────────
  var computeBtn = $("#computeFrameBtn");
  var frameLoading = $("#frameLoading");
  var frameError = $("#frameError");

  if (computeBtn) {
    computeBtn.addEventListener("click", function () {
      frameError.classList.add("hidden");
      frameLoading.classList.remove("hidden");
      computeBtn.disabled = true;

      var fileInput = $("#fileInput");
      var file = fileInput && fileInput.files && fileInput.files[0];
      var isUpload = !uploadSection.classList.contains("hidden") && file;

      var powertrain = ($("#powertrain") || {}).value || "unknown";
      var bodyType = ($("#bodyType") || {}).value || "";

      if (isUpload) {
        var formData = new FormData();
        formData.append("file", file);
        formData.append("powertrain", powertrain);
        formData.append("body_type", bodyType);
        var maxBikVal = ($("#maxBik") || {}).value;
        if (maxBikVal) formData.append("max_bik", maxBikVal);

        fetch("/api/leasing/frame", { method: "POST", body: formData })
          .then(handleFrameResponse)
          .catch(handleFrameError);
      } else {
        var maxBik = ($("#maxBik") || {}).value || "";
        var listPrice = ($("#listPrice") || {}).value || "";

        fetch("/api/leasing/frame", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            max_bik: maxBik || null,
            list_price: listPrice || null,
            powertrain: powertrain,
            body_type: bodyType,
          }),
        })
          .then(handleFrameResponse)
          .catch(handleFrameError);
      }
    });
  }

  function handleFrameResponse(resp) {
    frameLoading.classList.add("hidden");
    computeBtn.disabled = false;
    return resp.json().then(function (data) {
      if (data.error) {
        showFrameError(data.message || data.error);
        return;
      }
      var payload = data.data || data;
      currentCandidates = payload.candidates || [];
      currentFrame = payload.frame || {};
      showCandidates();
    });
  }

  function handleFrameError(err) {
    frameLoading.classList.add("hidden");
    computeBtn.disabled = false;
    showFrameError("שגיאת רשת: " + (err.message || ""));
  }

  function showFrameError(msg) {
    frameError.textContent = msg;
    frameError.classList.remove("hidden");
  }

  function showCandidates() {
    var step2 = $("#step2");
    step2.classList.remove("hidden");

    var summary = $("#candidatesSummary");
    summary.innerHTML = "נמצאו <strong>" + currentCandidates.length + "</strong> מועמדים";
    if (currentFrame.source === "upload") summary.innerHTML += " (מקובץ שהועלה)";

    var tbody = $("#candidatesTbody");
    tbody.innerHTML = "";
    currentCandidates.slice(0, 30).forEach(function (c) {
      var bik = c.bik ? c.bik.monthly_bik : "—";
      var tr = document.createElement("tr");
      tr.className = "border-b border-slate-800 hover:bg-slate-800/50";
      tr.innerHTML =
        '<td class="py-2 px-2">' + escapeHtml(c.make) + "</td>" +
        '<td class="py-2 px-2">' + escapeHtml(c.model) + "</td>" +
        '<td class="py-2 px-2">' + (c.list_price_ils ? c.list_price_ils.toLocaleString() + " ₪" : "—") + "</td>" +
        '<td class="py-2 px-2">' + (typeof bik === "number" ? bik.toLocaleString() + " ₪" : bik) + "</td>" +
        '<td class="py-2 px-2">' + escapeHtml(c.powertrain || "") + "</td>";
      tbody.appendChild(tr);
    });

    step2.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ── Recommend ───────────────────────────────────────────
  var recommendBtn = $("#recommendBtn");
  var recommendLoading = $("#recommendLoading");
  var recommendError = $("#recommendError");

  if (recommendBtn) {
    recommendBtn.addEventListener("click", function () {
      if (recommendBtn.disabled) return;
      recommendError.classList.add("hidden");
      recommendLoading.classList.remove("hidden");
      recommendBtn.disabled = true;

      var prefs = {};
      $$(".q-input").forEach(function (el) {
        prefs[el.name] = el.value;
      });
      if (fuelToggle && fuelToggle.checked) {
        prefs.fuel_relevant = true;
      }

      fetch("/api/leasing/recommend", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          candidates: currentCandidates,
          prefs: prefs,
          frame: currentFrame,
          legal_confirm: true,
        }),
      })
        .then(function (resp) {
          recommendLoading.classList.add("hidden");
          recommendBtn.disabled = false;
          return resp.json().then(function (data) {
            if (data.error) {
              showRecommendError(data.message || data.error);
              return;
            }
            var payload = data.data || data;
            lastResult = payload.result || payload;
            showResults(lastResult);
          });
        })
        .catch(function (err) {
          recommendLoading.classList.add("hidden");
          recommendBtn.disabled = false;
          showRecommendError("שגיאת רשת: " + (err.message || ""));
        });
    });
  }

  function showRecommendError(msg) {
    recommendError.textContent = msg;
    recommendError.classList.remove("hidden");
  }

  function showResults(result) {
    var step3 = $("#step3");
    step3.classList.remove("hidden");

    // Top 3 cards
    var cardsDiv = $("#top3Cards");
    cardsDiv.innerHTML = "";
    var top3 = result.top3 || [];
    top3.forEach(function (car, i) {
      var colors = ["from-yellow-500 to-amber-600", "from-slate-400 to-slate-500", "from-amber-700 to-amber-800"];
      var medal = ["🥇", "🥈", "🥉"];
      var card = document.createElement("div");
      card.className = "rounded-xl border border-slate-700 p-4 bg-gradient-to-br " + (colors[i] || "from-slate-700 to-slate-800") + " text-white";
      card.innerHTML =
        '<div class="text-2xl mb-2">' + (medal[i] || "") + " #" + (car.rank || i + 1) + "</div>" +
        '<div class="font-bold text-lg">' + escapeHtml(car.make) + " " + escapeHtml(car.model) + "</div>" +
        '<div class="text-sm opacity-90">' + escapeHtml(car.trim || "") + "</div>" +
        '<div class="text-sm mt-2">BIK: ' + (car.monthly_bik || "—") + " ₪</div>" +
        '<div class="text-sm mt-2">' + escapeHtml(car.reason_he || "") + "</div>";
      cardsDiv.appendChild(card);
    });

    // Full ranking table
    var rankTbody = $("#rankingTbody");
    rankTbody.innerHTML = "";
    (result.full_ranking || []).forEach(function (car) {
      var tr = document.createElement("tr");
      tr.className = "border-b border-slate-800";
      tr.innerHTML =
        '<td class="py-2 px-2">' + (car.rank || "") + "</td>" +
        '<td class="py-2 px-2">' + escapeHtml(car.make) + "</td>" +
        '<td class="py-2 px-2">' + escapeHtml(car.model) + "</td>" +
        '<td class="py-2 px-2">' + (car.score || "") + "</td>";
      rankTbody.appendChild(tr);
    });

    // Warnings
    var warningsDiv = $("#warningsDiv");
    var warnings = result.warnings || [];
    if (warnings.length > 0) {
      warningsDiv.innerHTML = "<strong>⚠️ אזהרות:</strong><ul class='list-disc mr-5 mt-1'>" +
        warnings.map(function (w) { return "<li>" + escapeHtml(w) + "</li>"; }).join("") + "</ul>";
      warningsDiv.classList.remove("hidden");
    } else {
      warningsDiv.classList.add("hidden");
    }

    step3.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ── Copy JSON ───────────────────────────────────────────
  if ($("#copyJsonBtn")) {
    $("#copyJsonBtn").addEventListener("click", function () {
      if (!lastResult) return;
      var text = JSON.stringify(lastResult, null, 2);
      navigator.clipboard.writeText(text).then(function () {
        var btn = $("#copyJsonBtn");
        btn.textContent = "✅ הועתק!";
        setTimeout(function () { btn.textContent = "📋 העתק JSON"; }, 2000);
      });
    });
  }

  // ── New Search ──────────────────────────────────────────
  if ($("#newSearchBtn")) {
    $("#newSearchBtn").addEventListener("click", function () {
      currentCandidates = [];
      currentFrame = {};
      lastResult = null;
      $("#step2").classList.add("hidden");
      $("#step3").classList.add("hidden");
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

})();
