/* Live stock/dashboard refresh: read-only polling that never touches form drafts.
 * - Polls every 15 seconds; never writes, queues, or stores personal data.
 * - Never uses browser storage; all draft state stays in the live form (memory).
 * - Shows connection status and last successful refresh; warns near sign-in expiry.
 */
(function () {
  "use strict";
  var POLL_MS = 15000;
  var lastOk = null;
  var failures = 0;

  function el(id) { return document.getElementById(id); }

  function setStatus(text) {
    var s = el("connection-status");
    if (s) s.textContent = "Connection: " + text;
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
    // Update only elements explicitly marked for live values.
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
    // Update per-row Available cells without touching inputs or selects.
    document.querySelectorAll("[data-available-for]").forEach(function (cell) {
      var id = cell.getAttribute("data-available-for");
      var info = availability[id];
      if (info) {
        cell.textContent = info.available + (info.low ? " (low)" : "");
      }
    });
  }

  async function pollOnce() {
    try {
      var dash = await fetch("/api/dashboard/", { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } });
      if (dash.status === 401 || dash.status === 403) {
        setStatus("sign-in needed");
        showSessionWarning(true);
        return;
      }
      if (!dash.ok) throw new Error("dashboard " + dash.status);
      var dj = await dash.json();
      var stock = await fetch("/api/stock/", { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } });
      if (stock.status === 401 || stock.status === 403) {
        setStatus("sign-in needed");
        showSessionWarning(true);
        return;
      }
      if (!stock.ok) throw new Error("stock " + stock.status);
      var sj = await stock.json();
      refreshLiveCounts(dj);
      refreshStockTable(sj);
      lastOk = new Date();
      failures = 0;
      setStatus("connected");
      setRefresh(lastOk);
      showSessionWarning(false);
    } catch (err) {
      failures += 1;
      setStatus("offline — showing last saved page (retry " + failures + ")");
    }
  }

  function preventDoubleSubmit() {
    document.querySelectorAll("form[data-once]").forEach(function (form) {
      form.addEventListener("submit", function () {
        var btn = form.querySelector("[type=submit]");
        if (form.dataset.submitted === "1") {
          // Server idempotency key protects against replays; block the extra click.
          if (window.event) window.event.preventDefault();
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

  function warnBeforeExpiry() {
    // If the page has been open ~25 minutes without a full navigation,
    // remind staff that the 30-minute sign-in expiry is near. Polling alone
    // never extends the session, so this warning is honest.
    setTimeout(function () { showSessionWarning(true); }, 25 * 60 * 1000);
  }

  document.addEventListener("DOMContentLoaded", function () {
    preventDoubleSubmit();
    warnBeforeExpiry();
    pollOnce();
    setInterval(pollOnce, POLL_MS);
  });
})();
