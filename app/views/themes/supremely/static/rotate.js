/* Rotating headline word (front-page "Rotating words" content): loops
   through the words forever, holding longest on the last one ("You.").
   Lives in a static file because the app's CSP (script-src 'self') blocks
   inline scripts. The slot is sized to the current word and the width
   change is CSS-transitioned, so the line stays visually centered and the
   static lead glides gently instead of jumping. Reduced-motion users still
   get the word changes — theme.css strips the transitions so each swap is
   instant, with no movement. */
(function () {
  'use strict';

  var HOLD_MS = 1600;        // how long each word stays
  var LAST_HOLD_MS = 3000;   // the settle moment on the final word
  var SWAP_MS = 220;         // matches the CSS transition duration

  function init() {
    var el = document.querySelector('.sup-rotate');
    if (!el) return;
    var words = (el.dataset.words || '').split(',')
      .map(function (w) { return w.trim(); })
      .filter(Boolean);
    if (words.length < 2) return;

    el.style.whiteSpace = 'nowrap';

    // Measure every word in the live font so the slot can be sized to the
    // current word (re-measured on resize — the font size steps across
    // breakpoints).
    var widths = [];
    function measure() {
      var probe = document.createElement('span');
      probe.style.visibility = 'hidden';
      probe.style.position = 'absolute';
      probe.style.whiteSpace = 'pre';
      el.parentNode.appendChild(probe);
      widths = words.map(function (w) {
        probe.textContent = w;
        return Math.ceil(probe.offsetWidth);
      });
      probe.remove();
    }

    var i = 0;
    function show(idx) {
      el.textContent = words[idx];
      el.style.width = widths[idx] + 'px';
    }

    measure();
    show(0);

    var resizeTimer;
    window.addEventListener('resize', function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        measure();
        el.style.width = widths[i] + 'px';
      }, 150);
    });

    function tick() {
      el.classList.add('sup-rotate-out');
      setTimeout(function () {
        i = (i + 1) % words.length;
        show(i);
        el.classList.remove('sup-rotate-out');
        setTimeout(tick, i === words.length - 1 ? LAST_HOLD_MS : HOLD_MS);
      }, SWAP_MS);
    }
    setTimeout(tick, HOLD_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
