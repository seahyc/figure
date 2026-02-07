function(options) {
  var opts = Object.assign({
    clickActionButtons: true,
    clickProgressButtons: true,
    clickSequentialNav: true,
    waitForCountdowns: true,
    reportResults: true
  }, options || {});

  var actions = [];

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
  if (opts.clickProgressButtons) {
    var progressPattern = /(\d+)\s*[\/of]\s*(\d+)/;
    var allText = document.body ? document.body.innerText : '';
    var progressMatch = allText.match(progressPattern);

    if (progressMatch) {
      var current = parseInt(progressMatch[1]);
      var total = parseInt(progressMatch[2]);
      if (current < total && total <= 20) {
        // Find clickable elements near the progress text
        document.querySelectorAll('button, [role="button"]').forEach(function(btn) {
          var text = btn.textContent.trim().toLowerCase();
          // Look for buttons that seem to advance progress (capture, collect, click, tap, etc.)
          if (/^(capture|collect|click|tap|press|grab|get|pick|catch|gather|count|increment|add)/i.test(btn.textContent.trim())) {
            // Click multiple times to reach the target
            var remaining = total - current;
            for (var i = 0; i < Math.min(remaining, 15); i++) {
              btn.click();
            }
            actions.push('Clicked progress button "' + btn.textContent.trim().substring(0, 30) + '" x' + Math.min(remaining, 15) + ' (' + current + '/' + total + ')');
          }
        });
      }
    }
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

  // Pattern 5: Report findings by injecting a results div
  if (opts.reportResults) {
    // Look for visible codes on the page (common code format)
    var codeElements = document.querySelectorAll('.font-mono, [class*="code"], code, pre');
    var foundCodes = [];
    codeElements.forEach(function(el) {
      var text = el.textContent.trim();
      // Match 6-char alphanumeric codes
      var codeMatch = text.match(/\b[A-Za-z0-9]{6}\b/);
      if (codeMatch && el.offsetWidth > 0) {
        foundCodes.push(codeMatch[0]);
      }
    });

    // Also search for codes in bold/highlighted text
    document.querySelectorAll('.font-bold, strong, b, [class*="highlight"]').forEach(function(el) {
      var text = el.textContent.trim();
      var codeMatch = text.match(/\b[A-Za-z0-9]{6}\b/);
      if (codeMatch && el.offsetWidth > 0 && foundCodes.indexOf(codeMatch[0]) === -1) {
        foundCodes.push(codeMatch[0]);
      }
    });

    if (foundCodes.length > 0) {
      actions.push('Found potential codes: ' + foundCodes.join(', '));
    }

    // Inject results into DOM for LLM visibility
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
