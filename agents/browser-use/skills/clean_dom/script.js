function(options) {
  var opts = Object.assign({
    aggressive: false,
    keepSelector: null,
    minRepeat: 5,
    dedupeInteractive: true,
    interactiveMinRepeat: 3
  }, options || {});
  var excluded = 0;
  var EXCLUDE_ATTR = 'data-browser-use-exclude';
  var SUMMARY_CLASS = '__clean-dom-summary';

  // Remove previous summaries to prevent accumulation on re-runs
  document.querySelectorAll('.' + SUMMARY_CLASS).forEach(function(el) { el.remove(); });

  // Selectors for elements that must always remain visible
  var keepSel = 'form, input, select, textarea, [data-code], [data-hidden]';
  var keepEls = new Set();
  if (opts.keepSelector) {
    document.querySelectorAll(opts.keepSelector).forEach(function(el) { keepEls.add(el); });
  }
  document.querySelectorAll(keepSel).forEach(function(el) { keepEls.add(el); });

  function shouldKeep(el) {
    if (keepEls.has(el)) return true;
    if (el.shadowRoot) return true;
    if (el.querySelector && el.querySelector(keepSel)) return true;
    var id = (el.id || '').toLowerCase();
    if (['root', 'app', '__next', 'main'].includes(id)) return true;
    if (el.getAttribute(EXCLUDE_ATTR) === 'true') return false;
    return false;
  }

  function markExclude(el) {
    if (el.getAttribute(EXCLUDE_ATTR) === 'true') return;
    el.setAttribute(EXCLUDE_ATTR, 'true');
    excluded++;
  }

  // Phase 1: Hide repetitive non-interactive text blocks
  // Find elements with same normalized text pattern appearing 5+ times
  var allEls = document.querySelectorAll('div, section, p, span, article, li');
  var patternGroups = {};
  for (var i = 0; i < allEls.length; i++) {
    var el = allEls[i];
    if (shouldKeep(el)) continue;
    if (el.getAttribute(EXCLUDE_ATTR) === 'true') continue;
    // Skip interactive elements (handled in Phase 2)
    var tag = el.tagName.toLowerCase();
    if (tag === 'button' || tag === 'a' || el.getAttribute('role') === 'button') continue;
    // Skip elements that contain interactive children
    if (el.querySelector && el.querySelector('button, a[href], input, select, textarea, [role="button"]')) continue;
    var text = el.textContent.trim();
    if (text.length < 5 || text.length > 300) continue;
    var normalized = text.replace(/\d+/g, '#');
    if (!patternGroups[normalized]) patternGroups[normalized] = [];
    patternGroups[normalized].push(el);
  }

  for (var norm in patternGroups) {
    var group = patternGroups[norm];
    if (group.length < opts.minRepeat) continue;
    // Keep first element for context, exclude the rest
    for (var g = 1; g < group.length; g++) {
      markExclude(group[g]);
    }
    // Insert a visible summary so the LLM knows the count
    var summary = document.createElement('span');
    summary.className = SUMMARY_CLASS;
    summary.style.cssText = 'font-size:11px;color:#888;display:block;';
    summary.textContent = '[' + (group.length - 1) + ' more similar elements hidden]';
    var firstEl = group[0];
    if (firstEl.parentNode) {
      firstEl.parentNode.insertBefore(summary, firstEl.nextSibling);
    }
  }

  // Phase 2: Deduplicate interactive elements (buttons, clickable divs)
  // If 3+ elements have identical text, keep first, exclude rest
  if (opts.dedupeInteractive) {
    var interactiveEls = document.querySelectorAll(
      'button, [role="button"], a:not([href])'
    );
    var interGroups = {};
    for (var j = 0; j < interactiveEls.length; j++) {
      var iel = interactiveEls[j];
      if (iel.getAttribute(EXCLUDE_ATTR) === 'true') continue;
      // Never exclude elements inside forms or shadow DOM hosts
      if (iel.closest('form')) continue;
      if (iel.shadowRoot) continue;
      // Never exclude if it contains a form element
      if (iel.querySelector && iel.querySelector('input, select, textarea')) continue;
      var itext = iel.textContent.trim().toLowerCase();
      if (itext.length < 2 || itext.length > 50) continue;
      if (!interGroups[itext]) interGroups[itext] = [];
      interGroups[itext].push(iel);
    }
    for (var ikey in interGroups) {
      var igroup = interGroups[ikey];
      if (igroup.length < opts.interactiveMinRepeat) continue;
      // Keep first, exclude rest
      for (var ik = 1; ik < igroup.length; ik++) {
        markExclude(igroup[ik]);
      }
      // Insert count summary after first kept element
      var btnSummary = document.createElement('span');
      btnSummary.className = SUMMARY_CLASS;
      btnSummary.style.cssText = 'font-size:11px;color:#888;display:block;';
      btnSummary.textContent = '[' + (igroup.length - 1) + ' more "' + ikey + '" buttons hidden]';
      var firstBtn = igroup[0];
      if (firstBtn.parentNode) {
        firstBtn.parentNode.insertBefore(btnSummary, firstBtn.nextSibling);
      }
    }
  }

  // Phase 2.5: Exclude known decoy buttons that waste LLM attention.
  // These buttons appear on the challenge site but NEVER advance progress.
  // Only "Submit Code" advances steps. Never exclude submit-related buttons.
  var decoyTexts = ['next', 'continue', 'proceed', 'go forward', 'next step', 'next page', 'click me', 'click here', 'advance', 'move on', 'keep going', 'browse forward', 'continue journey', 'proceed forward', 'next section', 'continue reading'];
  var allButtons = document.querySelectorAll('button, [role="button"]');
  var decoyCount = 0;
  for (var d = 0; d < allButtons.length; d++) {
    var btn = allButtons[d];
    if (btn.getAttribute(EXCLUDE_ATTR) === 'true') continue;
    var btnText = btn.textContent.trim().toLowerCase();
    // Never exclude submit-related buttons
    if (btnText.indexOf('submit') !== -1) continue;
    // Never exclude buttons inside forms
    if (btn.closest('form')) continue;
    // Check if button text matches a known decoy pattern
    if (decoyTexts.indexOf(btnText) !== -1) {
      markExclude(btn);
      decoyCount++;
    }
  }

  // Phase 2.75: Exclude structural noise (nav, footer, banners) outside challenge area.
  // These add DOM bulk but never contain challenge content.
  var noiseSelectors = [
    'nav', 'footer',
    '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
    'header:not(:has(form)):not(:has(input))'
  ];
  var noiseCount = 0;
  noiseSelectors.forEach(function(sel) {
    try {
      document.querySelectorAll(sel).forEach(function(el) {
        if (shouldKeep(el)) return;
        if (el.getAttribute(EXCLUDE_ATTR) === 'true') return;
        // Don't exclude if it contains challenge-relevant content
        if (el.querySelector && el.querySelector('input, [data-code], canvas, [draggable]')) return;
        markExclude(el);
        noiseCount++;
      });
    } catch(e) { /* :has() not supported in older browsers */ }
  });

  // Phase 2.9: DOM pruning — reduce token count for LLM (Prune4Web-inspired)
  // Remove/mark elements that never contain challenge content to reduce DOM noise.
  var pruneCount = 0;
  // Strip SVG paths (keep the <svg> but hide complex path data)
  document.querySelectorAll('svg path, svg polygon, svg polyline, svg circle, svg rect, svg line').forEach(function(el) {
    if (shouldKeep(el)) return;
    if (el.getAttribute(EXCLUDE_ATTR) === 'true') return;
    markExclude(el);
    pruneCount++;
  });
  // Strip script, style, noscript, link, meta (shouldn't be in accessibility tree anyway)
  document.querySelectorAll('script, style, noscript, link[rel="stylesheet"]').forEach(function(el) {
    if (el.getAttribute(EXCLUDE_ATTR) === 'true') return;
    markExclude(el);
    pruneCount++;
  });
  // Strip data-* attributes that waste tokens (keep data-code, data-value, data-answer, data-slot, data-part)
  var keepDataAttrs = ['data-code', 'data-value', 'data-answer', 'data-slot', 'data-part', 'data-hover-area', 'data-scroll-box', 'data-browser-use-exclude'];
  document.querySelectorAll('[data-testid], [data-reactid]').forEach(function(el) {
    try {
      Array.from(el.attributes).forEach(function(attr) {
        if (attr.name.startsWith('data-') && keepDataAttrs.indexOf(attr.name) === -1 &&
            attr.name !== 'data-browser-use-exclude') {
          el.removeAttribute(attr.name);
        }
      });
    } catch(e) {}
  });

  // Phase 3 (aggressive): Exclude off-screen non-interactive blocks
  if (opts.aggressive) {
    var viewportBottom = window.innerHeight + window.scrollY + 500;
    var remaining = document.querySelectorAll('div, section, p, article');
    for (var k = 0; k < remaining.length; k++) {
      var el2 = remaining[k];
      if (shouldKeep(el2)) continue;
      if (el2.getAttribute(EXCLUDE_ATTR) === 'true') continue;
      if (el2.tagName === 'BUTTON' || el2.getAttribute('role') === 'button') continue;
      if (el2.querySelector && el2.querySelector('button, a[href], input, select, textarea, [role="button"]')) continue;
      var rect = el2.getBoundingClientRect();
      var absTop = rect.top + window.scrollY;
      if (absTop > viewportBottom && rect.height < 200 && rect.height > 0) {
        markExclude(el2);
      }
    }
  }

  var total = document.querySelectorAll('*').length;
  var excludedTotal = document.querySelectorAll('[' + EXCLUDE_ATTR + '="true"]').length;
  return 'Cleaned DOM: excluded ' + excluded + ' new (' + decoyCount + ' decoy buttons, ' + noiseCount + ' structural noise), total excluded: ' + excludedTotal + ', total DOM: ' + total + ', visible: ~' + (total - excludedTotal);
}
