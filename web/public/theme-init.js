// Applies the saved/system theme before first paint (no flash). Kept in sync with src/store/theme.ts.
(function () {
  var pref = "system";
  try { pref = localStorage.getItem("fm-theme") || "system"; } catch (e) { /* storage blocked */ }
  var dark = pref === "dark" || (pref !== "light" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
})();
