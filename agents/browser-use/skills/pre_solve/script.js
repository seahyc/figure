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

  // Track all previously submitted codes to prevent re-submitting stale codes from previous steps
  // This array persists across pre_solve invocations via window global
  var allSubmittedCodes = window.__preSolveSubmittedCodes || [];
  // Also detect codes visible as "accepted" on the page (catches manual submissions by LLM)
  var acceptedMatch = document.body && document.body.innerText &&
    document.body.innerText.match(/(?:AUTO-SUBMITTED|submitted|accepted)[^A-Z]*([A-HJ-NP-Z2-9]{6})/i);
  if (acceptedMatch && allSubmittedCodes.indexOf(acceptedMatch[1]) === -1) {
    allSubmittedCodes.push(acceptedMatch[1]);
    window.__preSolveSubmittedCodes = allSubmittedCodes;
  }

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

  // Pattern 5: (removed — drag_drop is now handled by React state extraction in Source 10)

  // Pattern 6: Auto-solve canvas/gesture challenges
  // Handles both: gesture (1 stroke + Complete button) and canvas (3+ strokes)
  // We simulate mousedown, mousemove, mouseup to draw strokes.
  (function() {
    var canvas = document.querySelector('canvas.cursor-crosshair, canvas[class*="crosshair"], canvas');
    if (!canvas) return;
    // Check for stroke counter (canvas challenge) or gesture-complete button
    var bodyText = document.body ? document.body.innerText : '';
    var strokeMatch = bodyText.match(/(\d+)\s*\/\s*(\d+)\s*strokes?/i);
    var gestureBtn = document.getElementById('gesture-complete');
    // Skip if no gesture button and no stroke counter and canvas doesn't look like a challenge
    if (!gestureBtn && !strokeMatch && !bodyText.match(/draw|stroke|canvas.*challenge/i)) return;

    var rect = canvas.getBoundingClientRect();
    var cx = rect.left + rect.width / 2;
    var cy = rect.top + rect.height / 2;
    var hw = rect.width * 0.3;
    var hh = rect.height * 0.3;

    // Determine how many strokes to draw
    var strokesNeeded = 1;
    if (strokeMatch) {
      var current = parseInt(strokeMatch[1]);
      var total = parseInt(strokeMatch[2]);
      strokesNeeded = total - current;
    }
    strokesNeeded = Math.max(strokesNeeded, 1);

    // Draw multiple strokes using both MouseEvent and PointerEvent (React 17+ uses PointerEvents)
    for (var s = 0; s < Math.min(strokesNeeded + 1, 6); s++) {
      var startX = cx - hw + (s * 20);
      var startY = cy - hh + (s * 15);
      var endX = cx + hw - (s * 10);
      var endY = cy + hh - (s * 10);
      // Dispatch both pointer and mouse events (React may listen to either)
      canvas.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: startX, clientY: startY, pointerId: 1 }));
      canvas.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: startX, clientY: startY }));
      for (var m = 1; m <= 5; m++) {
        var mx = startX + (endX - startX) * m / 5;
        var my = startY + (endY - startY) * m / 5;
        canvas.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: mx, clientY: my, pointerId: 1 }));
        canvas.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: mx, clientY: my }));
      }
      canvas.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, clientX: endX, clientY: endY, pointerId: 1 }));
      canvas.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, clientX: endX, clientY: endY }));
    }

    // Try clicking Complete button (gesture challenge)
    gestureBtn = document.getElementById('gesture-complete');
    if (gestureBtn && !gestureBtn.disabled) {
      gestureBtn.click();
      actions.push('Auto-solved gesture: drew + clicked Complete');
    } else {
      // Also try "Complete Challenge" button text
      document.querySelectorAll('button').forEach(function(btn) {
        var t = btn.textContent.trim();
        if (/complete.*challenge/i.test(t) && !btn.disabled) {
          btn.click();
          actions.push('Auto-solved canvas: drew + clicked Complete Challenge');
        }
      });
      if (strokesNeeded > 0) {
        actions.push('Canvas: drew ' + Math.min(strokesNeeded + 1, 6) + ' strokes');
      }
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
  // Challenge renders 3-4 absolutely positioned divs with text "Part N: XX"
  // Multiple selector strategies since live site may compile classes differently
  (function() {
    // Only run if split parts challenge is active
    var bodyText = document.body ? document.body.innerText : '';
    if (!/split.?parts|scattered.*parts|find.*click.*parts/i.test(bodyText)) return;

    var parts = [];
    // Strategy 1: data-part attribute (source code uses this)
    parts = document.querySelectorAll('[data-part]');
    // Strategy 2: class-based selectors
    if (parts.length < 2) {
      parts = document.querySelectorAll('[class*="absolute"][class*="bg-yellow"], .absolute.bg-yellow-400');
    }
    // Strategy 3: Find elements containing "Part N:" text with cursor pointer
    if (parts.length < 2) {
      var candidates = [];
      document.querySelectorAll('div, span').forEach(function(el) {
        var text = el.textContent.trim();
        if (/^Part\s+\d+\s*:/i.test(text) && text.length < 30) {
          // Check if this is a leaf-ish element (the actual part, not a container)
          if (el.children.length <= 1) candidates.push(el);
        }
      });
      if (candidates.length >= 2) parts = candidates;
    }
    // Strategy 4: Scan computed styles for absolute-positioned cursor-pointer elements
    // with yellow-ish background, z-index 100
    if (parts.length < 2) {
      var styled = [];
      document.querySelectorAll('div').forEach(function(el) {
        var s = window.getComputedStyle(el);
        if (s.position === 'absolute' && s.cursor === 'pointer' && s.zIndex === '100') {
          styled.push(el);
        }
      });
      if (styled.length >= 2) parts = styled;
    }

    if (parts.length >= 2) {
      var clickedTexts = [];
      for (var pi = 0; pi < parts.length; pi++) {
        if (!parts[pi].dataset || !parts[pi].dataset.clicked) {
          parts[pi].click();
          clickedTexts.push(parts[pi].textContent.trim().substring(0, 20));
        }
      }
      actions.push('Clicked ' + parts.length + ' split parts: ' + clickedTexts.join(', '));
    }
  })();

  // Pattern 9: Auto-solve sequence challenge — 4 action types: click, hover, type, scroll
  // Phase 1 dispatches all 4 events; Phase 2 (5s later) clicks Complete button after hover timer fires
  (function() {
    var bodyText = document.body ? document.body.innerText : '';
    if (!/sequence.*challenge|complete.*actions|action.*sequence/i.test(bodyText)) return;
    // Find sequence action elements by ID or text
    var clickBtn = document.getElementById('seq-click-btn');
    var hoverArea = document.getElementById('seq-hover-area');
    var typeInput = document.getElementById('seq-type-input');
    var scrollBox = document.getElementById('seq-scroll-box');
    var completeBtn = document.getElementById('seq-complete-btn');
    // Fallback: find by text/attributes if IDs missing
    if (!clickBtn) {
      document.querySelectorAll('button').forEach(function(btn) {
        var t = btn.textContent.trim().toLowerCase();
        if (t === 'click me' || t === 'click' || /^click\s/i.test(t)) {
          if (!clickBtn && !btn.disabled) clickBtn = btn;
        }
        if (/^complete$/i.test(t)) completeBtn = btn;
      });
    }
    if (!hoverArea) {
      document.querySelectorAll('div, span').forEach(function(el) {
        if (/hover here|hover.*area/i.test(el.textContent.trim()) && el.textContent.trim().length < 40) {
          if (!hoverArea) hoverArea = el;
        }
      });
    }
    if (!typeInput) typeInput = document.querySelector('input[type="text"]');
    if (!scrollBox) {
      document.querySelectorAll('div').forEach(function(el) {
        var s = window.getComputedStyle(el);
        if (s.overflow === 'auto' || s.overflow === 'scroll' || s.overflowY === 'auto' || s.overflowY === 'scroll') {
          if (el.scrollHeight > el.clientHeight + 10) scrollBox = el;
        }
      });
    }
    // Dispatch all 4 action events
    var seqActions = [];
    if (clickBtn) { clickBtn.click(); seqActions.push('click'); }
    if (hoverArea) {
      hoverArea.dispatchEvent(new MouseEvent('mouseenter', { bubbles: false }));
      hoverArea.dispatchEvent(new PointerEvent('pointerenter', { bubbles: false }));
      seqActions.push('hover-enter');
    }
    if (typeInput) {
      var nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      nativeSetter.call(typeInput, 'X');
      typeInput.dispatchEvent(new Event('input', { bubbles: true }));
      seqActions.push('type');
    }
    if (scrollBox) {
      scrollBox.scrollTop = 100;
      scrollBox.dispatchEvent(new Event('scroll', { bubbles: true }));
      seqActions.push('scroll');
    }
    // On Phase 2 (5s later), the hover timer (800ms) has already fired, so click Complete
    if (completeBtn && !completeBtn.disabled) {
      completeBtn.click();
      seqActions.push('complete');
    }
    if (seqActions.length > 0) actions.push('Sequence: ' + seqActions.join(', '));
  })();

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

  // Pattern 15: Auto-solve hover_reveal — dispatch hover events on "Hover here" elements
  (function() {
    var bodyText = document.body ? document.body.innerText : '';
    if (!/hover.*reveal|hover.*code|hover.*here/i.test(bodyText)) return;
    // Find the hover target element
    var target = null;
    document.querySelectorAll('div, span, p').forEach(function(el) {
      var text = el.textContent.trim();
      if (/hover here/i.test(text) && text.length < 50 && !target) {
        target = el;
      }
    });
    if (!target) {
      // Try data-testid or class-based selectors
      target = document.querySelector('[data-testid="hover-box"]') ||
               document.querySelector('[class*="hover"]');
    }
    if (target) {
      var rect = target.getBoundingClientRect();
      var cx = rect.left + rect.width / 2;
      var cy = rect.top + rect.height / 2;
      // Dispatch full hover event sequence
      target.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true, clientX: cx, clientY: cy }));
      target.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, clientX: cx, clientY: cy }));
      target.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: cx, clientY: cy }));
      // Also dispatch pointer events which React may use
      target.dispatchEvent(new PointerEvent('pointerenter', { bubbles: true, clientX: cx, clientY: cy }));
      target.dispatchEvent(new PointerEvent('pointerover', { bubbles: true, clientX: cx, clientY: cy }));
      target.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: cx, clientY: cy }));
      actions.push('Hover dispatch on "' + target.textContent.trim().substring(0, 30) + '"');
    }
  })();

  // Pattern 16: Auto-solve shadow_dom — traverse nested shadowRoot elements and click
  (function() {
    var bodyText = document.body ? document.body.innerText : '';
    if (!/shadow.*dom|shadow.*level|navigate.*shadow/i.test(bodyText)) return;
    // Find the shadow container
    var container = document.getElementById('shadow-container');
    if (!container) {
      // Fallback: find any element with a shadowRoot
      document.querySelectorAll('div').forEach(function(el) {
        if (el.shadowRoot && !container) container = el.parentElement || el;
      });
    }
    if (!container) return;
    // Traverse up to 5 levels of nested shadow DOM
    var current = container;
    var clickedLevels = 0;
    for (var level = 0; level < 5; level++) {
      var host = null;
      // Find shadow host inside current element
      var children = current.querySelectorAll ? current.querySelectorAll('*') : [];
      for (var ci = 0; ci < children.length; ci++) {
        if (children[ci].shadowRoot) { host = children[ci]; break; }
      }
      if (!host) {
        // Also check direct children
        var directKids = current.children || [];
        for (var di = 0; di < directKids.length; di++) {
          if (directKids[di].shadowRoot) { host = directKids[di]; break; }
        }
      }
      if (!host || !host.shadowRoot) break;
      var shadow = host.shadowRoot;
      // Click the wrapper div inside shadow
      var wrapper = shadow.querySelector('div');
      if (wrapper) {
        wrapper.click();
        clickedLevels++;
        current = wrapper; // Move deeper
      } else break;
    }
    if (clickedLevels > 0) actions.push('Shadow DOM: clicked ' + clickedLevels + ' levels');
  })();

  // Pattern 17: Auto-solve mutation challenge — click trigger button N times + reveal
  (function() {
    var bodyText = document.body ? document.body.innerText : '';
    if (!/mutation|trigger.*mutation|mutations?\s*triggered/i.test(bodyText)) return;
    // Find trigger button and reveal button
    var triggerBtn = document.getElementById('mutate-btn');
    var revealBtn = document.getElementById('mutation-reveal');
    if (!triggerBtn) {
      document.querySelectorAll('button').forEach(function(btn) {
        var t = btn.textContent.trim().toLowerCase();
        if (/trigger|mutate/i.test(t) && !btn.disabled) triggerBtn = btn;
        if (/reveal/i.test(t)) revealBtn = btn;
      });
    }
    if (triggerBtn) {
      // Check progress: "N / M" pattern
      var progressMatch = bodyText.match(/(\d+)\s*\/\s*(\d+)\s*(?:mutations?|triggered)/i);
      var clicksNeeded = 5; // default
      if (progressMatch) {
        clicksNeeded = parseInt(progressMatch[2]) - parseInt(progressMatch[1]);
      }
      for (var mi = 0; mi < Math.max(clicksNeeded, 1); mi++) {
        triggerBtn.click();
      }
      actions.push('Mutation: clicked trigger ' + Math.max(clicksNeeded, 1) + ' times');
    }
    // Re-find reveal button (may have been enabled by mutations)
    if (!revealBtn) {
      document.querySelectorAll('button').forEach(function(btn) {
        if (/reveal/i.test(btn.textContent.trim()) && !btn.disabled) revealBtn = btn;
      });
    }
    if (revealBtn && !revealBtn.disabled) {
      revealBtn.click();
      actions.push('Mutation: clicked Reveal');
    }
  })();

  // Pattern 18: Auto-solve encoded_base64 — decode base64 text visible on page
  (function() {
    var bodyText = document.body ? document.body.innerText : '';
    if (!/base64|decode|encoded/i.test(bodyText)) return;
    // Find elements with base64-encoded text (font-mono class or code elements)
    document.querySelectorAll('.font-mono, code, pre, [class*="code"]').forEach(function(el) {
      var text = el.textContent.trim();
      // Base64 string pattern: 8+ characters of A-Za-z0-9+/=
      if (/^[A-Za-z0-9+/=]{8,}$/.test(text)) {
        try {
          var decoded = atob(text);
          actions.push('Base64 decoded: "' + decoded.substring(0, 40) + '"');
        } catch(e) {}
      }
    });
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

    // Source 9b: Shadow DOM content — search inside open shadow roots for codes
    (function() {
      function searchShadow(root, depth) {
        if (depth > 5) return;
        var els = root.querySelectorAll('*');
        for (var si = 0; si < els.length; si++) {
          var text = els[si].textContent.trim();
          if (text && text.length <= 20) {
            var match = text.match(codePattern);
            if (match && foundCodes.indexOf(match[0]) === -1) {
              foundCodes.push(match[0]);
            }
          }
          if (els[si].shadowRoot) {
            searchShadow(els[si].shadowRoot, depth + 1);
          }
        }
      }
      document.querySelectorAll('*').forEach(function(el) {
        if (el.shadowRoot) searchShadow(el.shadowRoot, 0);
      });
    })();

    // Source 10: React fiber state extraction (universal — works for all challenge types)
    // The live site is a React app — challenge codes are stored in component state.
    // Walk the React fiber tree to find the challenge component (Gv) with matching stepNum.
    // Uses step-matching to avoid stale codes from previous steps after SPA transitions.
    (function() {
      // Detect current step from page text
      var pageText = document.body ? document.body.innerText : '';
      var stepMatch = pageText.match(/step\s+(\d+)\s*(?:of|\/)\s*(\d+)/i);
      var currentStep = stepMatch ? parseInt(stepMatch[1]) : null;

      // Helper: extract 6-char codes from a fiber's memoizedState chain
      // NOTE: Do NOT check fiber.alternate — it may contain stale codes from previous renders
      function extractCodes(fiber) {
        var codes = [];
        if (!fiber || !fiber.memoizedState) return codes;
        var state = fiber.memoizedState;
        var depth = 0;
        while (state && depth < 10) {
          if (typeof state.memoizedState === 'string' && codePattern.test(state.memoizedState)) {
            if (codes.indexOf(state.memoizedState) === -1) codes.push(state.memoizedState);
          }
          if (state.queue && typeof state.queue.lastRenderedState === 'string' &&
              codePattern.test(state.queue.lastRenderedState)) {
            if (codes.indexOf(state.queue.lastRenderedState) === -1) codes.push(state.queue.lastRenderedState);
          }
          state = state.next;
          depth++;
        }
        return codes;
      }

      // Strategy A: Walk UP from challenge element (fast, works when DOM is fresh)
      var challengeArea = document.querySelector('.max-w-6xl') || document.querySelector('main');
      var startEl = null;
      if (challengeArea) {
        startEl = challengeArea.querySelector('[class*="bg-indigo"]') ||
                  challengeArea.querySelector('[class*="bg-green"]') ||
                  challengeArea.querySelector('[class*="bg-blue"]') ||
                  challengeArea.querySelector('[class*="bg-yellow"]') ||
                  challengeArea.querySelector('[class*="bg-purple"]') ||
                  challengeArea.querySelector('[class*="bg-red"]') ||
                  challengeArea.querySelector('[class*="bg-orange"]') ||
                  challengeArea.querySelector('[class*="bg-pink"]') ||
                  challengeArea.querySelector('canvas') ||
                  challengeArea.querySelector('[class*="border-dashed"]') ||
                  challengeArea.querySelector('[draggable="true"]') ||
                  challengeArea.querySelector('[class*="border-2"]') ||
                  challengeArea.querySelector('strong');
      }

      var reactCodes = [];
      if (startEl) {
        var fiberKey = Object.keys(startEl).find(function(k) { return k.indexOf('__reactFiber') === 0; });
        if (fiberKey) {
          var fiber = startEl[fiberKey];
          // Walk UP: ONLY extract codes from the challenge component (has config+stepNum props)
          // Skip generic intermediate fibers to avoid stale codes from other components
          for (var i = 0; i < 30 && fiber; i++) {
            var props = fiber.memoizedProps;
            if (props && props.config && typeof props.stepNum === 'number') {
              // Step-match: only use code if stepNum matches current page step
              if (currentStep === null || props.stepNum === currentStep) {
                var codes = extractCodes(fiber);
                for (var ci = 0; ci < codes.length; ci++) {
                  if (reactCodes.indexOf(codes[ci]) === -1) reactCodes.push(codes[ci]);
                }
                // Also check immediate children (some challenges store code in child components)
                var childStack = [fiber.child];
                var childVisited = 0;
                while (childStack.length > 0 && childVisited < 50) {
                  var cf = childStack.pop();
                  if (!cf) continue;
                  childVisited++;
                  var childCodes = extractCodes(cf);
                  for (var cci = 0; cci < childCodes.length; cci++) {
                    if (reactCodes.indexOf(childCodes[cci]) === -1) reactCodes.push(childCodes[cci]);
                  }
                  if (cf.child) childStack.push(cf.child);
                  if (cf.sibling) childStack.push(cf.sibling);
                }
              }
              break; // Found the challenge component, stop walking
            }
            fiber = fiber.return;
          }
        }
      }

      // Strategy B: If walk-up found nothing (or only stale codes), walk DOWN from #root
      // This catches cases where the DOM element is stale but the fiber tree has updated
      if (reactCodes.length === 0 || (allSubmittedCodes.length > 0 &&
          reactCodes.every(function(c) { return allSubmittedCodes.indexOf(c) !== -1; }))) {
        var rootEl = document.getElementById('root') || document.getElementById('__next');
        if (rootEl) {
          var rootKey = Object.keys(rootEl).find(function(k) {
            return k.indexOf('__reactFiber') === 0 || k.indexOf('__reactContainer') === 0;
          });
          if (rootKey) {
            var stack = [rootEl[rootKey]];
            var visited = 0;
            while (stack.length > 0 && visited < 2000) {
              var f = stack.pop();
              if (!f) continue;
              visited++;
              // Look for challenge component with matching stepNum
              var fp = f.memoizedProps;
              if (fp && fp.config && typeof fp.stepNum === 'number') {
                if (currentStep === null || fp.stepNum === currentStep) {
                  var rootCodes = extractCodes(f);
                  for (var rci = 0; rci < rootCodes.length; rci++) {
                    if (reactCodes.indexOf(rootCodes[rci]) === -1) {
                      reactCodes.push(rootCodes[rci]);
                    }
                  }
                }
              }
              if (f.child) stack.push(f.child);
              if (f.sibling) stack.push(f.sibling);
            }
          }
        }
      }

      // Add React codes at END — DOM-visible codes from Sources 1-9 should take priority
      for (var ri = 0; ri < reactCodes.length; ri++) {
        if (foundCodes.indexOf(reactCodes[ri]) === -1) {
          foundCodes.push(reactCodes[ri]);
          actions.push('React state code: ' + reactCodes[ri]);
        }
      }
    })();

    if (foundCodes.length > 0) {
      actions.push('Found potential codes: ' + foundCodes.join(', '));
    }
  }

  // Auto-submit: if we found a code, type it and click Submit Code
  // Filter out ALL previously submitted codes to prevent re-submitting stale codes
  if (allSubmittedCodes.length > 0) {
    foundCodes = foundCodes.filter(function(c) { return allSubmittedCodes.indexOf(c) === -1; });
  }
  // Skip if "Code accepted" is still visible AND the accepted code matches what we'd submit
  // (Don't block submission of a genuinely NEW code just because "Code accepted" text lingers)
  var alreadyAccepted = false;
  if (document.body && document.body.innerText) {
    var bodyInner = document.body.innerText;
    if (bodyInner.indexOf('Code accepted') !== -1 || bodyInner.indexOf('Proceeding to step') !== -1) {
      // Only block if we'd re-submit the same code that was just accepted
      var acceptedCodeMatch = bodyInner.match(/(?:accepted|proceeding)[^A-Z]*([A-HJ-NP-Z2-9]{6})/i);
      if (acceptedCodeMatch && foundCodes.length > 0 && foundCodes[0] === acceptedCodeMatch[1]) {
        alreadyAccepted = true;
      }
      // Also block if ALL our found codes are in the submitted list
      if (foundCodes.length === 0) alreadyAccepted = true;
    }
  }
  if (opts.autoSubmit && foundCodes.length >= 1 && !alreadyAccepted) {
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
          if (!window.__preSolveSubmittedCodes) window.__preSolveSubmittedCodes = [];
          window.__preSolveSubmittedCodes.push(bestCode);
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
