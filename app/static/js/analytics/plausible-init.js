/* Plausible bootstrap — the inline half of the official snippet, served as a
   first-party file so the site's Content-Security-Policy never needs
   'unsafe-inline'. The site ID lives in the vendor script's URL (or its
   data-domain attribute), so this file carries no per-site config. */
window.plausible = window.plausible || function () {
  (window.plausible.q = window.plausible.q || []).push(arguments);
};
window.plausible.init = window.plausible.init || function (options) {
  window.plausible.o = options || {};
};
window.plausible.init();
