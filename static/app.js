/* MTC Supplies: read-only live refresh + theme + small form helpers.
 * - Polls every 15 seconds; never writes, queues, or stores personal data.
 * - Never uses browser storage for drafts; theme choice only ("mtc-theme").
 * - Shows connection status and last successful refresh; warns near expiry.
 */
(function () {
  "use strict";
  var POLL_MS = 15000;
  var lastOk = null;
  var failures = 0;

  function el(id) { return document.getElementById(id); }

  function setStatus(text, tone) {
    var s = el("connection-status");
    if (!s) return;
    s.textContent = "Connection: " + text;
    s.classList.remove("ok", "warn", "crit", "neutral");
    s.classList.add(tone || "neutral");
  }

  function setRefresh(date) {
    var r = el("last-refresh");
    if (r) r.textContent = "Last refresh: " + date.toLocaleTimeString();
  }

  function showSessionWarning(show) {
    var w = el("session-warning");
    if (w) w.hidden = !show;
  }

  function refreshLiveCounts(data) {
    if (!data) return;
    document.querySelectorAll("[data-live-low]").forEach(function (node) {
      if (typeof data.low_stock !== "undefined") node.textContent = data.low_stock;
    });
    document.querySelectorAll("[data-live-people]").forEach(function (node) {
      if (typeof data.people !== "undefined") node.textContent = data.people;
    });
    document.querySelectorAll("[data-live-items]").forEach(function (node) {
      if (typeof data.items !== "undefined") node.textContent = data.items;
    });
  }

  function refreshStockTable(data) {
    if (!data || !data.items) return;
    var availability = {};
    data.items.forEach(function (it) { availability[it.id] = it; });
    document.querySelectorAll("[data-available-for]").forEach(function (cell) {
      var id = cell.getAttribute("data-available-for");
      var info = availability[id];
      if (info) {
        if (cell.classList.contains("stock-num")) {
          cell.textContent = info.available;
          var wrap = cell.closest("[data-stock-wrap]");
          if (wrap) {
            wrap.classList.toggle("is-low", !!info.low);
          }
        } else {
          cell.textContent = info.available + (info.low ? " (low)" : "");
        }
      }
    });
    // Issue-goods dropdowns keep their own "Available: N" labels fresh too.
    if (typeof window.updateIssueAvailability === "function") {
      try { window.updateIssueAvailability(availability); } catch (e) {}
    }
  }

  async function pollOnce() {
    try {
      var dash = await fetch("/api/dashboard/", { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } });
      if (dash.status === 401 || dash.status === 403) {
        setStatus("sign-in needed", "warn");
        showSessionWarning(true);
        return;
      }
      if (!dash.ok) throw new Error("dashboard " + dash.status);
      var dj = await dash.json();
      var stock = await fetch("/api/stock/", { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } });
      if (stock.status === 401 || stock.status === 403) {
        setStatus("sign-in needed", "warn");
        showSessionWarning(true);
        return;
      }
      if (!stock.ok) throw new Error("stock " + stock.status);
      var sj = await stock.json();
      refreshLiveCounts(dj);
      refreshStockTable(sj);
      lastOk = new Date();
      failures = 0;
      setStatus("connected", "ok");
      setRefresh(lastOk);
      showSessionWarning(false);
      warnBeforeExpiry(dj.expires_in);
    } catch (err) {
      failures += 1;
      setStatus("offline — showing last saved page (retry " + failures + ")", "crit");
    }
  }

  function preventDoubleSubmit() {
    document.querySelectorAll("form[data-once]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        var btn = form.querySelector("[type=submit]");
        if (form.dataset.submitted === "1") {
          event.preventDefault();
          return false;
        }
        form.dataset.submitted = "1";
        if (btn) {
          btn.disabled = true;
          btn.textContent = btn.textContent + "…";
        }
      });
    });
  }

  var EXPIRY_WARN_SECONDS = 5 * 60;

  function warnBeforeExpiry(expiresIn) {
    if (typeof expiresIn === "number" && expiresIn <= EXPIRY_WARN_SECONDS) {
      showSessionWarning(true);
    }
  }

  function initTheme() {
    var btn = el("themeBtn");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var root = document.documentElement;
      var cur = root.getAttribute("data-theme");
      var next = cur === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("mtc-theme", next); } catch (e) {}
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initTheme();
    preventDoubleSubmit();
    pollOnce();
    setInterval(pollOnce, POLL_MS);
  });
})();
