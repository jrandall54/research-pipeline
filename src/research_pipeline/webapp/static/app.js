// Small enhancements only: the pages work without JavaScript. Later tasks add the vote, save and summarize behaviour here.
"use strict";

// Skim / Read: the choice is remembered on this device (never in the database).
(function () {
  function stored() {
    try { return localStorage.getItem("depth"); } catch (e) { return null; }
  }
  function setDepth(depth, remember) {
    document.body.setAttribute("data-depth", depth);
    document.querySelectorAll("[data-depth-set]").forEach(function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-depth-set") === depth ? "true" : "false");
    });
    if (remember) {
      try { localStorage.setItem("depth", depth); } catch (e) { /* private mode: still works for this page */ }
    }
  }
  document.addEventListener("DOMContentLoaded", function () {
    var saved = stored();
    if (saved === "skim" || saved === "read") setDepth(saved, false);
    document.querySelectorAll("[data-depth-set]").forEach(function (b) {
      b.addEventListener("click", function () { setDepth(b.getAttribute("data-depth-set"), true); });
    });
  });
})();

// Mine / Following / Mix: shown only when you follow someone. The choice is remembered on this
// device, and opening the feed with no choice in the address goes to the remembered one.
(function () {
  var VIEWS = ["mine", "following", "mix"];
  document.addEventListener("DOMContentLoaded", function () {
    var links = document.querySelectorAll("[data-view-set]");
    if (!links.length) return;
    var saved = null;
    try { saved = localStorage.getItem("view"); } catch (e) { /* private mode: no memory */ }
    if (location.pathname === "/" && !location.search && VIEWS.indexOf(saved) > 0) {
      location.replace("/?view=" + saved);
      return;
    }
    links.forEach(function (a) {
      a.addEventListener("click", function () {
        try { localStorage.setItem("view", a.getAttribute("data-view-set")); } catch (e) { /* ignore */ }
      });
    });
  });
})();

// Vote, save and summarize: each button posts to /api/paper/<id>/... and updates itself from the answer.
(function () {
  function post(id, action, body) {
    return fetch("/api/paper/" + encodeURIComponent(id) + "/" + action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).catch(function () {
      throw new Error("Couldn't reach your library. Check your connection.");
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(data.error || "Something went wrong. Try again.");
        return data;
      });
    });
  }
  function setVote(scope, vote) {
    var up = scope.querySelector('[data-action="vote-up"]');
    var down = scope.querySelector('[data-action="vote-down"]');
    if (up) up.setAttribute("aria-pressed", vote === 1 ? "true" : "false");
    if (down) down.setAttribute("aria-pressed", vote === -1 ? "true" : "false");
  }
  function handle(btn) {
    var action = btn.getAttribute("data-action");
    var article = btn.closest("article");
    var id = article.getAttribute("data-paper-id");
    var status = article.querySelector(".actions .status");
    var pressed = btn.getAttribute("aria-pressed") === "true";
    var request;
    if (action === "vote-up" || action === "vote-down") {
      // Tapping the choice that is already active takes it back.
      var dir = pressed ? "clear" : (action === "vote-up" ? "up" : "down");
      request = post(id, "vote", { direction: dir }).then(function (d) { setVote(article, d.vote); });
    } else if (action === "save") {
      request = post(id, "save", { saved: !pressed }).then(function (d) {
        btn.setAttribute("aria-pressed", d.saved ? "true" : "false");
        btn.textContent = d.saved ? "Saved" : "Save";
      });
    } else if (action === "summarize") {
      request = post(id, "summarize").then(function () {
        var box = btn.closest(".summarize");
        box.setAttribute("data-state", "pending");
        box.textContent = "";
        var note = document.createElement("span");
        note.className = "muted";
        note.textContent = "Summary coming tonight";
        box.appendChild(note);
      });
    } else {
      return;
    }
    btn.disabled = true;
    if (status) status.textContent = "";
    request.then(function () { btn.disabled = false; }, function (e) {
      btn.disabled = false;
      if (status) status.textContent = e.message;
    });
  }
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest("button[data-action]");
    if (btn) handle(btn);
  });
})();
