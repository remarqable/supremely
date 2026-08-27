/* Rotating headline word (front-page "Rotating words" content): cycles
   through the words once and settles on the last. Lives in a static file
   because the app's CSP (script-src 'self') blocks inline scripts.
   Reduced-motion users still get the word changes — theme.css strips the
   transition so each swap is instant, with no movement. */
(function () {
  'use strict';

  var HOLD_MS = 1600;   // how long each word stays
  var SWAP_MS = 220;    // matches the CSS transition duration

  function init() {
    var el = document.querySelector('.sup-rotate');
    if (!el) return;
    var words = (el.dataset.words || '').split(',')
      .map(function (w) { return w.trim(); })
      .filter(Boolean);
    if (words.length < 2) return;

    // Reserve the widest word's width so the static lead never shifts as
    // the line re-centers; the word sits at the start of its fixed slot.
    function reserveWidth() {
      var probe = document.createElement('span');
      probe.style.visibility = 'hidden';
      probe.style.position = 'absolute';
      probe.style.whiteSpace = 'pre';
      el.parentNode.appendChild(probe);
      var widest = 0;
      words.forEach(function (w) {
        probe.textContent = w;
        widest = Math.max(widest, probe.offsetWidth);
      });
      probe.remove();
      el.style.minWidth = Math.ceil(widest) + 'px';
      el.style.textAlign = 'start';
    }
    reserveWidth();
    var resizeTimer;
    window.addEventListener('resize', function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(reserveWidth, 150);
    });

    var i = 0;
    el.textContent = words[0];

    function next() {
      el.classList.add('sup-rotate-out');
      setTimeout(function () {
        i += 1;
        el.textContent = words[i];
        el.classList.remove('sup-rotate-out');
        if (i < words.length - 1) setTimeout(next, HOLD_MS);
      }, SWAP_MS);
    }
    setTimeout(next, HOLD_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
