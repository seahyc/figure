function(options) {
  const opts = Object.assign({
    keywords: ['dismiss', 'close', 'decline', 'reject', 'accept',
               'no thanks', 'not now', 'maybe later', 'got it', 'ok'],
    selectors: ['.close', '.close-btn', '.modal-close', '[class*="close-"]'],
    hideOverlays: true
  }, options || {});

  let dismissed = 0;

  // Phase 1: Click buttons matching keywords by text content
  document.querySelectorAll('button, [role="button"]').forEach(function(btn) {
    var text = btn.textContent.trim().toLowerCase();
    if (opts.keywords.some(function(k) { return text === k; })) {
      btn.click();
      dismissed++;
    }
  });

  // Phase 2: Click elements with matching aria-label
  document.querySelectorAll('[aria-label]').forEach(function(el) {
    var label = el.getAttribute('aria-label').toLowerCase();
    if (opts.keywords.some(function(k) { return label.includes(k); })) {
      el.click();
      dismissed++;
    }
  });

  // Phase 3: Click elements matching custom selectors
  opts.selectors.forEach(function(sel) {
    document.querySelectorAll(sel).forEach(function(el) {
      el.click();
      dismissed++;
    });
  });

  // Phase 4: Hide remaining fixed/absolute overlays
  if (opts.hideOverlays) {
    document.querySelectorAll('div, section, aside').forEach(function(el) {
      var style = getComputedStyle(el);
      var z = parseFloat(style.zIndex) || 0;
      if ((style.position === 'fixed' || style.position === 'absolute') && z > 50
          && el.offsetWidth > 0 && style.display !== 'none') {
        var id = (el.id || '').toLowerCase();
        var cls = (el.className || '').toString().toLowerCase();
        if (['root', 'app', '__next'].includes(id) ||
            cls.includes('app-container') || cls.includes('main-content')) return;
        el.style.display = 'none';
        dismissed++;
      }
    });
  }

  // Restore scroll
  document.body.style.overflow = '';
  document.documentElement.style.overflow = '';
  return 'Dismissed ' + dismissed + ' elements';
}
