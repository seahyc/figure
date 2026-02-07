function(options) {
  var opts = Object.assign({
    keywords: ['dismiss', 'close', 'decline', 'reject', 'accept',
               'no thanks', 'not now', 'maybe later', 'got it', 'ok'],
    selectors: ['.close', '.close-btn', '.modal-close', '[class*="close-"]'],
    hideOverlays: true
  }, options || {});

  var dismissed = 0;

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

  // Phase 4: Hide remaining fixed/absolute overlays (with challenge element exclusions)
  if (opts.hideOverlays) {
    // Data attributes that indicate challenge-related elements
    var challengeAttrs = ['data-part', 'data-piece', 'data-tab', 'data-slot',
                          'data-hover-target', 'data-scroll-box', 'data-challenge',
                          'data-step', 'data-code', 'data-type'];

    document.querySelectorAll('div, section, aside').forEach(function(el) {
      var style = getComputedStyle(el);
      var z = parseFloat(style.zIndex) || 0;
      if ((style.position === 'fixed' || style.position === 'absolute') && z > 50
          && el.offsetWidth > 0 && style.display !== 'none') {
        // Skip app root elements
        var id = (el.id || '').toLowerCase();
        var cls = (el.className || '').toString().toLowerCase();
        if (['root', 'app', '__next'].includes(id) ||
            cls.includes('app-container') || cls.includes('main-content')) return;

        // Skip challenge-related elements (have data- attributes)
        var isChallengeElement = challengeAttrs.some(function(attr) {
          return el.hasAttribute(attr);
        });
        if (isChallengeElement) return;

        // Skip elements inside challenge containers
        if (el.closest('.challenge-container') || el.closest('.step-content') ||
            el.closest('[data-challenge]')) return;

        el.style.display = 'none';
        dismissed++;
      }
    });
  }

  // Phase 5: Handle blocking radio modals (high z-index modal with radio options)
  var modalSelectors = ['.blocking-modal-content', '.modal-content', '[class*="blocking-modal"]',
                        '[class*="modal"][class*="block"]'];
  var modal = null;
  for (var i = 0; i < modalSelectors.length; i++) {
    modal = document.querySelector(modalSelectors[i]);
    if (modal) break;
  }
  // Also detect modals by structure: high z-index container with radio inputs
  if (!modal) {
    document.querySelectorAll('div').forEach(function(el) {
      if (modal) return;
      var style = getComputedStyle(el);
      var z = parseFloat(style.zIndex) || 0;
      if (z >= 10000 && el.querySelector('input[type="radio"]')) {
        modal = el;
      }
    });
  }

  if (modal) {
    // Scroll the modal's scrollable area to find all options
    var scrollArea = modal.querySelector('[style*="overflow"]') ||
                     modal.querySelector('.modal-body') || modal;
    if (scrollArea && scrollArea.scrollHeight > scrollArea.clientHeight) {
      scrollArea.scrollTop = scrollArea.scrollHeight;
    }

    // Find the correct radio option (matches "Option X ... Correct" pattern)
    var radios = modal.querySelectorAll('input[type="radio"]');
    var correctRadio = null;
    radios.forEach(function(radio) {
      var label = radio.closest('label') || radio.parentElement;
      if (label) {
        var text = label.textContent || '';
        if (/correct/i.test(text) || /right\s*answer/i.test(text) || /true/i.test(text)) {
          correctRadio = radio;
        }
      }
    });

    // If no "correct" label found, try matching "Option [A-D].*Correct"
    if (!correctRadio) {
      radios.forEach(function(radio) {
        var label = radio.closest('label') || radio.parentElement;
        if (label) {
          var text = label.textContent || '';
          if (/Option\s+[A-D].*Correct/i.test(text)) {
            correctRadio = radio;
          }
        }
      });
    }

    if (correctRadio) {
      correctRadio.checked = true;
      correctRadio.click();
      correctRadio.dispatchEvent(new Event('change', {bubbles: true}));
      dismissed++;

      // Find and click the submit/continue button in the modal
      var modalButtons = modal.querySelectorAll('button');
      modalButtons.forEach(function(btn) {
        var text = btn.textContent.trim().toLowerCase();
        if ((text.includes('submit') || text.includes('continue') || text.includes('confirm')) &&
            !text.includes('code')) {
          btn.click();
          dismissed++;
        }
      });
    }
  }

  // Restore scroll
  document.body.style.overflow = '';
  document.documentElement.style.overflow = '';
  return 'Dismissed ' + dismissed + ' elements';
}
