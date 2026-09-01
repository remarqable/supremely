/* Google Analytics 4 bootstrap — the inline gtag stub from the official
   snippet, served as a first-party file so the site's Content-Security-Policy
   never needs 'unsafe-inline'. The measurement ID arrives via this script
   tag's data-measurement-id attribute. */
(function () {
  var script = document.currentScript;
  var id = script && script.dataset.measurementId;
  if (!id) return;
  window.dataLayer = window.dataLayer || [];
  window.gtag = window.gtag || function gtag() {
    window.dataLayer.push(arguments);
  };
  window.gtag('js', new Date());
  window.gtag('config', id);
})();
