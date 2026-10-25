/* Picks the web app manifest of the PWA being opened before the browser reads it:
 * /op → operator tablet (id /op/, landscape), otherwise the technician app (id /m/). */
(function () {
  try {
    if (location.pathname === "/op" || location.pathname.indexOf("/op/") === 0) {
      var link = document.querySelector('link[rel="manifest"]');
      if (link) link.setAttribute("href", "/op.webmanifest");
      var apple = document.querySelector('link[rel="apple-touch-icon"]');
      if (apple) apple.setAttribute("href", "/icons/op-192.png");
    }
  } catch (e) { /* never block the app */ }
})();
