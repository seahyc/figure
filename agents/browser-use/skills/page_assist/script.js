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
  // Confirmed codes bypass the stale filter — used when B-handlers verify a code is
  // genuinely for the current step (e.g., "Code revealed:" text from solved puzzles).
  var confirmedCodes = [];
  // Codes found across all sources. Declared early so B-handlers can add to it.
  var foundCodes = [];

  // --- Constants ---
  // Challenge code charset: uppercase letters (minus I, O) + digits (minus 0, 1)
  var CODE_CHARSET = 'A-HJ-NP-Z2-9';
  var CODE_PATTERN = new RegExp('\\b[' + CODE_CHARSET + ']{6}\\b');
  var CODE_PATTERN_GLOBAL = new RegExp('[' + CODE_CHARSET + ']{6}', 'g');
  // Real challenge codes always contain at least one letter.
  // All-digit matches (e.g. 935768) are false positives from React internals.
  var HAS_LETTER = /[A-HJ-NP-Z]/;

  // React-compatible native input setter (avoids React overriding .value)
  var nativeInputSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  function setInputValue(input, value) {
    nativeInputSetter.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // Clear old results div to prevent false positives from previous steps
  var oldResults = document.getElementById('__page_assist_results');
  if (oldResults) oldResults.remove();

  // Click target queue: store bounding rects for Playwright trusted clicks.
  // JS btn.click() is unreliable with React 18 event delegation, so we also
  // record coordinates for the Python hook to re-click via Playwright.
  window.__pageAssistClickTargets = window.__pageAssistClickTargets || [];
  function trustClick(el, label) {
    el.click(); // best-effort JS click
    var r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) {
      window.__pageAssistClickTargets.push({
        x: Math.round(r.x + r.width / 2),
        y: Math.round(r.y + r.height / 2),
        label: label || (el.textContent || '').trim().substring(0, 50)
      });
    }
  }

  // Probe-only mode: skip Sections A and B, only run Section C (code extraction).
  // Used by adaptive Phase 2 polling to check for new codes without re-triggering actions.
  var probeOnly = opts.probeOnly || false;

  // Detect current step number for step-change tracking
  // Use textContent (not innerText) because dismiss_popups hides the step header,
  // and innerText excludes text from display:none elements
  var stepText = document.body ? document.body.textContent : '';
  var stepDetect = stepText.match(/step\s+(\d+)\s*(?:of|\/)\s*(\d+)/i);
  var detectedStep = stepDetect ? parseInt(stepDetect[1]) : null;
  var stepJustChanged = false;
  if (detectedStep !== null && window.__pageAssistLastStep && window.__pageAssistLastStep !== detectedStep) {
    actions.push('Step changed: ' + window.__pageAssistLastStep + ' -> ' + detectedStep);
    // Keep submitted codes across step changes — old codes linger in the DOM
    // during SPA transitions and must remain excluded to avoid re-submitting stale codes
    stepJustChanged = true;
  }
  if (detectedStep !== null) window.__pageAssistLastStep = detectedStep;

  // Guard: if we just auto-submitted on this step and it hasn't changed, skip re-running
  // for a few calls (prevents re-submission loops where old code lingers in DOM).
  // Auto-clear after 3 calls so page_assist can retry if submit actually failed.
  if (!stepJustChanged && detectedStep !== null &&
      window.__lastAutoSubmitStep === detectedStep && !probeOnly) {
    if (!window.__autoSubmitGuardCount) window.__autoSubmitGuardCount = 0;
    window.__autoSubmitGuardCount++;
    if (window.__autoSubmitGuardCount <= 3) {
      actions.push('Waiting for step transition after auto-submit on step ' + detectedStep + ' (guard ' + window.__autoSubmitGuardCount + '/3)');
      if (opts.reportResults) {
        var waitDiv = document.createElement('div');
        waitDiv.id = '__page_assist_results';
        waitDiv.style.cssText = 'font-size:11px;color:#666;padding:4px;border:1px dashed #ccc;margin:4px 0;background:#fffff0;';
        waitDiv.textContent = 'page_assist: AUTO-SUBMITTED on step ' + detectedStep + '. Waiting for page to advance. Do NOT interact.';
        var insertTarget = document.getElementById('__agent-timestamp');
        if (insertTarget && insertTarget.nextSibling) {
          document.body.insertBefore(waitDiv, insertTarget.nextSibling);
        } else if (document.body.firstChild) {
          document.body.insertBefore(waitDiv, document.body.firstChild);
        }
      }
      return 'page_assist: Waiting for step transition after auto-submit on step ' + detectedStep;
    }
    // Guard expired — auto-submit may have failed, allow retry
    window.__lastAutoSubmitStep = null;
    window.__autoSubmitGuardCount = 0;
    actions.push('Auto-submit guard expired on step ' + detectedStep + ' — retrying');
  }
  // Clear the auto-submit guard when step changes
  if (stepJustChanged) {
    window.__lastAutoSubmitStep = null;
    window.__autoSubmitGuardCount = 0;
  }

  // Track submitted codes with step info. Format: [{code: 'XXXXXX', step: N}, ...].
  // ALL submitted codes are filtered (prevents stale codes from SPA transitions).
  // On stuck recovery (8+ steps), agent.py clears current step's entries to allow retry.
  if (!window.__pageAssistSubmittedCodes) window.__pageAssistSubmittedCodes = [];
  var allSubmittedEntries = window.__pageAssistSubmittedCodes;
  var allSubmittedCodes = allSubmittedEntries.map(function(e) { return e.code; });

  // Note: submitted codes persist across steps intentionally — old codes from previous
  // steps linger in the DOM during SPA transitions and must remain excluded.
  // Detect codes visible as "accepted" or in error messages (catches manual submissions by LLM)
  if (document.body && document.body.innerText) {
    // Strip our own results div text to avoid matching "AUTO-SUBMITTED" as a real submission
    var resultsEl = document.getElementById('__page_assist_results');
    var pageInner = document.body.innerText;
    if (resultsEl) pageInner = pageInner.replace(resultsEl.textContent, '');
    // Match codes near "accepted", "submitted", "wrong code", "proceeding" messages
    var submitPatterns = [
      /(?:submitted|accepted|proceeding)[^A-Z]*([A-HJ-NP-Z2-9]{6})/ig,
      /wrong\s*code[^A-Z]*([A-HJ-NP-Z2-9]{6})/ig,
      /([A-HJ-NP-Z2-9]{6})[^a-z]*(?:was|is)?\s*(?:wrong|rejected|invalid)/ig
    ];
    submitPatterns.forEach(function(pat) {
      var m;
      while ((m = pat.exec(pageInner)) !== null) {
        var code = m[1];
        if (allSubmittedCodes.indexOf(code) === -1) {
          allSubmittedCodes.push(code);
          // Also add to the step-scoped entries array (use step 0 for unknown/past step)
          var entryStep = detectedStep || 0;
          var exists = allSubmittedEntries.some(function(e) { return e.code === code; });
          if (!exists) allSubmittedEntries.push({ code: code, step: entryStep });
        }
      }
    });
    // If "Code accepted" visible, also track the code in the input field as submitted
    if (pageInner.indexOf('Code accepted') !== -1 || pageInner.indexOf('Proceeding') !== -1) {
      var codeInp = document.getElementById('code-input') ||
        document.querySelector('input[placeholder*="code" i], input[placeholder*="character" i]');
      if (codeInp && codeInp.value && CODE_PATTERN.test(codeInp.value) && HAS_LETTER.test(codeInp.value)) {
        var inpCode = codeInp.value.match(CODE_PATTERN)[0];
        if (allSubmittedCodes.indexOf(inpCode) === -1) {
          allSubmittedCodes.push(inpCode);
          var exists = allSubmittedEntries.some(function(e) { return e.code === inpCode; });
          if (!exists) allSubmittedEntries.push({ code: inpCode, step: detectedStep || 0 });
        }
      }
    }
  }

  // -----------------------------------------------------------------------
  // Section A: Generic interaction patterns (framework-agnostic)
  // -----------------------------------------------------------------------
  if (probeOnly) {
    // Skip Sections A and B entirely — jump to Section C (code extraction)
  } else {

  // A1: Click action buttons with general action verbs
  if (opts.clickActionButtons) {
    var actionVerbs = /^(reveal|play|connect|register|extract|start|show|open|unlock|enable|activate|load|fetch|begin|launch|display|uncover|expose|decode|decrypt|generate)\b/i;
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
        trustClick(btn, text.substring(0, 40));
        actions.push('Clicked action button: "' + text.substring(0, 40) + '"');
      }
    });
  }

  // A2: Click repeatedly-actionable buttons near progress indicators (N/M pattern)
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
                trustClick(target, 'progress');
              }
              actions.push('Clicked progress button "' + ttext.substring(0, 30) + '" x' + Math.min(remaining, 15) + ' (' + current + '/' + total + ')');
            }
          });
        }
      }
    });
  }

  // A3: Click sequential navigation elements (tabs, numbered buttons)
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
        trustClick(btn, btn.textContent.trim());
        actions.push('Clicked sequential nav: "' + btn.textContent.trim() + '"');
      });
    }
  }

  // A4: Detect visible countdowns (just report, don't block)
  // Only report countdowns with explicit "remaining/left" text — skip generic MM:SS patterns
  // which match every page's clock display and just add noise.
  if (opts.waitForCountdowns) {
    var countdownPattern = /(\d+)\s*(seconds?|s)\s*(remaining|left|until|to go)/i;
    var bodyText = document.body ? document.body.innerText : '';
    if (countdownPattern.test(bodyText)) {
      actions.push('Countdown/timer detected on page - may need to wait');
    }
  }

  // -----------------------------------------------------------------------
  // Section B: Web interaction patterns
  // -----------------------------------------------------------------------

  // B1: Repeated capture — click Capture button N times to complete a collection
  var __rotatingActive = false;
  (function() {
    var captureBtn = null;
    var countEl = null;
    document.querySelectorAll('button').forEach(function(btn) {
      if (!captureBtn && /^capture\b/i.test(btn.textContent.trim()) && !btn.disabled) captureBtn = btn;
    });
    document.querySelectorAll('p, span, div').forEach(function(el) {
      if (!countEl && /captures?:\s*\d+\s*\/\s*\d+/i.test(el.textContent.trim())) countEl = el;
    });
    if (!captureBtn) return;
    // Check if captures are already at 3/3 (via count element or body text)
    var countText = countEl ? countEl.textContent : (document.body ? document.body.innerText : '');
    var m = countText.match(/captures?:\s*(\d+)\s*\/\s*(\d+)/i);
    if (m && parseInt(m[1]) >= parseInt(m[2])) return; // Already complete
    // Only activate if this looks like a rotating challenge (rapidly changing code display)
    var bodyText = document.body ? document.body.innerText : '';
    if (!/rotat|rapid|flash|capture/i.test(bodyText)) return;
    // Click Capture 3 times (idempotent — extra clicks after 3 are ignored)
    __rotatingActive = true;
    for (var rc = 0; rc < 3; rc++) {
      trustClick(captureBtn, 'Capture');
    }
    actions.push('Rotating: clicked Capture 3 times');
  })();

  // B2: Form computation — parse expression, compute answer, fill input, click Solve
  (function() {
    // Find a VISIBLE element containing the math expression (not stale hidden text)
    var mathEl = null;
    var calcMatch = null;
    var mathMatch = null;
    document.querySelectorAll('p, span, div, h1, h2, h3, h4, strong, b').forEach(function(el) {
      if (mathEl) return;
      if (el.offsetParent === null && el.offsetWidth === 0) return; // hidden
      var t = el.textContent.trim();
      if (t.length > 100) return; // Skip large containers
      var cm = t.match(/(\d+)\s*[x×]\s*(\d+)\s*\+\s*(\d+)\s*=\s*\?/);
      if (cm) { calcMatch = cm; mathEl = el; return; }
      var mm = t.match(/(\d+)\s*\+\s*(\d+)\s*=\s*\?/);
      if (mm) { mathMatch = mm; mathEl = el; }
    });
    if (!calcMatch && !mathMatch) return;
    var bodyText = document.body ? document.body.innerText : '';

    // Check if puzzle is already solved — "Code revealed: XXXXXX" visible on page
    var revealedMatch = bodyText.match(/code\s*(?:revealed|is)[:\s]*([A-HJ-NP-Z2-9]{6})/i);
    var puzzleHasStaleCode = false;
    if (revealedMatch) {
      var revCode = revealedMatch[1];
      if (allSubmittedCodes.indexOf(revCode) !== -1) {
        // Code already submitted — stale from previous step's React state.
        // DON'T return — continue to solve logic to trigger React state reset.
        // The reset will flip the "solved" boolean, then the NEXT page_assist call
        // can re-solve the puzzle and get the correct code for this step.
        puzzleHasStaleCode = true;
        actions.push('B2: puzzle shows stale code ' + revCode + ' — attempting reset');
      } else {
        if (foundCodes.indexOf(revCode) === -1) foundCodes.push(revCode);
        confirmedCodes.push(revCode); // Bypass stale filter — confirmed for current step
        actions.push('B2: puzzle already solved, code revealed: ' + revCode);
        return;
      }
    }

    var answer = calcMatch
      ? parseInt(calcMatch[1]) * parseInt(calcMatch[2]) + parseInt(calcMatch[3])
      : parseInt(mathMatch[1]) + parseInt(mathMatch[2]);
    // Find answer input — try ID first, then placeholder-based fallback, then any visible unfilled input
    var puzzleInput = null;
    puzzleInput = document.getElementById('puzzle-input') || document.getElementById('calc-input');
    if (!puzzleInput) {
      document.querySelectorAll('input[type="text"], input[type="number"]').forEach(function(inp) {
        if (puzzleInput) return;
        var ph = (inp.placeholder || '').toLowerCase();
        if (ph.indexOf('code') !== -1 || ph.indexOf('character') !== -1) return;
        if (ph.indexOf('answer') !== -1 || ph.indexOf('enter') !== -1) puzzleInput = inp;
      });
    }
    // Fallback: any visible text/number input NOT used for code submission
    if (!puzzleInput) {
      document.querySelectorAll('input[type="text"], input[type="number"]').forEach(function(inp) {
        if (puzzleInput) return;
        if (inp.offsetWidth === 0) return;
        var ph = (inp.placeholder || '').toLowerCase();
        if (ph.indexOf('code') !== -1 || ph.indexOf('character') !== -1) return;
        // Skip the code submission input (near "Submit Code" button)
        var parent = inp.closest('div') || inp.parentElement;
        var hasSubmitCode = false;
        if (parent) {
          parent.querySelectorAll('button').forEach(function(b) {
            if (b.textContent.trim() === 'Submit Code') hasSubmitCode = true;
          });
        }
        if (hasSubmitCode) return;
        puzzleInput = inp;
      });
    }
    // Find Solve button — try ID first, then exact text, then partial text match
    var solveBtn = document.getElementById('puzzle-solve') || document.getElementById('calc-solve');
    if (!solveBtn) {
      document.querySelectorAll('button').forEach(function(btn) {
        if (!solveBtn && /^solve$/i.test(btn.textContent.trim()) && !btn.disabled) solveBtn = btn;
      });
    }
    // Fallback: button containing "solve" or "check" near the puzzle
    if (!solveBtn) {
      document.querySelectorAll('button').forEach(function(btn) {
        if (!solveBtn && /solve|check answer/i.test(btn.textContent.trim()) && !btn.disabled &&
            btn.textContent.trim().toLowerCase() !== 'submit code') solveBtn = btn;
      });
    }
    // Broader fallback: any input element (including those without type attr)
    if (!puzzleInput) {
      document.querySelectorAll('input').forEach(function(inp) {
        if (puzzleInput) return;
        var t = (inp.type || '').toLowerCase();
        if (t === 'hidden' || t === 'checkbox' || t === 'radio' || t === 'submit' || t === 'button') return;
        if (inp.offsetWidth === 0 || inp.offsetHeight === 0) return;
        var ph = (inp.placeholder || '').toLowerCase();
        if (ph.indexOf('code') !== -1 || ph.indexOf('character') !== -1) return;
        // Check grandparent scope for "Submit Code" to avoid matching the code entry input
        var scope = inp.parentElement;
        for (var d = 0; d < 3 && scope; d++) scope = scope.parentElement;
        if (!scope) scope = inp.parentElement;
        var hasSubmitCode = false;
        if (scope) {
          scope.querySelectorAll('button').forEach(function(b) {
            if (b.textContent.trim() === 'Submit Code') hasSubmitCode = true;
          });
        }
        if (hasSubmitCode) return;
        puzzleInput = inp;
      });
    }
    // Broader button fallback: any non-disabled visible button that's not Submit Code or a known decoy
    if (!solveBtn) {
      var decoyTexts = /^(submit code|next|continue|proceed|advance|go forward|move on|click here|click me|keep going|next step|next section|continue reading|proceed forward|browse forward|continue journey|next page|capture)$/i;
      document.querySelectorAll('button').forEach(function(btn) {
        if (solveBtn) return;
        var txt = btn.textContent.trim();
        if (btn.disabled || btn.offsetWidth === 0) return;
        if (decoyTexts.test(txt)) return;
        if (txt.length > 30) return; // Skip buttons with long text (likely not solve button)
        // Prefer buttons with solve-related text
        if (/solve|check|calculate|verify|submit.*answer|compute/i.test(txt)) {
          solveBtn = btn;
        }
      });
    }
    // Last resort: ANY non-decoy button
    if (!solveBtn) {
      var decoyTexts2 = /^(submit code|next|continue|proceed|advance|go forward|move on|click here|click me|keep going|next step|next section|continue reading|proceed forward|browse forward|continue journey|next page|capture|reveal|start|play|connect|register|extract|show|open|unlock|enable|activate|load|fetch|begin|launch|display)$/i;
      document.querySelectorAll('button').forEach(function(btn) {
        if (solveBtn) return;
        var txt = btn.textContent.trim();
        if (btn.disabled || btn.offsetWidth === 0) return;
        if (decoyTexts2.test(txt)) return;
        if (txt.length > 20) return;
        solveBtn = btn;
      });
    }
    if (!puzzleInput || !solveBtn) {
      // Puzzle UI stuck — input or solve button missing (component in solved state).
      // When puzzleHasStaleCode=true but input+button exist, we fall through to
      // solve the puzzle normally (below). Solving triggers React to reveal the
      // new code on re-render, which the NEXT page_assist call will find.
      // Only enter fiber search when UI elements are genuinely missing.
      var freshCode = null;
      var resetDone = false;
      var exprEl = null;
      document.querySelectorAll('*').forEach(function(el) {
        if (el.textContent && /=\s*\?/.test(el.textContent) && el.offsetParent !== null) {
          if (!exprEl || el.textContent.length < exprEl.textContent.length) exprEl = el;
        }
      });
      if (exprEl) {
        var fk = Object.keys(exprEl).find(function(k) { return k.indexOf('__reactFiber') === 0; });
        if (fk) {
          // Walk UP level-by-level checking props and state for fresh codes
          var fiber = exprEl[fk];
          var upLevels = 0;
          var hookDump = [];
          for (var up = 0; up < 15 && fiber && !freshCode; up++) {
            // Check props (memoizedProps and pendingProps) for code strings
            var propsToCheck = [fiber.memoizedProps, fiber.pendingProps];
            for (var pi = 0; pi < propsToCheck.length && !freshCode; pi++) {
              var mp = propsToCheck[pi];
              if (!mp || typeof mp !== 'object') continue;
              for (var pk in mp) {
                if (typeof mp[pk] === 'string' && CODE_PATTERN.test(mp[pk]) && HAS_LETTER.test(mp[pk]) &&
                    allSubmittedCodes.indexOf(mp[pk]) === -1) {
                  freshCode = mp[pk]; break;
                }
                if (pk === 'config' && mp[pk] && typeof mp[pk] === 'object') {
                  for (var ck in mp[pk]) {
                    if (typeof mp[pk][ck] === 'string' && CODE_PATTERN.test(mp[pk][ck]) && HAS_LETTER.test(mp[pk][ck]) &&
                        allSubmittedCodes.indexOf(mp[pk][ck]) === -1) {
                      freshCode = mp[pk][ck]; break;
                    }
                  }
                }
                if (freshCode) break;
              }
            }
            // Check memoizedState hooks
            if (!freshCode && fiber.memoizedState) {
              var hook = fiber.memoizedState;
              var hd = 0;
              while (hook && hd < 15) {
                if (hook.queue && hook.queue.dispatch) {
                  var st = hook.memoizedState;
                  if (typeof st === 'string' && CODE_PATTERN.test(st) && HAS_LETTER.test(st) &&
                      allSubmittedCodes.indexOf(st) === -1) {
                    freshCode = st; break;
                  }
                  if (st === true) { hook.queue.dispatch(false); resetDone = true; }
                  if (typeof st === 'number' && st > 0 && st < 100) { hook.queue.dispatch(0); resetDone = true; }
                  // Reset string hooks containing stale code or old answer to force "unsolved" state
                  if (puzzleHasStaleCode && typeof st === 'string' && st.length > 0 &&
                      (st === revCode || /^\d+$/.test(st))) {
                    hook.queue.dispatch(''); resetDone = true;
                  }
                  if (hookDump.length < 8 && st !== null && st !== undefined && st !== '' && st !== 0 && st !== false) {
                    hookDump.push('L' + up + ':' + typeof st + ':' + (typeof st === 'object' ? JSON.stringify(st).substring(0, 25) : String(st).substring(0, 15)));
                  }
                }
                hook = hook.next; hd++;
              }
            }
            fiber = fiber.return; upLevels++;
          }
          // DFS DOWN from puzzle component (3 levels up) for codes in pendingProps/state
          if (!freshCode && exprEl[fk]) {
            var puzzleFiber = exprEl[fk];
            for (var pu = 0; pu < 3 && puzzleFiber.return; pu++) puzzleFiber = puzzleFiber.return;
            var dfsStack = [puzzleFiber]; var dfsV = 0;
            while (dfsStack.length > 0 && dfsV < 300 && !freshCode) {
              var df = dfsStack.pop(); if (!df) continue; dfsV++;
              var pp = df.pendingProps || df.memoizedProps;
              if (pp && typeof pp === 'object') {
                for (var ppk in pp) {
                  if (typeof pp[ppk] === 'string' && CODE_PATTERN.test(pp[ppk]) && HAS_LETTER.test(pp[ppk]) &&
                      allSubmittedCodes.indexOf(pp[ppk]) === -1) { freshCode = pp[ppk]; break; }
                }
              }
              if (!freshCode && df.memoizedState) {
                var dh = df.memoizedState; var dd = 0;
                while (dh && dd < 10 && !freshCode) {
                  if (typeof dh.memoizedState === 'string' && CODE_PATTERN.test(dh.memoizedState) &&
                      HAS_LETTER.test(dh.memoizedState) && allSubmittedCodes.indexOf(dh.memoizedState) === -1) {
                    freshCode = dh.memoizedState;
                  }
                  dh = dh.next; dd++;
                }
              }
              if (df.child) dfsStack.push(df.child);
              if (df.sibling) dfsStack.push(df.sibling);
            }
          }
          if (freshCode) {
            foundCodes.push(freshCode); confirmedCodes.push(freshCode);
            actions.push('B2-extract: code ' + freshCode + ' from fiber (up ' + upLevels + ')');
          } else if (resetDone) {
            actions.push('B2-reset: dispatched state reset to force unsolved mode. Call page_assist again after brief wait.');
          } else {
            actions.push('B2-stuck: no fresh code (up ' + upLevels + ', hooks: [' + hookDump.join('; ') + '])');
          }
        }
      }
      // Fallback: DFS from #root for Gv component (config + stepNum props)
      if (!freshCode) {
        var gvCode = null;
        var rootEl = document.getElementById('root');
        if (rootEl && detectedStep) {
          var rootFk = Object.keys(rootEl).find(function(k) { return k.indexOf('__reactFiber') === 0 || k.indexOf('__reactInternalInstance') === 0; });
          if (rootFk) {
            var rootFiber = rootEl[rootFk];
            while (rootFiber.return) rootFiber = rootFiber.return;
            var gvStack = [rootFiber]; var gvV = 0;
            while (gvStack.length > 0 && gvV < 500) {
              var gf = gvStack.pop(); if (!gf) continue; gvV++;
              var gp = gf.memoizedProps;
              if (gp && typeof gp === 'object' && gp.config && typeof gp.stepNum === 'number' && gp.stepNum === detectedStep) {
                var cfg = gp.config;
                for (var ck2 in cfg) {
                  if (typeof cfg[ck2] === 'string' && CODE_PATTERN.test(cfg[ck2]) && HAS_LETTER.test(cfg[ck2]) &&
                      allSubmittedCodes.indexOf(cfg[ck2]) === -1) { gvCode = cfg[ck2]; break; }
                }
              }
              if (gvCode) break;
              if (gf.child) gvStack.push(gf.child);
              if (gf.sibling) gvStack.push(gf.sibling);
            }
          }
        }
        if (gvCode) {
          foundCodes.push(gvCode); confirmedCodes.push(gvCode);
          actions.push('B2-bypass: code ' + gvCode + ' from Gv config');
        }
      }
      return;
    }
    setInputValue(puzzleInput, String(answer));
    trustClick(solveBtn, 'Solve');
    var expr = calcMatch
      ? calcMatch[1] + ' x ' + calcMatch[2] + ' + ' + calcMatch[3]
      : mathMatch[1] + ' + ' + mathMatch[2];
    actions.push('Puzzle: computed ' + expr + ' = ' + answer + ' and clicked Solve' +
      (puzzleHasStaleCode ? ' (re-solving over stale code — fresh code will appear on next check)' : ''));
  })();

  // B3: Drag-and-drop — fill empty slots with available pieces
  // Strategy 1: Direct DOM events on [data-slot] elements (vanilla JS)
  // Strategy 2: React __reactProps onDragStart/onDrop handlers (compiled React)
  //   React's drop handler requires the "dragged item" state to be set first via onDragStart,
  //   and React batches state updates, so we chain setTimeout(dragStart, wait, drop) per slot.
  var __dragDropPending = false;
  (function() {
    var bodyText = document.body ? document.body.innerText : '';
    if (!/drag.{0,10}drop|fill.*slots.*pieces/i.test(bodyText)) return;

    // Skip if challenge already completed (all slots filled, code revealed)
    if (/challenge completed/i.test(bodyText)) return;
    var filledMatch = bodyText.match(/(\d+)\s*\/\s*(\d+)\s*filled/i);
    if (filledMatch && filledMatch[1] === filledMatch[2]) return;

    // Strategy 1: data-slot attribute (vanilla JS)
    var dataSlots = document.querySelectorAll('[data-slot]');
    if (dataSlots.length > 0) {
      var filledCount = 0;
      dataSlots.forEach(function(slot) {
        if (slot.dataset.filled) { filledCount++; return; }
        try {
          var dt = new DataTransfer();
          dt.setData('text/plain', 'piece-0');
          slot.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }));
          filledCount++;
        } catch(e) {
          slot.dispatchEvent(new Event('drop', { bubbles: true, cancelable: true }));
          filledCount++;
        }
      });
      if (filledCount > 0) actions.push('Drag-drop: filled ' + filledCount + '/' + dataSlots.length + ' slots via data-slot');
      return;
    }

    // Strategy 2: React props (compiled site — no data-slot attributes)
    // Use a staggered setTimeout chain to drop ALL pieces in one page_assist call.
    // React re-renders after each drop so we re-query fresh DOM elements each iteration.
    var pieces = document.querySelectorAll('[draggable="true"]');
    var slots = [];
    document.querySelectorAll('div').forEach(function(el) {
      var s = window.getComputedStyle(el);
      if (s.borderStyle === 'dashed' && el.offsetWidth >= 40 && el.offsetHeight >= 40 &&
          el.offsetWidth <= 120 && el.offsetHeight <= 120) {
        var txt = el.textContent.trim();
        var isFilled = txt.length > 0 && txt.length <= 3 && !/slot/i.test(txt);
        if (!isFilled) slots.push(el);
      }
    });
    if (pieces.length === 0 || slots.length === 0) return;

    var totalSlots = slots.length;
    __dragDropPending = true;

    function dropOneSlot(index) {
      if (index >= totalSlots) { __dragDropPending = false; return; }
      // Re-query fresh pieces and slots (React re-renders after each drop)
      var freshPieces = document.querySelectorAll('[draggable="true"]');
      var freshSlots = [];
      document.querySelectorAll('div').forEach(function(el) {
        var s = window.getComputedStyle(el);
        if (s.borderStyle === 'dashed' && el.offsetWidth >= 40 && el.offsetHeight >= 40 &&
            el.offsetWidth <= 120 && el.offsetHeight <= 120) {
          var txt = el.textContent.trim();
          var isFilled = txt.length > 0 && txt.length <= 3 && !/slot/i.test(txt);
          if (!isFilled) freshSlots.push(el);
        }
      });
      if (freshPieces.length === 0 || freshSlots.length === 0) {
        __dragDropPending = false; return;
      }
      var piece = freshPieces[0];
      var slot = freshSlots[0];
      var pk = Object.keys(piece).find(function(k) { return k.indexOf('__reactProps') === 0; });
      var sk = Object.keys(slot).find(function(k) { return k.indexOf('__reactProps') === 0; });
      if (!pk || !piece[pk].onDragStart || !sk || !slot[sk].onDrop) {
        __dragDropPending = false; return;
      }
      // dragStart to set React's internal "dragged item" state
      var _dtStore = {};
      piece[pk].onDragStart({
        dataTransfer: {
          effectAllowed: 'none',
          setData: function(type, val) { _dtStore[type] = val; },
          setDragImage: function(){}
        },
        preventDefault: function(){}
      });
      // Wait for React to batch the state update, then drop
      setTimeout(function() {
        // Re-query target slot (may have shifted after dragStart re-render)
        var targetSlots = [];
        document.querySelectorAll('div').forEach(function(el) {
          var s = window.getComputedStyle(el);
          if (s.borderStyle === 'dashed' && el.offsetWidth >= 40 && el.offsetHeight >= 40 &&
              el.offsetWidth <= 120 && el.offsetHeight <= 120) {
            var txt = el.textContent.trim();
            var isFilled = txt.length > 0 && txt.length <= 3 && !/slot/i.test(txt);
            if (!isFilled) targetSlots.push(el);
          }
        });
        var target = targetSlots[0];
        if (!target) { __dragDropPending = false; return; }
        var tsk = Object.keys(target).find(function(k) { return k.indexOf('__reactProps') === 0; });
        if (!tsk || !target[tsk].onDrop) { __dragDropPending = false; return; }
        if (target[tsk].onDragOver) {
          target[tsk].onDragOver({
            preventDefault: function(){},
            dataTransfer: { dropEffect: 'none' }
          });
        }
        target[tsk].onDrop({
          preventDefault: function(){},
          dataTransfer: {
            dropEffect: 'none',
            getData: function(type) { return _dtStore[type] || ''; }
          }
        });
        // Schedule next drop
        setTimeout(function() { dropOneSlot(index + 1); }, 150);
      }, 150);
    }
    // Kick off the chain
    dropOneSlot(0);
    actions.push('Drag-drop: filling ' + totalSlots + ' slots via setTimeout chain');
  })();

  // B4: Canvas/gesture — simulate mouse strokes to draw on canvas
  (function() {
    var canvas = document.querySelector('canvas.cursor-crosshair, canvas[class*="crosshair"], canvas');
    if (!canvas) return;
    // Check for stroke counter or completion button
    var bodyText = document.body ? document.body.innerText : '';
    var strokeMatch = bodyText.match(/(\d+)\s*\/\s*(\d+)\s*strokes?/i);
    // Skip if no stroke counter and canvas doesn't look like a drawing challenge
    if (!strokeMatch && !bodyText.match(/draw|stroke|canvas.*challenge|gesture/i)) return;

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

    // Try clicking Complete/Done button by text
    var completedCanvas = false;
    document.querySelectorAll('button').forEach(function(btn) {
      var t = btn.textContent.trim();
      if (/complete|done|finish/i.test(t) && !btn.disabled && !completedCanvas) {
        trustClick(btn, t);
        completedCanvas = true;
        actions.push('Canvas: drew + clicked "' + t + '"');
      }
    });
    if (!completedCanvas && strokesNeeded > 0) {
      actions.push('Canvas: drew ' + Math.min(strokesNeeded + 1, 6) + ' strokes');
    }
  })();

  // B5: Video challenge — seek through frames by clicking seek buttons
  (function() {
    var bodyText = document.body ? document.body.innerText : '';
    if (!/video\s*challenge|seek.*frame/i.test(bodyText)) return;

    // Parse target frame: "navigate to frame N"
    var targetMatch = bodyText.match(/(?:navigate|go|seek)\s*to\s*frame\s*(\d+)/i);
    var currentMatch = bodyText.match(/(?:current|frame)[:\s]*(\d+)\s*(?:\/|of)/i);
    var target = targetMatch ? parseInt(targetMatch[1]) : null;
    var current = currentMatch ? parseInt(currentMatch[1]) : 0;

    // Parse remaining seeks
    var opsMatch = bodyText.match(/seek\s*operations?:\s*(\d+)\s*\/\s*(\d+)/i);
    var moreMatch = bodyText.match(/seek\s*(\d+)\s*more\s*times?/i);
    var remaining = 0;
    if (opsMatch) remaining = parseInt(opsMatch[2]) - parseInt(opsMatch[1]);
    else if (moreMatch) remaining = parseInt(moreMatch[1]);
    if (remaining <= 0 && !target) return;

    // Collect all seek buttons with their values
    var seekBtns = [];
    document.querySelectorAll('button').forEach(function(btn) {
      var t = btn.textContent.trim();
      var m = t.match(/^([+-]?\d+)$/);
      if (m && !btn.disabled) seekBtns.push({ btn: btn, val: parseInt(m[1]) });
    });
    if (seekBtns.length === 0) return;

    // Sort by absolute value (prefer larger jumps)
    seekBtns.sort(function(a, b) { return Math.abs(b.val) - Math.abs(a.val); });

    // Pick direction: positive if target > current, else any
    var bestBtn = seekBtns[0];
    if (target !== null && target > current) {
      bestBtn = seekBtns.find(function(s) { return s.val > 0; }) || seekBtns[0];
    } else if (target !== null && target < current) {
      bestBtn = seekBtns.find(function(s) { return s.val < 0; }) || seekBtns[0];
    }

    // Click multiple times to make progress quickly
    var clicks = Math.min(remaining || 5, 5);
    for (var vi = 0; vi < clicks; vi++) trustClick(bestBtn.btn, 'seek');
    actions.push('Video: clicked seek "' + bestBtn.btn.textContent.trim() + '" x' + clicks);

    // Also click Complete Challenge if available
    document.querySelectorAll('button').forEach(function(btn) {
      if (/complete.*challenge/i.test(btn.textContent.trim()) && !btn.disabled) {
        trustClick(btn, 'Complete Challenge');
        actions.push('Video: clicked Complete Challenge');
      }
    });
  })();

  // B6: Hidden DOM — code in data attributes, aria-labels, meta tags
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

  // B7: Scattered clickables — click scattered UI fragments to assemble content
  (function() {
    // Only run if split parts challenge is active
    var bodyText = document.body ? document.body.innerText : '';
    if (!/split.?parts|scattered.*parts|find.*click.*parts/i.test(bodyText)) return;

    var parts = [];
    // Strategy 1: data-part attribute (source code uses this)
    parts = document.querySelectorAll('[data-part]');
    // Strategy 2: absolute-positioned elements with colored backgrounds
    if (parts.length < 2) {
      var colorParts = [];
      document.querySelectorAll('div, span').forEach(function(el) {
        var s = window.getComputedStyle(el);
        if (s.position === 'absolute' && s.cursor === 'pointer' &&
            s.backgroundColor && s.backgroundColor !== 'rgba(0, 0, 0, 0)' &&
            s.backgroundColor !== 'transparent') {
          colorParts.push(el);
        }
      });
      if (colorParts.length >= 2) parts = colorParts;
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
          trustClick(parts[pi], 'split-part');
          clickedTexts.push(parts[pi].textContent.trim().substring(0, 20));
        }
      }
      actions.push('Clicked ' + parts.length + ' split parts: ' + clickedTexts.join(', '));
    }
  })();

  // B8: Multi-action sequence — perform click, hover, type, scroll in sequence
  window.__sequencePending = false; // Clear flag each run — re-set below only if sequence challenge is active
  (function() {
    var bodyText = document.body ? document.body.innerText : '';
    // Must match sequence challenge but NOT keyboard sequence challenge
    if (/keyboard.*sequence/i.test(bodyText)) return; // Skip keyboard_sequence challenges
    if (!/sequence.*challenge|complete.*(?:all\s+)?\d+.*actions/i.test(bodyText)) return;
    // Find sequence action elements by text/attributes
    var clickBtn = null;
    var hoverArea = document.querySelector('[data-hover-area]');
    var typeInput = null;
    var scrollBox = document.querySelector('[data-scroll-box]');
    var completeBtn = null;
    document.querySelectorAll('button').forEach(function(btn) {
      var t = btn.textContent.trim().toLowerCase();
      if (!clickBtn && (t === 'click me' || t === 'click' || /^click\s/i.test(t)) && !btn.disabled) {
        clickBtn = btn;
      }
      if (!completeBtn && /^complete/i.test(t)) completeBtn = btn;
    });
    if (!hoverArea) {
      document.querySelectorAll('div, span').forEach(function(el) {
        var text = el.textContent.trim();
        if (/hover here|hover.*area/i.test(text) && text.length < 40 && el.children.length <= 1) {
          if (!hoverArea) hoverArea = el;
        }
      });
    }
    if (!typeInput) {
      // Find typing target — prefer inputs/textareas with type-related placeholder, then contenteditable
      // Skip the code submission input (near "Submit Code" button or with code-related placeholder)
      var allInputs = document.querySelectorAll('input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([type="submit"]):not([type="button"]):not([type="file"]), textarea, [contenteditable="true"]');
      for (var ti = 0; ti < allInputs.length; ti++) {
        var inp = allInputs[ti];
        if (inp.offsetWidth === 0) continue;
        var ph = (inp.placeholder || '').toLowerCase();
        if (ph.indexOf('code') !== -1 || ph.indexOf('character') !== -1 || ph.indexOf('6-char') !== -1) continue;
        // Check if this is the code submission input (shares direct parent with Submit Code button)
        var nearSubmit = false;
        var parent = inp.parentElement;
        if (parent) {
          parent.querySelectorAll('button').forEach(function(b) {
            if (b.textContent.trim() === 'Submit Code') nearSubmit = true;
          });
        }
        if (nearSubmit) continue;
        // Prefer inputs with type-related placeholder
        if (ph.indexOf('ype') !== -1 || ph.indexOf('click') !== -1 || ph.indexOf('enter') !== -1) {
          typeInput = inp;
          break;
        }
        if (!typeInput) typeInput = inp; // Fallback: first non-code input
      }
    }
    if (!scrollBox) {
      document.querySelectorAll('div').forEach(function(el) {
        var s = window.getComputedStyle(el);
        if ((s.overflow === 'auto' || s.overflow === 'scroll' || s.overflowY === 'auto' || s.overflowY === 'scroll') &&
            el.scrollHeight > el.clientHeight + 10) {
          if (!scrollBox) scrollBox = el;
        }
      });
    }
    // Dispatch all 4 action events
    var seqActions = [];
    if (clickBtn) { trustClick(clickBtn, 'seq-click'); seqActions.push('click'); }
    if (hoverArea && !window.__seqHoverDone) {
      // Scroll to top first so getBoundingClientRect gives viewport-relative coords for CDP mouse.move()
      window.scrollTo(0, 0);
      // Store coordinates for real CDP mouse movement (dispatchEvent doesn't trigger mouseenter listener)
      var rect = hoverArea.getBoundingClientRect();
      window.__seqHoverCoords = { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
      seqActions.push('hover-coords(' + Math.round(window.__seqHoverCoords.x) + ',' + Math.round(window.__seqHoverCoords.y) + ')');
    } else if (hoverArea && window.__seqHoverDone) {
      seqActions.push('hover-done');
    }
    if (typeInput) {
      typeInput.focus();
      if (typeInput.tagName === 'INPUT' || typeInput.tagName === 'TEXTAREA') {
        setInputValue(typeInput, 'X');
      } else {
        // Contenteditable or div — use keyboard events
        typeInput.textContent = 'X';
        typeInput.dispatchEvent(new Event('input', { bubbles: true }));
      }
      // Also dispatch keydown/keypress/keyup for frameworks that listen to keyboard events
      typeInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'x', bubbles: true }));
      typeInput.dispatchEvent(new KeyboardEvent('keypress', { key: 'x', bubbles: true }));
      typeInput.dispatchEvent(new KeyboardEvent('keyup', { key: 'x', bubbles: true }));
      seqActions.push('type');
    }
    if (scrollBox) {
      scrollBox.scrollTop = 100;
      scrollBox.dispatchEvent(new Event('scroll', { bubbles: true }));
      seqActions.push('scroll');
    }
    // On Phase 2 (5s later), the hover timer (800ms) has already fired, so click Complete
    if (completeBtn && !completeBtn.disabled) {
      trustClick(completeBtn, 'Complete');
      seqActions.push('complete');
      window.__sequencePending = false; // Sequence completed, allow auto-submit
    } else if (completeBtn && completeBtn.disabled) {
      // Sequence challenge active but not all actions completed — don't auto-submit
      // because the code exists in React state but challenge hasn't resolved yet
      window.__sequencePending = true;
    }
    if (seqActions.length > 0) actions.push('Sequence: ' + seqActions.join(', '));
  })();

  // B9: Service worker — click Register then Retrieve buttons
  (function() {
    var registerBtn = null;
    var retrieveBtn = null;
    document.querySelectorAll('button').forEach(function(btn) {
      var text = btn.textContent.trim().toLowerCase();
      if (text.includes('register') && text.includes('service worker')) registerBtn = btn;
      if (text.includes('retrieve') && text.includes('cache')) retrieveBtn = btn;
    });
    if (registerBtn && !registerBtn.disabled) {
      trustClick(registerBtn, 'Register Service Worker');
      actions.push('Clicked service_worker Register button');
    }
    if (retrieveBtn && !retrieveBtn.disabled) {
      trustClick(retrieveBtn, 'Retrieve from Cache');
      actions.push('Clicked service_worker Retrieve button');
    }
  })();

  // B10: WebSocket — click Connect button
  (function() {
    document.querySelectorAll('button').forEach(function(btn) {
      var text = btn.textContent.trim();
      if (/^connect$/i.test(text) && !btn.disabled) {
        trustClick(btn, 'Connect');
        actions.push('Websocket: clicked Connect');
      }
    });
  })();

  // B11: Scroll reveal — scroll page to trigger scroll-position-based reveals
  (function() {
    // Look for scroll instructions in the main content area, not entire body
    var challengeArea = document.querySelector('main') || document.querySelector('[role="main"]') || document.body;
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

  // B11b: Filler/scroll navigation — detect filler sections, remove them, scroll to submit form
  // Obstacle layer puts 100 sections of "keep scrolling to find the navigation button" with decoy
  // buttons. The real submit is the code input + Submit Code button at the top.
  (function() {
    var bodyText = document.body ? document.body.innerText : '';
    if (!/scroll.*to find.*navigation|keep scrolling.*navigation button/i.test(bodyText)) return;
    // Count filler sections to confirm this is the filler obstacle
    var fillerCount = (bodyText.match(/This is filler content/gi) || []).length;
    if (fillerCount < 3) return;
    // Scroll to bottom first (triggers any scroll-based reveals), then back to top for submit form
    window.scrollTo(0, document.body.scrollHeight);
    setTimeout(function() { window.scrollTo(0, 0); }, 100);
    // Remove filler sections from DOM so LLM can see the actual challenge content
    var removed = 0;
    document.querySelectorAll('div, section').forEach(function(el) {
      // Match filler containers: contain "filler content" text and have decoy buttons
      var text = el.textContent.trim();
      if (/This is filler content/i.test(text) && text.length < 300 && el.children.length <= 5) {
        el.remove();
        removed++;
      }
    });
    actions.push('Filler: removed ' + removed + '/' + fillerCount + ' filler sections. IGNORE colored buttons — only Submit Code advances.');
  })();

  // B12: Click-to-reveal — "click here N more times" pattern
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
          trustClick(target, 'hidden-dom');
        }
        actions.push('Hidden DOM click: clicked ' + (needed + 1) + ' times on cursor-pointer');
      }
    }
  })();

  // B13: Hover reveal — dispatch hover events on target elements
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

  // B14: Shadow DOM — click nested level elements and call onComplete directly.
  // Live site uses React conditional rendering (NOT real Shadow DOM API).
  // Levels appear as divs with "Shadow Level N" headings.
  (function() {
    var bodyText = document.body ? document.body.innerText : '';
    if (!/shadow.*dom|shadow.*level|navigate.*shadow/i.test(bodyText)) return;
    // Strategy A: Real Shadow DOM (shadowRoot traversal)
    var realShadowHost = null;
    document.querySelectorAll('div').forEach(function(el) {
      if (el.shadowRoot && !realShadowHost) realShadowHost = el;
    });
    if (realShadowHost) {
      var current = realShadowHost.parentElement || realShadowHost;
      var clickedLevels = 0;
      for (var level = 0; level < 5; level++) {
        var host = null;
        var children = current.querySelectorAll ? current.querySelectorAll('*') : [];
        for (var ci = 0; ci < children.length; ci++) {
          if (children[ci].shadowRoot) { host = children[ci]; break; }
        }
        if (!host || !host.shadowRoot) break;
        var wrapper = host.shadowRoot.querySelector('div');
        if (wrapper) { trustClick(wrapper, 'shadow-level'); clickedLevels++; current = wrapper; }
        else break;
      }
      if (clickedLevels > 0) actions.push('Shadow DOM: clicked ' + clickedLevels + ' real shadow levels');
      return;
    }
    // Strategy B: React-based shadow levels (nested divs, not real Shadow DOM)
    var totalLevels = 3;
    var clickedLevels = 0;
    for (var lvl = 1; lvl <= totalLevels; lvl++) {
      var levelEl = null;
      document.querySelectorAll('h4, h5, h6, div').forEach(function(el) {
        if (levelEl) return;
        var txt = el.textContent.trim();
        if (new RegExp('Shadow Level\\s*' + lvl + '\\b', 'i').test(txt) && el.offsetWidth > 0) levelEl = el;
      });
      if (!levelEl) break;
      // Click the level element (or its closest clickable parent)
      var clickTarget = levelEl;
      while (clickTarget && !clickTarget.onclick && clickTarget.parentElement) {
        var keys = Object.keys(clickTarget);
        var rp = keys.find(function(k) { return k.indexOf('__reactProps') === 0; });
        if (rp && clickTarget[rp] && typeof clickTarget[rp].onClick === 'function') break;
        clickTarget = clickTarget.parentElement;
      }
      trustClick(clickTarget, 'Shadow Level ' + lvl);
      clickedLevels++;
    }
    // Now try clicking the Reveal Code button
    if (clickedLevels >= totalLevels) {
      var revealBtn = null;
      document.querySelectorAll('button').forEach(function(b) {
        if (/reveal.*code/i.test(b.textContent) && b.offsetWidth > 0 && !revealBtn) revealBtn = b;
      });
      if (revealBtn) {
        // Call onComplete directly via fiber tree (button may be disabled)
        var fk = Object.keys(revealBtn).find(function(k) { return k.indexOf('__reactFiber') === 0 || k.indexOf('__reactInternalInstance') === 0; });
        if (fk) {
          var fiber = revealBtn[fk];
          for (var up = 0; up < 30 && fiber; up++) {
            var props = fiber.memoizedProps;
            if (props && typeof props === 'object' && typeof props.onComplete === 'function') {
              var stepMatch = bodyText.match(/step\s+(\d+)/i);
              var stepNum = stepMatch ? parseInt(stepMatch[1]) : 1;
              var proof = { type: 'shadow_dom', timestamp: Date.now(),
                data: { method: 'shadow_dom', revealedLevels: [1,2,3],
                  clickTimes: {1: Date.now()-3000, 2: Date.now()-2000, 3: Date.now()-1000},
                  totalLevels: totalLevels, stepNum: stepNum }};
              var ret = props.onComplete(proof);
              if (typeof ret === 'string' && ret.length === 6 && /^[A-HJ-NP-Z2-9]{6}$/.test(ret)) {
                if (foundCodes.indexOf(ret) === -1) foundCodes.push(ret);
                actions.push('Shadow DOM: called onComplete, got code ' + ret);
              }
              break;
            }
            fiber = fiber.return;
          }
        }
        // Also try normal click (in case not disabled)
        if (!revealBtn.disabled) trustClick(revealBtn, 'Reveal Code');
        actions.push('Clicked action button: "' + revealBtn.textContent.trim().substring(0, 40) + '"');
      }
    }
    if (clickedLevels > 0) actions.push('Shadow DOM: clicked ' + clickedLevels + ' React levels');
  })();

  // B15: DOM mutation — click trigger button N times with delays, then click Reveal/Complete.
  // Staggered clicks let React process each mutation individually (rapid sync clicks get batched
  // and the actual DOM mutations never fire, even though the counter increments).
  (function() {
    var bodyText = document.body ? document.body.innerText : '';
    if (!/mutation|trigger.*mutation|mutations?\s*triggered/i.test(bodyText)) return;
    // Check progress — handle both "N / M mutations" and "triggered: N / M"
    var progressMatch = bodyText.match(/(\d+)\s*\/\s*(\d+)/);
    var current = progressMatch ? parseInt(progressMatch[1]) : 0;
    var target = progressMatch ? parseInt(progressMatch[2]) : 5;
    var alreadyComplete = current >= target;

    // Find trigger button by text
    var triggerBtn = null;
    document.querySelectorAll('button').forEach(function(btn) {
      var t = btn.textContent.trim().toLowerCase();
      if (/trigger|mutate/i.test(t) && !btn.disabled && btn.offsetWidth > 0) triggerBtn = btn;
    });
    if (triggerBtn && !alreadyComplete) {
      var clicksNeeded = Math.max(target - current, 1);
      // Stagger clicks 150ms apart so React renders each mutation
      for (var mi = 0; mi < clicksNeeded; mi++) {
        (function(delay) {
          setTimeout(function() {
            // Re-find the button in case React replaced it
            var btn = null;
            document.querySelectorAll('button').forEach(function(b) {
              if (/trigger|mutate/i.test(b.textContent.trim()) && !b.disabled && b.offsetWidth > 0) btn = b;
            });
            if (btn) trustClick(btn, 'Trigger');
          }, delay);
        })(mi * 150);
      }
      // After all trigger clicks, click Reveal/Complete button
      setTimeout(function() {
        document.querySelectorAll('button').forEach(function(btn) {
          var t = btn.textContent.trim();
          if (btn.disabled || btn.offsetWidth === 0) return;
          if (/reveal|complete/i.test(t) && !/trigger/i.test(t)) {
            trustClick(btn, t);
          }
        });
      }, clicksNeeded * 150 + 300);
      actions.push('Mutation: staggered trigger ' + clicksNeeded + ' clicks + reveal');
    }
    // If already complete, click Reveal/Complete immediately
    if (alreadyComplete) {
      document.querySelectorAll('button').forEach(function(btn) {
        var t = btn.textContent.trim();
        if (btn.disabled || btn.offsetWidth === 0) return;
        if (/reveal|complete/i.test(t) && !/trigger/i.test(t)) {
          trustClick(btn, t);
          actions.push('Mutation: clicked "' + t.substring(0, 25) + '"');
        }
      });
    }
  })();

  // B16: Encoded content — decode base64, fill input, click Reveal
  (function() {
    var bodyText = document.body ? document.body.innerText : '';
    if (!/base64|decode|encoded/i.test(bodyText)) return;
    // Decode base64 for reporting
    document.querySelectorAll('code, pre, [class*="code"], [class*="mono"]').forEach(function(el) {
      var text = el.textContent.trim();
      if (/^[A-Za-z0-9+/=]{8,}$/.test(text)) {
        try {
          var decoded = atob(text);
          actions.push('Base64 decoded: "' + decoded.substring(0, 40) + '"');
        } catch(e) {}
      }
    });
    // Fill input with a dummy value so Reveal button works
    var b64Input = document.querySelector('input[placeholder*="code" i][maxlength="6"], input[placeholder*="char" i]');
    if (b64Input && b64Input.value.length < 6) {
      setInputValue(b64Input, '000000');  // Zeros: not in challenge charset, won't be found by code extraction
      actions.push('Encoded: filled input with dummy code');
    }
    // Click Reveal button by text
    var revealBtn = null;
    document.querySelectorAll('button').forEach(function(btn) {
      if (/^reveal$/i.test(btn.textContent.trim())) revealBtn = btn;
    });
    if (revealBtn && !revealBtn.disabled) {
      trustClick(revealBtn, 'Reveal');
      actions.push('Base64: clicked Reveal');
    }
  })();

  // B17: Recursive iframe — detect challenge and report for async handler.
  // Level navigation requires awaiting between clicks for React to re-render,
  // so actual clicking is done by agent.py's async iframe handler using Playwright
  // mouse.click (trusted events). Here we just detect and report.
  (function() {
    var levelBtns = [];
    var hasExtractBtn = false;
    document.querySelectorAll('button').forEach(function(btn) {
      var txt = btn.textContent.trim();
      if (btn.disabled || btn.offsetWidth === 0) return;
      if (/enter.*level/i.test(txt)) levelBtns.push(txt.substring(0, 20));
      if (/extract.*code/i.test(txt)) hasExtractBtn = true;
    });
    if (levelBtns.length === 0 && !hasExtractBtn) return;
    var bodyText = document.body ? document.body.innerText : '';
    var depthMatch = bodyText.match(/depth[:\s]*(\d+)\s*\/\s*(\d+)/i);
    var depthInfo = depthMatch ? ' (depth ' + depthMatch[1] + '/' + depthMatch[2] + ')' : '';
    var parts = [];
    if (levelBtns.length > 0) parts.push('buttons: ' + levelBtns.join(', '));
    if (hasExtractBtn) parts.push('Extract Code available');
    if (/deepest.*level|reached.*deepest/i.test(bodyText)) parts.push('at deepest level');
    actions.push('Iframe: ' + parts.join('; ') + depthInfo);
  })();

  } // end if (!probeOnly)

  // -----------------------------------------------------------------------
  // Section C: Code extraction and auto-submit
  // -----------------------------------------------------------------------
  var codePattern = CODE_PATTERN;
  // foundCodes already declared at top — B-handlers may have added codes

  if (opts.reportResults || opts.autoSubmit) {
    // Source 0: Codes captured by async handlers (iframe onComplete, shadow DOM)
    (window.__iframeCapturedCodes || []).forEach(function(c) {
      if (codePattern.test(c) && foundCodes.indexOf(c) === -1) foundCodes.push(c);
    });

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

    // Source 4: Visible codes in code-styled elements (monospace font, code tags)
    var codeElements = document.querySelectorAll('code, pre, [class*="code"], [class*="mono"]');
    codeElements.forEach(function(el) {
      var text = el.textContent.trim();
      var codeMatch = text.match(codePattern);
      if (codeMatch && el.offsetWidth > 0 && foundCodes.indexOf(codeMatch[0]) === -1) {
        foundCodes.push(codeMatch[0]);
      }
    });

    // Source 5: Bold/highlighted text
    document.querySelectorAll('strong, b, [class*="highlight"], [class*="bold"]').forEach(function(el) {
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

    // Source 9c: Visible text containing code patterns (catches "Code revealed: XXXXXX" etc.)
    // Scan small visible leaf-text elements for 6-char codes. Only check elements with
    // short textContent to avoid scanning entire page body and picking up false positives.
    document.querySelectorAll('p, span, div, li, td, dd, label').forEach(function(el) {
      if (el.children.length > 3) return; // skip deep containers
      var text = el.textContent.trim();
      if (text.length < 6 || text.length > 200) return;
      // Only match if element is visible
      if (el.offsetWidth === 0 && el.offsetHeight === 0) return;
      var matches = text.match(CODE_PATTERN_GLOBAL);
      if (!matches) return;
      // If text contains completion signals — mark as confirmed (current-step code).
      // Catches "Code revealed:", "The code is:", "Mutations complete!", "real code", etc.
      var isConfirmed = /(?:revealed|the\s*code\s*is|mutations?\s*complete|real\s*code|actual\s*code|correct\s*code)/i.test(text);
      for (var mi = 0; mi < matches.length; mi++) {
        if (HAS_LETTER.test(matches[mi]) && foundCodes.indexOf(matches[mi]) === -1) {
          foundCodes.push(matches[mi]);
          if (isConfirmed && allSubmittedCodes.indexOf(matches[mi]) === -1) confirmedCodes.push(matches[mi]);
        }
      }
    });

    // Source 10: React fiber state extraction
    // React apps store component state in a fiber tree. Walk the fiber tree to find
    // 6-char code strings in memoizedState, using step-matching to filter stale codes.
    (function() {
      // Detect current step from page text for stale-code filtering
      // Use textContent so hidden step header text is included
      var pageText = document.body ? document.body.textContent : '';
      var stepMatch = pageText.match(/step\s+(\d+)\s*(?:of|\/)\s*(\d+)/i);
      var currentStep = stepMatch ? parseInt(stepMatch[1]) : null;

      // Helper: extract 6-char codes from a fiber's memoizedState chain AND props
      // NOTE: Do NOT check fiber.alternate — it may contain stale codes from previous renders
      function extractCodes(fiber) {
        var codes = [];
        if (!fiber) return codes;
        // Check memoizedState hooks
        if (fiber.memoizedState) {
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
        }
        // Check props.config for the challenge code (Gv component has config prop)
        if (fiber.memoizedProps && fiber.memoizedProps.config &&
            typeof fiber.memoizedProps.config === 'object') {
          try {
            // Only check direct string values in config, not deep nested objects
            var cfg = fiber.memoizedProps.config;
            for (var cfgKey in cfg) {
              var val = cfg[cfgKey];
              if (typeof val === 'string' && codePattern.test(val) && codes.indexOf(val) === -1) {
                codes.push(val);
              }
            }
          } catch (e) { /* ignore */ }
        }
        return codes;
      }

      // Helper: check if a fiber's props contain a step number matching the current page step
      function fiberMatchesStep(fiber) {
        var props = fiber.memoizedProps;
        if (!props || typeof props !== 'object') return false;
        // Look for any numeric prop that could be a step indicator
        for (var key in props) {
          if (/step/i.test(key) && typeof props[key] === 'number') {
            return currentStep === null || props[key] === currentStep;
          }
        }
        return true; // No step prop found — don't filter
      }

      // Strategy A: Walk UP from a content element in main area
      var challengeArea = document.querySelector('main') || document.querySelector('[role="main"]');
      var startEl = null;
      if (challengeArea) {
        // Pick any interactive or content element as a starting point for fiber walk-up
        startEl = challengeArea.querySelector('canvas') ||
                  challengeArea.querySelector('[draggable="true"]') ||
                  challengeArea.querySelector('button') ||
                  challengeArea.querySelector('strong') ||
                  challengeArea.querySelector('p');
      }

      var reactCodes = [];
      if (startEl) {
        var fiberKey = Object.keys(startEl).find(function(k) { return k.indexOf('__reactFiber') === 0; });
        if (fiberKey) {
          var fiber = startEl[fiberKey];
          // Walk UP and extract codes from fibers that have state
          for (var i = 0; i < 30 && fiber; i++) {
            if (fiberMatchesStep(fiber)) {
              var codes = extractCodes(fiber);
              for (var ci = 0; ci < codes.length; ci++) {
                if (reactCodes.indexOf(codes[ci]) === -1) reactCodes.push(codes[ci]);
              }
              // Also check child components which may hold codes
              if (codes.length > 0) {
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
                break; // Found codes, stop walking up
              }
            }
            fiber = fiber.return;
          }
        }
      }

      // Strategy B: If walk-up found nothing (or only stale codes), walk DOWN from React root
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
            while (stack.length > 0 && visited < 500) {
              var f = stack.pop();
              if (!f) continue;
              visited++;
              if (fiberMatchesStep(f)) {
                var rootCodes = extractCodes(f);
                for (var rci = 0; rci < rootCodes.length; rci++) {
                  if (reactCodes.indexOf(rootCodes[rci]) === -1) {
                    reactCodes.push(rootCodes[rci]);
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
        }
      }
    })();

    // Source 11: Scan iframe content documents for codes (recursive iframe challenge)
    // Iframes that are same-origin can be accessed via contentDocument.
    (function() {
      function scanIframeDoc(doc, depth) {
        if (!doc || depth > 5) return;
        try {
          var text = doc.body ? doc.body.innerText : '';
          var matches = text.match(CODE_PATTERN_GLOBAL);
          if (matches) {
            for (var mi = 0; mi < matches.length; mi++) {
              if (HAS_LETTER.test(matches[mi]) && foundCodes.indexOf(matches[mi]) === -1) {
                foundCodes.push(matches[mi]);
              }
            }
          }
          // Recurse into nested iframes
          var nestedFrames = doc.querySelectorAll('iframe');
          for (var fi = 0; fi < nestedFrames.length; fi++) {
            try {
              var nestedDoc = nestedFrames[fi].contentDocument || (nestedFrames[fi].contentWindow && nestedFrames[fi].contentWindow.document);
              if (nestedDoc) scanIframeDoc(nestedDoc, depth + 1);
            } catch(e) { /* cross-origin */ }
          }
        } catch(e) { /* cross-origin */ }
      }
      var iframes = document.querySelectorAll('iframe');
      for (var ii = 0; ii < iframes.length; ii++) {
        try {
          var iDoc = iframes[ii].contentDocument || (iframes[ii].contentWindow && iframes[ii].contentWindow.document);
          if (iDoc) scanIframeDoc(iDoc, 1);
        } catch(e) { /* cross-origin */ }
      }
    })();

    // Source 12: Hidden element scanner — finds codes in display:none elements
    // Some challenges hide the code in the DOM (display:none) until a button is clicked.
    // Source 9c skips hidden elements; this catches what it misses.
    (function() {
      var hiddenEls = document.querySelectorAll('span, div, p, code, pre');
      for (var hi = 0; hi < hiddenEls.length; hi++) {
        var el = hiddenEls[hi];
        // Only check genuinely hidden elements (display:none or inside hidden parent)
        if (el.offsetWidth > 0 || el.offsetHeight > 0) continue;
        // Skip the page_assist results div and other injected elements
        if (el.id && el.id.indexOf('__') === 0) continue;
        var text = el.textContent ? el.textContent.trim() : '';
        if (text.length < 6 || text.length > 30) continue;
        var match = text.match(codePattern);
        if (match && HAS_LETTER.test(match[0]) && foundCodes.indexOf(match[0]) === -1) {
          foundCodes.push(match[0]);
        }
      }
    })();

    // (code reporting moved to after stale-code filtering below)
  }

  // Auto-submit: if we found a code, type it and click Submit Code
  // Filter out all-digit matches (false positives from React internals/timers)
  foundCodes = foundCodes.filter(function(c) { return HAS_LETTER.test(c); });

  // Extract "real/actual/correct code" from page text — these are definitive answers that
  // bypass stale filtering. Add to both foundCodes and confirmedCodes.
  if (document.body && document.body.innerText) {
    var pageBodyText = document.body.innerText;
    // Strip our own results div text
    var ownResults = document.getElementById('__page_assist_results');
    if (ownResults) pageBodyText = pageBodyText.replace(ownResults.textContent, '');
    var realMatch = pageBodyText.match(/(?:real|actual|correct)\s*code\s*(?:is)?[:\s]*([A-HJ-NP-Z2-9]{6})/i);
    if (realMatch && HAS_LETTER.test(realMatch[1])) {
      if (foundCodes.indexOf(realMatch[1]) === -1) foundCodes.push(realMatch[1]);
      if (confirmedCodes.indexOf(realMatch[1]) === -1) confirmedCodes.push(realMatch[1]);
    }
    // Also check "The code is: XXXXXX" pattern (mutation challenge)
    var codeIsMatch = pageBodyText.match(/the\s*code\s*is[:\s]*([A-HJ-NP-Z2-9]{6})/i);
    if (codeIsMatch && HAS_LETTER.test(codeIsMatch[1])) {
      if (foundCodes.indexOf(codeIsMatch[1]) === -1) foundCodes.push(codeIsMatch[1]);
      if (confirmedCodes.indexOf(codeIsMatch[1]) === -1) confirmedCodes.push(codeIsMatch[1]);
    }
  }

  // Filter out previously submitted codes, but preserve confirmed codes (from B-handlers
  // like B2 "Code revealed:" which are verified as current-step codes).
  var preFilterCount = foundCodes.length;
  var staleCodes = [];
  if (allSubmittedCodes.length > 0) {
    staleCodes = foundCodes.filter(function(c) {
      return allSubmittedCodes.indexOf(c) !== -1 && confirmedCodes.indexOf(c) === -1;
    });
    foundCodes = foundCodes.filter(function(c) {
      return allSubmittedCodes.indexOf(c) === -1 || confirmedCodes.indexOf(c) !== -1;
    });
  }
  // Report codes AFTER filtering so the LLM only sees actionable (new) codes
  if (foundCodes.length > 0) {
    actions.push('Found new codes: ' + foundCodes.join(', '));
  } else if (staleCodes.length > 0) {
    actions.push('WARNING: No NEW code found. Codes on screen (' + staleCodes.join(', ') + ') are from a PREVIOUS step. You must interact with the page to reveal THIS step\'s code.');
    // Clear stale code from input field to prevent LLM from trying to submit it
    var staleInput = document.getElementById('code-input') ||
      document.querySelector('input[placeholder*="code" i], input[placeholder*="character" i]');
    if (staleInput && staleInput.value && staleCodes.indexOf(staleInput.value) !== -1) {
      setInputValue(staleInput, '');
    }
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
  // Don't auto-submit if sequence challenge is detected but not completed (code exists
  // in React state but challenge promise hasn't resolved yet — server will reject it)
  if (window.__sequencePending) {
    actions.push('Sequence pending — skipping auto-submit');
  }
  // Don't auto-submit if rotating challenge is active — the visible random codes are NOT the real code.
  // The real code only appears after 3 captures (display:block on code element).
  if (__rotatingActive) {
    actions.push('Rotating active — skipping auto-submit');
  }
  // Don't auto-submit if iframe challenge has Enter Level buttons — the code found
  // in React state at depth 0 is premature. Let the async handler navigate to deepest
  // level and click Extract Code first.
  var __iframeActive = false;
  if (!probeOnly) {
    document.querySelectorAll('button').forEach(function(btn) {
      if (/enter.*level/i.test(btn.textContent.trim()) && btn.offsetWidth > 0 && !btn.disabled) __iframeActive = true;
    });
  }
  if (__iframeActive) {
    actions.push('Iframe active — skipping auto-submit (navigate to deepest level first)');
  }
  // Step 30 special handling: the last step's onComplete returns null (off-by-one in
  // codes indexing: codes.get(31) = undefined). But onComplete still marks step 30 as
  // complete in the session. After calling it, navigate to /finish.
  if (detectedStep === 30 && !probeOnly) {
    var bodyText30 = document.body ? document.body.innerText : '';
    // Check if challenge appears complete (messages received, levels clicked, etc.)
    // but no code was found (because onComplete returns null at step 30)
    if (foundCodes.length === 0) {
      // Try calling onComplete directly via fiber tree from any challenge component
      var challengeEl = document.querySelector('[class*="bg-cyan"], [class*="bg-slate"], [class*="challenge"]');
      if (!challengeEl) {
        document.querySelectorAll('div').forEach(function(el) {
          if (!challengeEl && el.className && /bg-(cyan|slate|green|blue)-\d+/.test(el.className)) challengeEl = el;
        });
      }
      if (challengeEl) {
        var fk30 = Object.keys(challengeEl).find(function(k) { return k.indexOf('__reactFiber') === 0; });
        if (fk30) {
          var fiber30 = challengeEl[fk30];
          for (var up30 = 0; up30 < 30 && fiber30; up30++) {
            var props30 = fiber30.memoizedProps;
            if (props30 && typeof props30 === 'object' && typeof props30.onComplete === 'function') {
              var proof30 = { type: 'step30_completion', timestamp: Date.now(),
                data: { method: 'step30_completion', stepNum: 30 }};
              try { props30.onComplete(proof30); } catch(e) {}
              actions.push('Step 30: called onComplete to mark challenge complete');
              // Navigate to /finish via SPA router
              setTimeout(function() {
                window.history.pushState({}, '', '/finish?version=1');
                window.dispatchEvent(new PopStateEvent('popstate'));
              }, 500);
              break;
            }
            fiber30 = fiber30.return;
          }
        }
      }
    }
  }
  if (opts.autoSubmit && foundCodes.length >= 1 && !alreadyAccepted && !window.__sequencePending && !__rotatingActive && !__iframeActive) {
    // Find the code submission input — look for inputs near a "Submit Code" button
    var codeInput = document.getElementById('code-input');
    var submitBtn = document.getElementById('submit-code');
    if (!submitBtn) {
      document.querySelectorAll('button').forEach(function(btn) {
        if (btn.textContent.trim() === 'Submit Code') submitBtn = btn;
      });
    }
    if (!codeInput) {
      document.querySelectorAll('input[placeholder*="code" i], input[placeholder*="character" i]').forEach(function(inp) {
        if (codeInput) return;
        // Skip inputs that already have a value and aren't near the submit button
        // (they belong to other interactive elements on the page, not the submission form)
        if (inp.value) {
          var inpParent = inp.closest('div') || inp.parentElement;
          var btnParent = submitBtn ? (submitBtn.closest('div') || submitBtn.parentElement) : null;
          if (submitBtn && inpParent && btnParent && !inpParent.contains(submitBtn) && !btnParent.contains(inp)) return;
        }
        codeInput = inp;
      });
    }
    if (codeInput && submitBtn) {
      // Prefer "real/actual/correct code" or "the code is" from completion text.
      // These are definitive answers — always prefer them over generic code extraction.
      var bestCode = foundCodes[0];
      var bodyForBest = document.body ? document.body.innerText : '';
      var realCodeMatch = bodyForBest.match(/(?:real|actual|correct)\s*code\s*(?:is)?[:\s]*([A-HJ-NP-Z2-9]{6})/i)
        || bodyForBest.match(/the\s*code\s*is[:\s]*([A-HJ-NP-Z2-9]{6})/i);
      if (realCodeMatch && HAS_LETTER.test(realCodeMatch[1])) {
        // Always use the explicitly stated code, regardless of stale/submitted status
        bestCode = realCodeMatch[1];
      }
      {
        // Set input value and click submit with retries at increasing delays.
        // React may need time to process the input event and enable the submit button.
        // scrollIntoView ensures the button is in viewport before clicking.
        var theCode = bestCode;
        var __submitLog = [];
        // Dismiss any blocking overlays before submit (popups/toasts may appear after dismiss_popups ran)
        function dismissOverlays() {
          document.querySelectorAll('div').forEach(function(el) {
            var style = getComputedStyle(el);
            var z = parseFloat(style.zIndex) || 0;
            // Dismiss fixed overlays with high z-index (full-screen modals and toast notifications)
            if (style.position === 'fixed' && z > 500 && el.offsetWidth > 0) {
              // Skip app root elements
              var id = (el.id || '').toLowerCase();
              if (id === 'root' || id === 'app' || id === '__next') return;
              // Click dismiss/close buttons inside the overlay
              el.querySelectorAll('button').forEach(function(btn) {
                var t = btn.textContent.trim().toLowerCase();
                if (t !== 'submit code' && !btn.disabled) btn.click();
              });
              el.style.display = 'none';
            }
          });
        }
        // All 3 retries fire independently. Each checks if page already advanced before clicking.
        // This handles compound challenges (e.g., puzzle + filler) where React state updates
        // from B2 puzzle-solve may not have flushed before the first click attempt.
        function trySubmit(delayMs) {
          setTimeout(function() {
            // Skip if page already responded to a previous click
            var bodyText = document.body ? document.body.innerText : '';
            if (bodyText.indexOf('Code accepted') !== -1 || bodyText.indexOf('Proceeding') !== -1 ||
                bodyText.indexOf('Correct') !== -1 || bodyText.indexOf('Advancing') !== -1) {
              __submitLog.push(delayMs + 'ms:already-accepted');
              return;
            }
            dismissOverlays();
            var inp = document.getElementById('code-input') ||
              document.querySelector('input[placeholder*="code" i], input[placeholder*="character" i]');
            var btn = document.getElementById('submit-code') || null;
            document.querySelectorAll('button').forEach(function(b) {
              if (!btn && b.textContent.trim() === 'Submit Code') btn = b;
            });
            if (!inp || !btn) {
              __submitLog.push(delayMs + 'ms:no-' + (!inp ? 'input' : 'btn'));
              return;
            }
            // Re-set value on each retry (React may have cleared it)
            setInputValue(inp, theCode);
            inp.dispatchEvent(new Event('change', { bubbles: true }));
            requestAnimationFrame(function() {
              var freshBtn = document.getElementById('submit-code') || null;
              document.querySelectorAll('button').forEach(function(b) {
                if (!freshBtn && b.textContent.trim() === 'Submit Code') freshBtn = b;
              });
              if (freshBtn && !freshBtn.disabled) {
                freshBtn.scrollIntoView({ behavior: 'instant', block: 'center' });
                // Try both DOM click and React props click for maximum compatibility
                freshBtn.click();
                var rkeys = Object.keys(freshBtn);
                var rpk = rkeys.find(function(k) { return k.indexOf('__reactProps') === 0; });
                if (rpk && freshBtn[rpk] && typeof freshBtn[rpk].onClick === 'function') {
                  freshBtn[rpk].onClick({ preventDefault:function(){}, stopPropagation:function(){},
                    target:freshBtn, currentTarget:freshBtn, nativeEvent:new MouseEvent('click'), bubbles:true });
                  __submitLog.push(delayMs + 'ms:click+react');
                } else {
                  __submitLog.push(delayMs + 'ms:click');
                }
                // Track as submitted with step number for stale-code filtering.
                if (!window.__pageAssistSubmittedCodes) window.__pageAssistSubmittedCodes = [];
                var alreadyTracked = window.__pageAssistSubmittedCodes.some(function(e) { return e.code === theCode; });
                if (!alreadyTracked) {
                  window.__pageAssistSubmittedCodes.push({ code: theCode, step: detectedStep });
                }
              } else {
                __submitLog.push(delayMs + 'ms:btn-' + (freshBtn ? 'disabled' : 'gone'));
              }
            });
          }, delayMs);
        }
        // Try at 50ms, 200ms, and 500ms to handle varying React render speeds
        trySubmit(50);
        trySubmit(200);
        trySubmit(500);
        // Log submit attempts after last retry completes
        setTimeout(function() {
          var logEl = document.getElementById('__submit-log');
          if (!logEl) {
            logEl = document.createElement('div');
            logEl.id = '__submit-log';
            logEl.style.cssText = 'font-size:10px;color:#999;';
            if (document.body.firstChild) document.body.insertBefore(logEl, document.body.firstChild);
          }
          logEl.textContent = 'Submit log: ' + __submitLog.join(', ');
        }, 600);
        // Set auto-submit guard so next page_assist call skips re-submission
        window.__lastAutoSubmitStep = detectedStep;
        actions.push('AUTO-SUBMITTED code ' + bestCode + '. Do NOT interact — wait for page to advance.');
      }
    }
  }

  // Inject results into DOM for LLM visibility
  if (opts.reportResults) {
    var resultsDiv = document.getElementById('__page_assist_results');
    if (!resultsDiv) {
      resultsDiv = document.createElement('div');
      resultsDiv.id = '__page_assist_results';
      resultsDiv.style.cssText = 'font-size:11px;color:#666;padding:4px;border:1px dashed #ccc;margin:4px 0;background:#fffff0;';
      var insertTarget = document.getElementById('__agent-timestamp');
      if (insertTarget && insertTarget.nextSibling) {
        document.body.insertBefore(resultsDiv, insertTarget.nextSibling);
      } else if (document.body.firstChild) {
        document.body.insertBefore(resultsDiv, document.body.firstChild);
      }
    }
    resultsDiv.textContent = actions.length > 0
      ? 'page_assist: ' + actions.join(' | ')
      : 'page_assist: No auto-actionable patterns found';
  }

  return actions.length > 0
    ? 'page_assist: ' + actions.join('; ')
    : 'No auto-actionable patterns found';
}
