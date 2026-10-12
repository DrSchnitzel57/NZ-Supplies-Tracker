/* Open-tab issuing only: no storage, autosave, queue or automatic write retry. */
(function () {
  "use strict";
  var form = document.getElementById("issue-form");
  if (!form) return;
  var person = document.getElementById("person-select");
  var search = document.getElementById("person-filter");
  var catalogue = document.getElementById("catalogue-select");
  var itemSearch = document.getElementById("item-filter");
  var qty = document.getElementById("catalogue-qty");
  var list = document.getElementById("issue-rows");
  var note = document.getElementById("issue-note");
  var feedback = document.getElementById("issue-feedback");
  var review = document.getElementById("shortage-review");
  var chosen = person.value;
  var dirty = list.children.length > 0 || !!note.value;
  var sending = false;
  var uncertain = null;
  // Catalogue controls are an enhancement, not extra issue lines on submission.
  catalogue.removeAttribute("name");
  qty.removeAttribute("name");
  document.getElementById("add-item").hidden = false;

  function say(text) { feedback.textContent = text; }
  function rows() { return Array.from(list.querySelectorAll(".issue-row")); }
  function renumber() {
    rows().forEach(function (row, i) {
      row.querySelector("input[type=hidden]").name = "item-" + i;
      row.querySelector("input[type=number]").name = "qty-" + i;
    });
    document.getElementById("draft-count").textContent = rows().length + " of 50 distinct items";
    document.getElementById("recipient-summary").textContent = chosen
      ? "Issue to: " + person.options[person.selectedIndex].textContent
      : "Choose the recipient above. Nothing can be issued without a recipient.";
  }
  function filter(select, input, count, noun) {
    var q = input.value.trim().toLowerCase();
    var matches = 0;
    Array.from(select.options).forEach(function (option) {
      option.hidden = !!option.value && !option.textContent.toLowerCase().includes(q);
      if (option.value && !option.hidden) matches++;
    });
    document.getElementById(count).textContent = matches + " matching " + noun;
    return Array.from(select.options).filter(function (o) { return o.value && !o.hidden; });
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
  search.addEventListener("input", function () {
    // Never submit an old UUID whose selected label no longer matches search.
    if (chosen && !person.options[person.selectedIndex].textContent.toLowerCase().includes(search.value.trim().toLowerCase())) {
      if (!confirmRecipient("")) { search.value = person.options[person.selectedIndex].textContent; }
      else person.value = "";
    }
    filter(person, search, "person-count", "missionaries");
    person.size = search.value ? 6 : 1;
  });
  search.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      var matches = filter(person, search, "person-count", "missionaries");
      if (matches.length && confirmRecipient(matches[0].value)) {
        person.value = chosen; person.size = 1; renumber();
      }
    } else if (event.key === "ArrowDown") { event.preventDefault(); person.focus(); }
  });
  person.addEventListener("change", function () {
    if (confirmRecipient(person.value)) { person.size = 1; search.value = ""; filter(person, search, "person-count", "missionaries"); }
    renumber();
  });
  itemSearch.addEventListener("input", function () {
    var matches = filter(catalogue, itemSearch, "item-count", "items");
    if (catalogue.selectedOptions[0] && catalogue.selectedOptions[0].hidden) catalogue.value = "";
    if (!matches.length) catalogue.value = "";
  });
  itemSearch.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      var matches = filter(catalogue, itemSearch, "item-count", "items");
      if (matches.length) { catalogue.value = matches[0].value; catalogue.focus(); }
    }
  });
  function addItem() {
    if (!catalogue.value || !qty.checkValidity()) { say("Choose an item and a positive whole quantity."); return; }
    var amount = Number(qty.value);
    var existing = rows().find(function (r) { return r.dataset.item === catalogue.value; });
    if (existing) {
      var input = existing.querySelector("input[type=number]");
      input.value = Number(input.value || 0) + amount;
      input.focus();
    } else {
      if (rows().length >= 50) { say("An issue can contain at most 50 distinct items."); return; }
      var option = catalogue.selectedOptions[0];
      var row = document.createElement("div"); row.className = "issue-row"; row.dataset.item = catalogue.value;
      var id = document.createElement("input"); id.type = "hidden"; id.value = catalogue.value;
      var label = document.createElement("span"); label.className = "line-label"; label.textContent = option.dataset.label;
      var quantityLabel = document.createElement("label"); quantityLabel.textContent = "Quantity ";
      var quantity = document.createElement("input"); quantity.type = "number"; quantity.min = "1";
      quantity.max = "999999999"; quantity.required = true; quantity.value = amount;
      quantityLabel.append(quantity);
      var remove = document.createElement("button"); remove.type = "button"; remove.className = "remove-row";
      remove.textContent = "Remove"; remove.setAttribute("aria-label", "Remove " + option.dataset.label);
      row.append(id, label, quantityLabel, remove); list.append(row);
    }
    dirty = true; renumber(); say("Added to the list. Review the recipient and quantities before issuing.");
  }
  document.getElementById("add-item").addEventListener("click", addItem);
  list.addEventListener("click", function (event) {
    if (event.target.classList.contains("remove-row")) { event.target.closest(".issue-row").remove(); dirty = true; renumber(); }
  });
  form.addEventListener("input", function () { dirty = true; });

  function freeze(value) {
    form.querySelectorAll("input:not([type=hidden]), select, button[type=button]").forEach(function (control) { control.disabled = value; });
  }
  async function submit(source, event) {
    event.preventDefault();
    if (sending) return;
    if (source === form && !rows().length) { say("Add at least one item to the issue."); return; }
    sending = true;
    var button = source.querySelector("button[type=submit]");
    button.disabled = true;
    var payload = uncertain || new FormData(source);
    try {
      var identity = await fetch("/api/session/", {cache: "no-store", credentials: "same-origin"});
      if (!identity.ok) { say("Sign in again in a new tab, then retry here. Your original draft is still in this tab; nothing was sent."); return; }
      var security = await identity.json();
      if (security.user_id !== form.dataset.draftUser) { say("This draft belongs to a different account. Sign in as the original staff member before retrying; nothing was sent."); return; }
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
      if (response.ok && response.headers.get("Content-Type").includes("application/json")) {
        var saved = JSON.parse(html);
        dirty = false; uncertain = null; window.location.assign(saved.redirect_url); return;
      }
      var result = new DOMParser().parseFromString(html, "text/html");
      if (response.redirected && new URL(response.url).pathname.startsWith("/accounts/")) {
        say("Sign-in is needed. Keep this tab open, sign in in another tab and retry here."); return;
      }
      if (response.ok && response.redirected) {
        dirty = false; uncertain = null; window.location.assign(response.url); return;
      }
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
            e.preventDefault(); review.hidden = true; form.hidden = false; person.focus();
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
      say("Cannot check sign-in while disconnected. Nothing was sent; keep this tab open and retry when connected.");
    } finally { sending = false; button.disabled = false; }
  }
  form.addEventListener("submit", function (e) { submit(form, e); });
  window.addEventListener("beforeunload", function (e) { if (dirty || uncertain) { e.preventDefault(); e.returnValue = ""; } });
  window.updateIssueAvailability = function (availability) {
    Array.from(catalogue.options).forEach(function (option) {
      var stock = availability[option.value];
      if (stock) option.textContent = option.dataset.label + " — Available: " + stock.available + (stock.low ? " (low)" : "");
    });
  };
  filter(person, search, "person-count", "missionaries");
  filter(catalogue, itemSearch, "item-count", "items");
  renumber();
})();
