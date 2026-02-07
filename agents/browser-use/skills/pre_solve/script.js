function(options) {
  var opts = Object.assign({
    clickActionButtons: true,
    clickProgressButtons: true,
    clickSequentialNav: true,
    waitForCountdowns: true,
    reportResults: true,
    autoSubmit: true
  }, options || {});

  var actions = [];

  // Clear old results div to prevent false positives from previous steps
  var oldResults = document.getElementById('__pre_solve_results');
  if (oldResults) oldResults.remove();

  // Pattern 1: Click action buttons with general action verbs
  if (opts.clickActionButtons) {
    var actionVerbs = /^(reveal|play|connect|register|extract|start|show|open|unlock|enable|activate|load|fetch|begin|launch|display|uncover|expose|decode|decrypt|generate|capture)\b/i;
    document.querySelectorAll('button, [role="button"], a.btn, a.button, [class*="btn"]').forEach(function(btn) {
      // Skip submit code buttons and decoy navigation buttons
      var text = btn.textContent.trim();
      var textLower = text.toLowerCase();
      if (textLower.includes('submit code') || textLower === 'next' || textLower === 'continue' ||
          textLower === 'proceed' || textLower === 'go forward' || textLower === 'next page' ||
          textLower === 'next step' || textLower === 'click me') return;
      // Skip hidden or disabled buttons
      if (btn.offsetWidth === 0 || btn.disabled) return;
      // Skip buttons that are inside the input/submit area
      if (btn.closest('.submit-area') || btn.closest('[class*="submit"]')) return;

      if (actionVerbs.test(text)) {
        btn.click();
        actions.push('Clicked action button: "' + text.substring(0, 40) + '"');
      }
    });
  }

  // Pattern 2: Click repeatedly-actionable buttons near progress indicators (N/M pattern)
  // Only for simple single-action counting (e.g. "Capture 0/10"), NOT multi-action sequences.
  if (opts.clickProgressButtons) {
    document.querySelectorAll('button, [role="button"]').forEach(function(btn) {
      var text = btn.textContent.trim();
      var m = text.match(/\((\d+)\s*\/\s*(\d+)\)/);
      if (m) {
        var current = parseInt(m[1]);
        var total = parseInt(m[2]);
        // Skip multi-action sequences (sequence challenge has hover/type/scroll actions)
        var hasMultiActions = document.querySelector('[class*="hover"], [class*="scroll"]');
        if (current < total && total <= 20 && !hasMultiActions) {
          document.querySelectorAll('button, [role="button"]').forEach(function(target) {
            var ttext = target.textContent.trim();
            if (/^(capture|collect|tap|press|grab|get|pick|catch|gather|count|increment|add)/i.test(ttext) && !target.disabled) {
              var remaining = total - current;
              for (var i = 0; i < Math.min(remaining, 15); i++) {
                target.click();
              }
              actions.push('Clicked progress button "' + ttext.substring(0, 30) + '" x' + Math.min(remaining, 15) + ' (' + current + '/' + total + ')');
            }
          });
        }
      }
    });
  }

  // Pattern 3: Click sequential navigation elements (tabs, numbered buttons)
  if (opts.clickSequentialNav) {
    var tabPattern = /^(tab|section|part|step|page|panel)\s*\d+$/i;
    var numberedBtns = [];
    document.querySelectorAll('button, [role="tab"], [data-tab]').forEach(function(btn) {
      var text = btn.textContent.trim();
      if (tabPattern.test(text) || /^\d+$/.test(text)) {
        numberedBtns.push(btn);
      }
    });
    if (numberedBtns.length >= 2) {
      numberedBtns.forEach(function(btn) {
        btn.click();
        actions.push('Clicked sequential nav: "' + btn.textContent.trim() + '"');
      });
    }
  }

  // Pattern 4: Wait for visible countdowns (just report, don't block)
  if (opts.waitForCountdowns) {
    var countdownPattern = /(\d+)\s*(seconds?|s)\s*(remaining|left|until|to go)/i;
    var timerPattern = /\d{1,2}:\d{2}/;
    var bodyText = document.body ? document.body.innerText : '';
    if (countdownPattern.test(bodyText) || timerPattern.test(bodyText)) {
      actions.push('Countdown/timer detected on page - may need to wait');
    }
  }

  // Pattern 5: Auto-solve drag_drop challenges
  // The challenge requires dropping ANY 6 pieces into 6 slots — no validation.
  // We fire synthetic drop events with a mock dataTransfer on each empty slot.
  // Note: new DataTransfer().getData() returns '' in Chromium for synthetic events,
  // so we use a plain Event with a mock dataTransfer object instead.
  (function() {
    var slots = document.querySelectorAll('[data-slot]');
    var pieces = document.querySelectorAll('[data-piece]');
    if (slots.length > 0 && pieces.length > 0) {
      var pieceArr = Array.from(pieces);
      var usedIdx = 0;
      slots.forEach(function(slot) {
        if (slot.dataset.filled) return;
        if (usedIdx >= pieceArr.length) return;
        var piece = pieceArr[usedIdx++];
        var pieceId = piece.getAttribute('data-piece');
        // Simulate dragover to allow drop
        var dragOverEvt = new Event('dragover', { bubbles: true, cancelable: true });
        dragOverEvt.preventDefault = function() {};
        slot.dispatchEvent(dragOverEvt);
        // Simulate drop with mock dataTransfer (Chromium blocks getData on synthetic DragEvents)
        var dropEvt = new Event('drop', { bubbles: true, cancelable: true });
        dropEvt.dataTransfer = {
          getData: function() { return pieceId; },
          setData: function() {},
          dropEffect: 'move',
          effectAllowed: 'all'
        };
        slot.dispatchEvent(dropEvt);
      });
      var filledCount = document.querySelectorAll('[data-slot][data-filled]').length;
      if (filledCount > 0) {
        actions.push('Auto-solved drag_drop: filled ' + filledCount + '/' + slots.length + ' slots');
      } else {
        actions.push('drag_drop: fired events on ' + slots.length + ' slots but none filled (DOM check)');
      }
    }
  })();

  // Pattern 6: Auto-solve gesture challenges
  // The challenge requires one mouse stroke on the canvas, then clicking "Complete".
  // We simulate mousedown, mousemove, mouseup to draw, then click the button.
  (function() {
    var canvas = document.querySelector('canvas.cursor-crosshair, canvas[class*="crosshair"]');
    if (!canvas) return;
    var gestureBtn = document.getElementById('gesture-complete');
    // Only act if the button exists and is disabled (not already solved)
    if (!gestureBtn || !gestureBtn.disabled) return;
    var rect = canvas.getBoundingClientRect();
    var cx = rect.left + rect.width / 2;
    var cy = rect.top + rect.height / 2;
    var hw = rect.width * 0.3;
    var hh = rect.height * 0.3;
    // Draw a simple square
    var points = [
      [cx - hw, cy - hh], [cx + hw, cy - hh],
      [cx + hw, cy + hh], [cx - hw, cy + hh],
      [cx - hw, cy - hh]
    ];
    canvas.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: points[0][0], clientY: points[0][1] }));
    for (var i = 1; i < points.length; i++) {
      canvas.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: points[i][0], clientY: points[i][1] }));
    }
    canvas.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, clientX: points[4][0], clientY: points[4][1] }));
    // Now click the Complete button (it should be enabled after mouseup)
    gestureBtn = document.getElementById('gesture-complete');
    if (gestureBtn && !gestureBtn.disabled) {
      gestureBtn.click();
      actions.push('Auto-solved gesture: drew square + clicked Complete');
    } else {
      actions.push('Gesture: drew on canvas, button may need another click');
    }
  })();

  // Pattern 7: Auto-solve hidden_dom — code in data attributes, aria-labels, meta tags
  (function() {
    var el = document.querySelector('[data-code]');
    if (el && el.dataset.code) {
      actions.push('Found hidden_dom code in data-code: ' + el.dataset.code);
      return;  // code finding will pick it up if visible, otherwise inject it
    }
    // Check aria-label for code pattern
    var ariaEls = document.querySelectorAll('[aria-label]');
    ariaEls.forEach(function(a) {
      var match = a.getAttribute('aria-label').match(/[A-HJ-NP-Z2-9]{6}/);
      if (match) actions.push('Found hidden_dom code in aria-label: ' + match[0]);
    });
    // Check meta tags
    var metas = document.querySelectorAll('meta[name*="code"], meta[name*="challenge"]');
    metas.forEach(function(m) {
      var match = m.content.match(/[A-HJ-NP-Z2-9]{6}/);
      if (match) actions.push('Found hidden_dom code in meta: ' + match[0]);
    });
  })();

  // Pattern 8: Auto-solve split_parts — click all parts, they reveal code fragments
  (function() {
    var parts = document.querySelectorAll('[class*="absolute"][class*="bg-yellow"], .absolute.bg-yellow-400');
    if (parts.length >= 2) {
      parts.forEach(function(p) { p.click(); });
      actions.push('Clicked ' + parts.length + ' split parts');
    }
  })();

  // Pattern 9: (removed — sequence challenges need multiple action types, better handled by LLM)

  // Pattern 10: Auto-solve service_worker — click Register then Retrieve buttons
  // First pass clicks Register, second pass (after 3.5s delay) clicks Retrieve
  (function() {
    var registerBtn = null;
    var retrieveBtn = null;
    document.querySelectorAll('button').forEach(function(btn) {
      var text = btn.textContent.trim().toLowerCase();
      if (text.includes('register') && text.includes('service worker')) registerBtn = btn;
      if (text.includes('retrieve') && text.includes('cache')) retrieveBtn = btn;
    });
    if (registerBtn && !registerBtn.disabled) {
      registerBtn.click();
      actions.push('Clicked service_worker Register button');
    }
    if (retrieveBtn && !retrieveBtn.disabled) {
      retrieveBtn.click();
      actions.push('Clicked service_worker Retrieve button');
    }
  })();

  // Pattern 11: Auto-solve websocket — click Connect button
  (function() {
    document.querySelectorAll('button').forEach(function(btn) {
      var text = btn.textContent.trim();
      if (/^connect$/i.test(text) && !btn.disabled) {
        btn.click();
        actions.push('Auto-solved websocket: clicked Connect');
      }
    });
  })();

  // Pattern 13: Auto-solve scroll_reveal — scroll page to trigger scroll-based reveals
  // Only trigger when the challenge description explicitly mentions scrolling to reveal
  (function() {
    // Look for scroll instructions in the challenge area, not entire body
    var challengeArea = document.querySelector('.max-w-6xl') || document.querySelector('main') || document.body;
    var headings = challengeArea.querySelectorAll('p, h1, h2, h3, strong, div.text-sm');
    var hasScrollChallenge = false;
    headings.forEach(function(el) {
      var t = el.textContent.trim();
      if (/scroll.*to\s*reveal|scroll\s*down.*\d+px|scroll.*reveal.*code/i.test(t)) {
        hasScrollChallenge = true;
      }
    });
    if (hasScrollChallenge) {
      var maxScroll = Math.max(document.body.scrollHeight, 1500);
      window.scrollTo(0, maxScroll);
      actions.push('Auto-scrolled to ' + maxScroll + 'px for scroll_reveal');
    }
  })();

  // Pattern 14: Auto-solve hidden_dom click variant — "click here N more times"
  (function() {
    var bodyText = document.body ? document.body.innerText : '';
    var clickMatch = bodyText.match(/click here (\d+) more times?/i);
    if (clickMatch) {
      var needed = parseInt(clickMatch[1]);
      // The "click here" text is inside a cursor-pointer div, click that container
      var target = document.querySelector('.cursor-pointer');
      if (!target) {
        // Fallback: find any element containing the "click here N more" text
        document.querySelectorAll('p, div, span').forEach(function(el) {
          if (!target && el.textContent.includes('click here') && el.textContent.includes('more time')) {
            target = el.closest('[class*="cursor"]') || el;
          }
        });
      }
      if (target) {
        for (var c = 0; c < needed + 1; c++) {
          target.click();
        }
        actions.push('Hidden DOM click: clicked ' + (needed + 1) + ' times on cursor-pointer');
      }
    }
  })();

  // Pattern 12: Find codes and optionally auto-submit
  // Challenge charset: ABCDEFGHJKLMNPQRSTUVWXYZ23456789 (no I, O, 0, 1)
  var codePattern = /\b[A-HJ-NP-Z2-9]{6}\b/;
  var foundCodes = [];

  if (opts.reportResults || opts.autoSubmit) {
    // Source 1: data-code attributes
    document.querySelectorAll('[data-code]').forEach(function(el) {
      var code = el.dataset.code;
      if (codePattern.test(code) && foundCodes.indexOf(code) === -1) {
        foundCodes.push(code);
      }
    });

    // Source 2: meta tags
    document.querySelectorAll('meta[name]').forEach(function(m) {
      var match = m.content.match(codePattern);
      if (match && foundCodes.indexOf(match[0]) === -1) {
        foundCodes.push(match[0]);
      }
    });

    // Source 3: aria-label attributes
    document.querySelectorAll('[aria-label]').forEach(function(el) {
      var match = el.getAttribute('aria-label').match(codePattern);
      if (match && foundCodes.indexOf(match[0]) === -1) {
        foundCodes.push(match[0]);
      }
    });

    // Source 4: Visible codes in code-styled elements
    var codeElements = document.querySelectorAll('.font-mono, [class*="code"], code, pre');
    codeElements.forEach(function(el) {
      var text = el.textContent.trim();
      var codeMatch = text.match(codePattern);
      if (codeMatch && el.offsetWidth > 0 && foundCodes.indexOf(codeMatch[0]) === -1) {
        foundCodes.push(codeMatch[0]);
      }
    });

    // Source 5: Bold/highlighted text
    document.querySelectorAll('.font-bold, strong, b, [class*="highlight"]').forEach(function(el) {
      var text = el.textContent.trim();
      var codeMatch = text.match(codePattern);
      if (codeMatch && el.offsetWidth > 0 && foundCodes.indexOf(codeMatch[0]) === -1) {
        foundCodes.push(codeMatch[0]);
      }
    });

    // Source 6: Hidden elements (display:none, visibility:hidden, opacity:0)
    document.querySelectorAll('*').forEach(function(el) {
      if (el.children.length > 0) return; // only leaf nodes
      var text = el.textContent.trim();
      if (!text || text.length > 20) return;
      var match = text.match(codePattern);
      if (!match || foundCodes.indexOf(match[0]) !== -1) return;
      var s = getComputedStyle(el);
      if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0' ||
          el.offsetWidth === 0 || el.offsetHeight === 0) {
        foundCodes.push(match[0]);
      }
    });

    // Source 7: HTML comments
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_COMMENT);
    while (walker.nextNode()) {
      var match = walker.currentNode.textContent.match(codePattern);
      if (match && foundCodes.indexOf(match[0]) === -1) {
        foundCodes.push(match[0]);
      }
    }

    // Source 8: title attributes
    document.querySelectorAll('[title]').forEach(function(el) {
      var match = el.getAttribute('title').match(codePattern);
      if (match && foundCodes.indexOf(match[0]) === -1) {
        foundCodes.push(match[0]);
      }
    });

    // Source 9: CSS ::before/::after content (via computed style)
    document.querySelectorAll('*').forEach(function(el) {
      ['::before', '::after'].forEach(function(pseudo) {
        var content = getComputedStyle(el, pseudo).content;
        if (content && content !== 'none' && content !== 'normal') {
          var match = content.match(codePattern);
          if (match && foundCodes.indexOf(match[0]) === -1) {
            foundCodes.push(match[0]);
          }
        }
      });
    });

    if (foundCodes.length > 0) {
      actions.push('Found potential codes: ' + foundCodes.join(', '));
    }
  }

  // Auto-submit: if we found exactly one code, type it and click Submit Code
  if (opts.autoSubmit && foundCodes.length >= 1) {
    // Strategy 1: ID-based selectors (local challenge server)
    var codeInput = document.getElementById('code-input');
    var submitBtn = document.getElementById('submit-code');
    // Strategy 2: Attribute/text-based selectors (live Netlify site)
    if (!codeInput) {
      codeInput = document.querySelector('input[placeholder*="code" i], input[placeholder*="character" i]');
    }
    if (!submitBtn) {
      document.querySelectorAll('button').forEach(function(btn) {
        if (btn.textContent.trim() === 'Submit Code') submitBtn = btn;
      });
    }
    if (codeInput && submitBtn) {
      var bestCode = foundCodes[0];
      // Only submit if input is empty or has the same code (avoid double-submit)
      if (!codeInput.value || codeInput.value === bestCode) {
        // Set value using native setter to trigger React/framework change handlers
        var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        nativeInputValueSetter.call(codeInput, bestCode);
        codeInput.dispatchEvent(new Event('input', { bubbles: true }));
        codeInput.dispatchEvent(new Event('change', { bubbles: true }));
        // Re-query submit button (input event may have enabled it)
        if (submitBtn.disabled) {
          // Try re-finding the button after state change
          var freshBtn = document.getElementById('submit-code');
          if (!freshBtn) {
            document.querySelectorAll('button').forEach(function(btn) {
              if (btn.textContent.trim() === 'Submit Code') freshBtn = btn;
            });
          }
          if (freshBtn && !freshBtn.disabled) submitBtn = freshBtn;
        }
        if (!submitBtn.disabled) {
          submitBtn.click();
          actions.push('AUTO-SUBMITTED code: ' + bestCode);
        } else {
          actions.push('CODE READY but submit button still disabled: ' + bestCode);
        }
      }
    }
  }

  // Inject results into DOM for LLM visibility
  if (opts.reportResults) {
    var resultsDiv = document.getElementById('__pre_solve_results');
    if (!resultsDiv) {
      resultsDiv = document.createElement('div');
      resultsDiv.id = '__pre_solve_results';
      resultsDiv.style.cssText = 'font-size:11px;color:#666;padding:4px;border:1px dashed #ccc;margin:4px 0;background:#fffff0;';
      var insertTarget = document.getElementById('__agent-timestamp');
      if (insertTarget && insertTarget.nextSibling) {
        document.body.insertBefore(resultsDiv, insertTarget.nextSibling);
      } else if (document.body.firstChild) {
        document.body.insertBefore(resultsDiv, document.body.firstChild);
      }
    }
    resultsDiv.textContent = actions.length > 0
      ? 'Pre-solve: ' + actions.join(' | ')
      : 'Pre-solve: No auto-actionable patterns found';
  }

  return actions.length > 0
    ? 'Pre-solve completed: ' + actions.join('; ')
    : 'No auto-actionable patterns found';
}
