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
    document.querySelectorAll("[data-live-today]").forEach(function (node) {
      if (typeof data.issues_today !== "undefined") node.textContent = data.issues_today;
    });
    var attention = el("home-attention");
    if (attention && data.attention_html && !attention.contains(document.activeElement)) attention.innerHTML = data.attention_html;
  }

  function refreshStockTable(data) {
    if (!data || !data.items) return;
    var availability = {};
    data.items.forEach(function (it) { availability[it.id] = it; });
    document.querySelectorAll("[data-stock-status]").forEach(function (node) {
      var info = availability[node.dataset.stockStatus];
      if (info) node.textContent = info.available === 0 ? "Out of stock" : info.low ? "Low stock" : "Available";
    });
    document.querySelectorAll("[data-net-for]").forEach(function (node) {
      var info = availability[node.dataset.netFor]; if (info) node.textContent = info.net_given;
    });
    document.querySelectorAll("[data-corrections-for]").forEach(function (node) {
      var info = availability[node.dataset.correctionsFor]; if (info) node.textContent = info.corrections_total;
    });
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
  var safeFormOwner = null;
  var uncertainSafeWrite = false;

  // Same-user recovery rotates the cookie's CSRF secret. Repair the untouched
  // shell (especially Sign out) as well as the submitted form's payload.
  window.refreshShellCsrf = function (token) {
    document.querySelectorAll('form input[name="csrfmiddlewaretoken"]').forEach(function (field) { field.value = token; });
  };

  window.addEventListener("beforeunload", function (event) {
    if (uncertainSafeWrite) { event.preventDefault(); event.returnValue = ""; }
  });

  function safeForms() {
    document.querySelectorAll("form[data-safe-form]").forEach(function (form) {
      if (form.dataset.recoveryReady) return;
      form.dataset.recoveryReady = "1";
      var pending = null;
      var busy = false;
      var status = document.createElement("p"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite"); form.prepend(status);
      function freeze(value) {
        form.querySelectorAll("input:not([type=hidden]), select, textarea").forEach(function (field) { field.disabled = value; });
      }
      form.addEventListener("submit", async function (event) {
        event.preventDefault();
        if (busy) return;
        // A whole-page response would destroy another form's in-memory retry.
        // Serialize these writes through definitive completion; only the owner
        // can retry an uncertain request, without changing its payload/key.
        if (safeFormOwner && safeFormOwner !== form) {
          status.textContent = "Resolve the pending request in the other form first. Nothing was sent from this form.";
          return;
        }
        safeFormOwner = form;
        busy = true;
        var button = form.querySelector("button[type=submit]"); button.disabled = true;
        var data = pending || new FormData(form);
        try {
          var identity = await fetch("/api/session/", {cache: "no-store", credentials: "same-origin"});
          if (!identity.ok) { status.textContent = "Sign in as the same staff member in another tab, then retry here. Your input is retained; nothing was sent."; return; }
          var security = await identity.json();
          if (security.user_id !== form.dataset.draftUser) { status.textContent = "This form belongs to a different account. Sign in as the original staff member; nothing was sent."; return; }
          window.refreshShellCsrf(security.csrf_token);
          data.set("csrfmiddlewaretoken", security.csrf_token);
          var reply, text;
          try {
            reply = await fetch(form.action, {method: "POST", body: data, credentials: "same-origin"});
            text = await reply.text();
          } catch (error) {
            pending = data; uncertainSafeWrite = true; freeze(true); status.textContent = "No reply — success is not confirmed. Retry this same request deliberately, or review the current record in another tab. Nothing is queued."; return;
          }
          if (reply.status >= 500) { pending = data; uncertainSafeWrite = true; freeze(true); status.textContent = "Server error — success is not confirmed. Retry deliberately or review the current record in another tab."; return; }
          if (reply.status === 403) { status.textContent = "Security token changed. Your input is retained; retry here to refresh it safely."; return; }
          if (reply.redirected && new URL(reply.url).pathname.startsWith("/accounts/login/")) { status.textContent = "Sign in in another tab as the original staff member, then retry here."; return; }
          if (reply.redirected && new URL(reply.url).pathname === "/admin-area/reauth/") {
            status.textContent = "Confirm your password in another tab, then retry this retained form. ";
            var link = document.createElement("a"); link.href = reply.url; link.target = "_blank"; link.rel = "noopener"; link.textContent = "Confirm identity"; status.append(link); return;
          }
          var result = new DOMParser().parseFromString(text, "text/html");
          var main = result.getElementById("main");
          if (!main) { status.textContent = "Unexpected reply. Your input is retained; review the record before retrying."; return; }
          pending = null;
          uncertainSafeWrite = false;
          document.getElementById("main").replaceChildren.apply(document.getElementById("main"), Array.from(main.childNodes).map(function (node) { return document.importNode(node, true); }));
          // Rejected action POSTs render their parent screen but are not GET
          // destinations. Keep its existing URL for reload and relative links.
          if (reply.ok && reply.redirected) history.replaceState(null, "", reply.url);
          document.title = result.title;
          safeForms(); preventDoubleSubmit();
          var errors = document.querySelector(".form-errors, .messages");
          if (errors) { errors.tabIndex = -1; errors.focus(); }
        } catch (error) { status.textContent = "Cannot check sign-in while disconnected. Nothing was sent; your input is retained."; }
        finally {
          busy = false; button.disabled = false;
          if (!pending && safeFormOwner === form) safeFormOwner = null;
        }
      });
    });
  }

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
    var menu = el("menu-toggle");
    if (menu) {
      document.documentElement.classList.add("js-menu");
      menu.addEventListener("click", function () {
        var open = document.documentElement.classList.toggle("menu-open");
        menu.setAttribute("aria-expanded", String(open));
        if (open) el("navigation").querySelector("a").focus();
      });
      document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && document.documentElement.classList.contains("menu-open")) {
          document.documentElement.classList.remove("menu-open"); menu.setAttribute("aria-expanded", "false"); menu.focus();
        }
      });
    }
    document.querySelectorAll(".navlink").forEach(function (link) {
      if (link.pathname === location.pathname) link.setAttribute("aria-current", "page");
    });
    initTheme();
    preventDoubleSubmit();
    safeForms();
    pollOnce();
    setInterval(pollOnce, POLL_MS);
  });
})();
