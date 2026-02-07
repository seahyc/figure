function(options) {
  var opts = Object.assign({
    selector: '.shadow-level-host',
    action: 'click',
    maxLevels: 10,
    retries: 3,
    retryDelay: 200
  }, options || {});

  var results = [];
  var clickedCount = 0;

  // Helper: find shadow hosts using multiple strategies
  function findShadowHosts(root) {
    var hosts = [];
    // Strategy 1: Use provided selector
    var bySelector = root.querySelectorAll(opts.selector);
    bySelector.forEach(function(h) { if (h.shadowRoot) hosts.push(h); });

    // Strategy 2: Generic shadow host detection (elements with shadowRoot)
    if (hosts.length === 0) {
      root.querySelectorAll('*').forEach(function(el) {
        if (el.shadowRoot && hosts.indexOf(el) === -1) {
          hosts.push(el);
        }
      });
    }
    return hosts;
  }

  // Helper: find clickable element inside a shadow root
  function findClickable(shadowRoot) {
    // Try specific patterns first
    var selectors = [
      'div[style*="cursor"]',
      'div[style*="padding"]',
      'button',
      '[role="button"]',
      'a',
      'div'
    ];
    for (var i = 0; i < selectors.length; i++) {
      var el = shadowRoot.querySelector(selectors[i]);
      if (el && el.offsetWidth > 0) return el;
    }
    return null;
  }

  // Sequential async traversal using Promises
  function traverseLevel(root, depth) {
    return new Promise(function(resolve) {
      if (depth >= opts.maxLevels) {
        resolve();
        return;
      }

      var hosts = findShadowHosts(root);
      if (hosts.length === 0) {
        resolve();
        return;
      }

      // Process first host at this level
      var host = hosts[0];
      var inner = findClickable(host.shadowRoot);

      if (inner) {
        if (opts.action === 'click') {
          inner.click();
          clickedCount++;
          results.push('Clicked shadow level ' + (depth + 1) + ': ' + (inner.textContent || '').substring(0, 30).trim());
        } else {
          results.push('Level ' + (depth + 1) + ': ' + (inner.textContent || '').substring(0, 50).trim());
        }

        // Wait for DOM to update, then recurse into the shadow root
        setTimeout(function() {
          traverseLevel(host.shadowRoot, depth + 1).then(resolve);
        }, 150);
      } else {
        // No clickable found, try next host
        if (hosts.length > 1) {
          var nextHost = hosts[1];
          if (nextHost.shadowRoot) {
            var nextInner = findClickable(nextHost.shadowRoot);
            if (nextInner) {
              if (opts.action === 'click') {
                nextInner.click();
                clickedCount++;
                results.push('Clicked shadow level ' + (depth + 1) + ' (alt): ' + (nextInner.textContent || '').substring(0, 30).trim());
              }
              setTimeout(function() {
                traverseLevel(nextHost.shadowRoot, depth + 1).then(resolve);
              }, 150);
              return;
            }
          }
        }
        resolve();
      }
    });
  }

  // Retry wrapper: retry traversal if no results on first attempt
  function attemptWithRetry(retriesLeft) {
    return new Promise(function(resolve) {
      results = [];
      clickedCount = 0;

      traverseLevel(document, 0).then(function() {
        if (clickedCount === 0 && retriesLeft > 0) {
          // Wait and retry — shadow roots may not have rendered yet
          setTimeout(function() {
            attemptWithRetry(retriesLeft - 1).then(resolve);
          }, opts.retryDelay);
        } else {
          if (clickedCount > 0) {
            resolve('Clicked ' + clickedCount + ' shadow DOM levels: ' + results.join('; '));
          } else {
            resolve('No shadow DOM elements found with selector: ' + opts.selector);
          }
        }
      });
    });
  }

  return attemptWithRetry(opts.retries);
}
