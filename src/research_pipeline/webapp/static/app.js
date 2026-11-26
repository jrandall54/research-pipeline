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
