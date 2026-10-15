/* Local finder and panel primitives. No persistent data or automatic writes. */
(function () {
  "use strict";
  document.addEventListener("click", function(event) {
    if (event.target.id === "copy-low-stock") {
      var message = document.getElementById("low-stock-message"), feedback = document.getElementById("copy-feedback");
      if (navigator.clipboard) navigator.clipboard.writeText(message.value).then(function() {feedback.textContent = "Copied all matching items.";}).catch(function() {message.focus();message.select();feedback.textContent = "Select and copy the message above.";});
      else {message.focus();message.select();feedback.textContent = "Select and copy the message above.";}
    }
    var form = document.getElementById("batch-form"); if (!form) return;
    var rows = form.querySelector("#batch-rows"), count = form.querySelector("[name=form-TOTAL_FORMS]");
    if (event.target.id === "batch-add") {
      if (Number(count.value) >= 50) { window.alert("A batch can contain at most 50 items."); return; }
      var html = form.querySelector("#batch-empty").innerHTML.replaceAll("__prefix__", count.value);
      rows.insertAdjacentHTML("beforeend", html); count.value = Number(count.value) + 1;
      var row = rows.lastElementChild;
      row.querySelector("[name$=-row_key]").value = crypto.randomUUID();
      row.querySelector("input:not([type=hidden])").focus();
    }
    if (event.target.classList.contains("batch-remove")) {
      event.target.closest("fieldset").remove();
      Array.from(rows.children).forEach(function(row, index) {
        row.querySelectorAll("[name],[id],[for]").forEach(function(node) {
          ["name","id","for"].forEach(function(attr) { if (node.hasAttribute(attr)) node.setAttribute(attr, node.getAttribute(attr).replace(/form-\d+-/g, "form-" + index + "-")); });
        });
      }); count.value = rows.children.length;
    }
  });
  var batch = document.getElementById("batch-form"), batchDirty = false;
  if (batch) {
    batch.addEventListener("input", function() { batchDirty = true; });
    batch.addEventListener("submit", function() { batchDirty = false; });
    window.addEventListener("beforeunload", function(event) { if (batchDirty) { event.preventDefault(); event.returnValue = ""; } });
  }
  document.querySelectorAll("[data-finder]").forEach(function (root) {
    var input = root.querySelector("input[role=combobox]"), popup = root.querySelector("[role=listbox]");
    var status = root.querySelector(".finder-status"), timer, serial = 0, active = -1, results = [];
    function close() { popup.hidden = true; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); active = -1; }
    function highlight(index) {
      active = index;
      popup.querySelectorAll("[role=option]").forEach(function (node, i) { node.setAttribute("aria-selected", String(i === active)); });
      if (active >= 0) { input.setAttribute("aria-activedescendant", popup.id + "-" + active); popup.children[active].scrollIntoView({block: "nearest"}); }
    }
    function select(index) {
      var result = results[index]; if (!result) return;
      if (root.dataset.finder === "all") { window.location.assign(result.url); return; }
      root.dispatchEvent(new CustomEvent("finder-select", {detail: result})); close();
    }
    async function lookup() {
      var ticket = ++serial; results = []; active = -1; popup.replaceChildren();
      input.removeAttribute("aria-activedescendant");
      if (!input.value.trim()) { close(); status.textContent = ""; return; }
      status.textContent = "Searching…"; popup.hidden = false; input.setAttribute("aria-expanded", "true");
      popup.textContent = "Searching…";
      try {
        var reply = await fetch("/api/find/?scope=" + root.dataset.finder + "&q=" + encodeURIComponent(input.value), {cache:"no-store", credentials:"same-origin"});
        if (ticket !== serial) return;
        if (!reply.ok) throw new Error(reply.status === 401 || reply.status === 403 ? "Sign in again to search." : "Search unavailable. Try again.");
        var data = await reply.json(); if (ticket !== serial) return;
        results = data.results; popup.replaceChildren();
        results.forEach(function (result, i) {
          var option = document.createElement("div"); option.id = popup.id + "-" + i; option.setAttribute("role", "option"); option.setAttribute("aria-selected", "false");
          var label = document.createElement("strong"); label.textContent = result.label;
          var detail = document.createElement("span"); detail.textContent = result.group + " · " + result.detail;
          option.append(label, detail); option.addEventListener("mousedown", function(e) {e.preventDefault();});
          option.addEventListener("click", function () { select(i); }); popup.append(option);
        });
        status.textContent = results.length ? results.length + " results. Use arrow keys to choose." : "No matches.";
        if (!results.length) popup.textContent = "No matches. Try a name or ID.";
      } catch (error) { if (ticket === serial) { popup.textContent = error.message; status.textContent = error.message; } }
    }
    input.addEventListener("input", function () { ++serial; close(); results = []; popup.replaceChildren(); clearTimeout(timer); timer = setTimeout(lookup, 180); });
    input.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { event.preventDefault(); ++serial; clearTimeout(timer); close(); }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault(); if (popup.hidden) lookup();
        else if (results.length) highlight((active + (event.key === "ArrowDown" ? 1 : -1) + results.length) % results.length);
      }
      if (event.key === "Enter" && !popup.hidden) { event.preventDefault(); if (active >= 0) select(active); }
    });
    input.addEventListener("blur", function () { ++serial; clearTimeout(timer); close(); });
    root.addEventListener("finder-clear", function () { ++serial; close(); results = []; popup.replaceChildren(); input.value = ""; });
  });

  // A panel is a bound server page in a same-origin frame. This deliberately
  // reuses the page-owned safe-form serializer instead of inventing a write path.
  var dialog = document.createElement("dialog"); dialog.className = "side-dialog";
  var closeButton = document.createElement("button"); closeButton.type = "button"; closeButton.textContent = "Close";
  var frame = document.createElement("iframe"); frame.title = "Edit inventory";
  dialog.append(closeButton, frame); document.body.append(dialog);
  var opener, originalUrl;
  window.addEventListener("beforeunload", function(event) {
    if (dialog.open && (frame.contentWindow.panelDirty || frame.contentWindow.panelPending)) { event.preventDefault(); event.returnValue = ""; }
  });
  function closePanel(event) {
    if (event) event.preventDefault();
    var child = frame.contentWindow;
    if (child.panelPending) { child.alert("Resolve the pending request before closing. Success is not confirmed."); return; }
    if (child.panelDirty && !window.confirm("Discard unsaved changes?")) return;
    dialog.close(); frame.src = "about:blank"; if (opener) opener.focus();
  }
  closeButton.addEventListener("click", closePanel); dialog.addEventListener("cancel", closePanel);
  document.addEventListener("click", function (event) {
    var link = event.target.closest("a[data-panel]"); if (!link || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (window.safeWritePending) { event.preventDefault(); window.alert("Resolve the pending request before opening another form."); return; }
    event.preventDefault(); opener = link; originalUrl = location.href;
    frame.src = link.href + (link.search ? "&" : "?") + "panel=1";
    dialog.showModal(); closeButton.focus();
  });
  frame.addEventListener("load", function () {
    if (!dialog.open || !frame.contentDocument) return;
    var child = frame.contentWindow, doc = frame.contentDocument;
    doc.documentElement.classList.add("in-panel");
    var form = doc.querySelector("main form[data-safe-form]");
    if (form) {
      doc.addEventListener("input", function () { child.panelDirty = true; });
      form.querySelector("input:not([type=hidden]),select,textarea")?.focus();
    }
    child.addEventListener("safe-form-complete", async function (event) {
      if (event.detail.saved) {
        child.panelDirty = false;
        try {
          var reply = await fetch(originalUrl, {credentials:"same-origin", cache:"no-store"});
          if (!reply.ok || reply.redirected) throw new Error("Refresh required");
          var result = new DOMParser().parseFromString(await reply.text(), "text/html");
          var top = scrollY, href = opener.getAttribute("href");
          document.getElementById("main").replaceChildren.apply(document.getElementById("main"), Array.from(result.getElementById("main").childNodes).map(function(node) {return document.importNode(node, true);}));
          dialog.close(); frame.src = "about:blank";
          var returned = Array.from(document.querySelectorAll("a[data-panel]")).find(function(link) {return link.getAttribute("href") === href;});
          if (returned) returned.focus({preventScroll:true}); scrollTo(0,top);
        } catch (error) { child.alert("Saved. The list could not refresh; close the panel and refresh the page to see the new values."); }
      }
    });
  });
})();
