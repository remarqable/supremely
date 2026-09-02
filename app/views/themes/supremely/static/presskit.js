// Copy-to-clipboard for the press kit's color swatches. A static asset
// because the Content-Security-Policy has no unsafe-inline (see rotate.js).
document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-copy]');
  if (!button || !navigator.clipboard) return;
  navigator.clipboard.writeText(button.dataset.copy).then(() => {
    const original = button.textContent;
    button.textContent = 'Copied';
    setTimeout(() => { button.textContent = original; }, 1200);
  });
});
