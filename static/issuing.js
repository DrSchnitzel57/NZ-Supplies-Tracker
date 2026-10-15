/* Open-tab issuing only: no storage, autosave, queue or automatic write retry. */
(function () {
  "use strict";
  var form = document.getElementById("issue-form");
  if (!form) return;
  var person = document.getElementById("person-select");
  var search = document.getElementById("person-filter");
  var catalogue = document.getElementById("catalogue-rows");
  var itemSearch = document.getElementById("item-filter");
  var list = document.getElementById("issue-rows");
  var note = document.getElementById("issue-note");
  var feedback = document.getElementById("issue-feedback");
  var review = document.getElementById("shortage-review");
  var chosen = person.value;
  var dirty = list.children.length > 0 || !!note.value;
  var sending = false;
  var uncertain = null;
  var selectedName = document.getElementById("selected-name");
  var selectedId = document.getElementById("selected-id");
  var picker = document.getElementById("recipient-picker");
  var identityCard = document.getElementById("selected-person");

  function say(text) {
    feedback.textContent = text; feedback.classList.toggle("quiet", text.startsWith("Added"));
    feedback.classList.toggle("warning", /not confirmed|Sign.in|Cannot|Could not|Security/.test(text));
  }
  function rows() { return Array.from(list.querySelectorAll(".issue-row")); }
  function renumber() {
    rows().forEach(function (row, i) {
      row.querySelector("input[type=hidden]").name = "item-" + i;
      row.querySelector("input[type=number]").name = "qty-" + i;
    });
    var units = rows().reduce(function(total, row) { return total + Number(row.querySelector("input[type=number]").value || 0); }, 0);
    document.getElementById("draft-count").textContent = rows().length + " of 50 distinct items · " + units + " units";
    document.getElementById("recipient-summary").textContent = chosen
      ? "For " + selectedName.textContent + " (" + selectedId.textContent + ")"
      : "Choose a recipient to continue.";
    document.getElementById("issue-submit").textContent = chosen ? "Issue supplies to " + selectedName.textContent : "Issue supplies";
  }
  function confirmRecipient(next) {
    if (next === chosen) return true;
    if ((rows().length || note.value) && !window.confirm("Change missionary? This clears every item and the note.")) {
      person.value = chosen;
      return false;
    }
    if (rows().length || note.value) { list.replaceChildren(); note.value = ""; }
    chosen = next;
    dirty = !!chosen;
    renumber();
    return true;
  }
  document.getElementById("person-finder").addEventListener("finder-select", function(event) {
    var result = event.detail;
    if (!confirmRecipient(result.id)) return;
    person.value = result.id; selectedName.textContent = result.label; selectedId.textContent = result.detail;
    picker.hidden = true; identityCard.hidden = false; renumber(); document.getElementById("change-person").focus();
  });
  document.getElementById("change-person").addEventListener("click", function() {
    if (!confirmRecipient("")) return;
    person.value = ""; picker.hidden = false; identityCard.hidden = true;
    document.getElementById("person-finder").dispatchEvent(new Event("finder-clear")); search.focus();
  });
  var catalogueRows = Array.from(catalogue.querySelectorAll(".catalogue-row"));
  function filterCatalogue() {
    var terms = itemSearch.value.trim().toLowerCase().slice(0,160).split(/\s+/).slice(0,8);
    var matching = catalogueRows.filter(function(row) {
      row.hidden = !terms.every(function(term) { return row.dataset.label.toLowerCase().includes(term); }); return !row.hidden;
    });
    matching.sort(function(a,b) {
      var query = itemSearch.value.trim().toLowerCase();
      return Number(b.querySelector("strong").textContent.toLowerCase() === query) - Number(a.querySelector("strong").textContent.toLowerCase() === query);
    }).forEach(function(row) { catalogue.append(row); });
    document.getElementById("item-count").textContent = matching.length + " matching items";
    document.getElementById("catalogue-empty").hidden = !!matching.length;
  }
  itemSearch.addEventListener("input", filterCatalogue);
  itemSearch.addEventListener("keydown", function(event) { if (event.key === "Enter") event.preventDefault(); });
  function addItem(source) {
    var itemId = source.dataset.item, labelText = source.dataset.label;
    var amount = 1;
    var existing = rows().find(function (r) { return r.dataset.item === itemId; });
    if (existing) {
      var input = existing.querySelector("input[type=number]");
      input.value = Number(input.value || 0) + amount;
      input.focus();
    } else {
      if (rows().length >= 50) { say("An issue can contain at most 50 distinct items."); return; }
      var row = document.createElement("div"); row.className = "issue-row"; row.dataset.item = itemId;
      var id = document.createElement("input"); id.type = "hidden"; id.value = itemId;
      var label = document.createElement("span"); label.className = "line-label"; label.textContent = labelText;
      var quantityLabel = document.createElement("label"); quantityLabel.textContent = "Quantity ";
      var quantity = document.createElement("input"); quantity.type = "number"; quantity.min = "1";
      quantity.max = "999999999"; quantity.required = true; quantity.value = amount;
      quantityLabel.append(quantity);
      var remove = document.createElement("button"); remove.type = "button"; remove.className = "remove-row";
      remove.textContent = "Remove"; remove.setAttribute("aria-label", "Remove " + labelText);
      row.append(id, label, quantityLabel, remove); list.append(row);
    }
    dirty = true; renumber(); say("Added to issue.");
  }
  catalogue.addEventListener("click", function(event) { if (event.target.closest(".catalogue-add")) addItem(event.target.closest(".catalogue-row")); });
  list.addEventListener("click", function (event) {
    if (event.target.classList.contains("remove-row")) { event.target.closest(".issue-row").remove(); dirty = true; renumber(); }
  });
  form.addEventListener("input", function () { dirty = true; renumber(); });

  function freeze(value) {
    form.querySelectorAll("input:not([type=hidden]), select, button[type=button]").forEach(function (control) { control.disabled = value; });
  }
  async function submit(source, event) {
    event.preventDefault();
    if (sending) return;
    if (source === form && !chosen) { say("Choose a missionary from the suggestions first."); search.focus(); return; }
    if (source === form && !rows().length) { say("Add at least one item to the issue."); return; }
    sending = true;
    var button = source.querySelector("button[type=submit]");
    button.disabled = true;
    var payload = uncertain || new FormData(source);
    try {
      var identity = await fetch("/api/session/", {cache: "no-store", credentials: "same-origin"});
      if (!identity.ok) { say(uncertain
        ? "Sign in again as the original staff member in a new tab. The original issue may already be saved; success is not confirmed. Retry that same request here deliberately."
        : "Sign in again in a new tab, then retry here. Your original draft is still in this tab; nothing was sent."); return; }
      var security = await identity.json();
      if (security.user_id !== form.dataset.draftUser) { say("This draft belongs to a different account. Sign in as the original staff member before retrying. " + (uncertain ? "The original issue may already be saved; success is not confirmed." : "Nothing was sent.")); return; }
      window.refreshShellCsrf(security.csrf_token);
      payload.set("csrfmiddlewaretoken", security.csrf_token);
      payload.set("draft_user_id", form.dataset.draftUser);
      var url = uncertain ? uncertain.action : source.action;
      // Keep exactly the attempted payload in memory when the reply is uncertain.
      payload.action = url;
      var response;
      try {
        response = await fetch(url, {method: "POST", credentials: "same-origin", body: payload, headers: {"X-Requested-With": "fetch"}});
        var html = await response.text();
      } catch (error) {
        uncertain = payload; freeze(true);
        say("No reply — success is not confirmed. The original request and retry key are retained. Retry deliberately without changing it, or check History in another tab. Nothing is queued.");
        return;
      }
      if (response.status >= 500) {
        uncertain = payload; freeze(true);
        say("Server error — success is not confirmed. Retry this same request deliberately, or check History in another tab."); return;
      }
      if (response.ok && (response.headers.get("Content-Type") || "").includes("application/json")) {
        var saved;
        try { saved = JSON.parse(html); if (!saved.redirect_url || !saved.redirect_url.startsWith("/issue/")) throw new Error("Missing result"); }
        catch (error) { uncertain = payload; freeze(true); say("Unexpected reply — success is not confirmed. Retry the original request deliberately, or check History in another tab."); return; }
        dirty = false; uncertain = null; window.location.assign(saved.redirect_url); return;
      }
      var result = new DOMParser().parseFromString(html, "text/html");
      if (response.redirected && new URL(response.url).pathname.startsWith("/accounts/")) {
        say("Sign-in is needed. Keep this tab open, sign in in another tab and retry here."); return;
      }
      if (response.ok && response.redirected) {
        dirty = false; uncertain = null; window.location.assign(response.url); return;
      }
      if (!result.querySelector("main")) { uncertain = payload; freeze(true); say("Unexpected reply — success is not confirmed. Retry the original request deliberately, or check History in another tab."); return; }
      uncertain = null; freeze(false);
      var shortage = result.querySelector("form[action='/issue/confirm/']");
      if (shortage) {
        review.replaceChildren();
        var heading = document.createElement("h2"); heading.textContent = "Review revised quantities — nothing was issued yet";
        review.append(heading);
        var table = result.querySelector(".tablewrap"); if (table) review.append(document.importNode(table, true));
        var confirmation = document.importNode(shortage, true); review.append(confirmation);
        confirmation.removeAttribute("data-once");
        confirmation.addEventListener("submit", function (e) {
          if (e.submitter && e.submitter.classList.contains("back-draft")) {
            e.preventDefault(); review.hidden = true; form.hidden = false; document.getElementById("change-person").focus();
          } else submit(confirmation, e);
        });
        form.hidden = true; review.hidden = false; heading.tabIndex = -1; heading.focus();
        say("Review the revised list, or return to your unchanged original draft.");
      } else {
        review.hidden = true; form.hidden = false;
        var errors = result.querySelector(".messages");
        say(response.status === 403 ? "Security token changed. Nothing was issued; retry here to refresh it safely."
          : errors ? errors.textContent.trim() : "Could not issue goods. Your original draft is retained; review it before retrying.");
      }
    } catch (error) {
      say(uncertain
        ? "Cannot check sign-in. The original issue may already be saved; success is not confirmed. Keep this tab open and retry deliberately."
        : "Cannot check sign-in while disconnected. Nothing was sent; keep this tab open and retry when connected.");
    } finally { sending = false; button.disabled = false; }
  }
  form.addEventListener("submit", function (e) { submit(form, e); });
  window.addEventListener("beforeunload", function (e) { if (dirty || uncertain) { e.preventDefault(); e.returnValue = ""; } });
  window.updateIssueAvailability = function (availability) {
    catalogueRows.forEach(function (row) {
      var stock = availability[row.dataset.item];
      if (stock) row.querySelector("[data-catalogue-stock]").textContent = stock.available;
    });
  };
  filterCatalogue();
  renumber();
})();
