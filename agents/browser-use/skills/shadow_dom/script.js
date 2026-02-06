function(options) {
  var opts = Object.assign({selector: '.shadow-level-host', action: 'click', maxLevels: 10}, options || {});
  var results = [];
  var clickedCount = 0;

  // Recursive function to traverse and click through nested shadow DOM levels
  function traverseAndClick(root, depth) {
    if (depth >= opts.maxLevels) return;

    // Find shadow host at current level
    var host = root.querySelector(opts.selector);
    if (!host || !host.shadowRoot) return;

    // Find clickable element inside shadow root (the wrapper div)
    var inner = host.shadowRoot.querySelector('div[style*="cursor"], div[style*="padding"], button, [role="button"]');
    if (inner) {
      if (opts.action === 'click') {
        inner.click();
        clickedCount++;
        results.push('Clicked shadow level ' + (depth + 1) + ': ' + (inner.textContent || '').substring(0, 30).trim());
      } else {
        results.push('Level ' + (depth + 1) + ': ' + (inner.textContent || '').substring(0, 50).trim());
      }

      // After clicking, look for next level inside this shadow root
      // Use setTimeout to allow DOM to update, then recurse
      setTimeout(function() {
        traverseAndClick(host.shadowRoot, depth + 1);
      }, 100);
    }
  }

  // Also try finding hosts anywhere in the document (not just at root)
  function findAllShadowHosts(root, depth) {
    if (depth >= opts.maxLevels) return;

    var hosts = root.querySelectorAll(opts.selector);
    hosts.forEach(function(host) {
      if (host.shadowRoot) {
        var inner = host.shadowRoot.querySelector('div, button');
        if (inner) {
          if (opts.action === 'click') {
            inner.click();
            clickedCount++;
            results.push('Clicked shadow level ' + (depth + 1));
          }
        }
        // Recurse into this shadow root to find nested hosts
        findAllShadowHosts(host.shadowRoot, depth + 1);
      }
    });
  }

  // Try sequential traversal first
  traverseAndClick(document, 0);

  // If no results, try finding all hosts
  if (results.length === 0) {
    findAllShadowHosts(document, 0);
  }

  // Return a promise that resolves after all clicks have been processed
  return new Promise(function(resolve) {
    setTimeout(function() {
      if (clickedCount > 0) {
        resolve('Clicked ' + clickedCount + ' shadow DOM levels: ' + results.join('; '));
      } else {
        resolve('No shadow DOM elements found with selector: ' + opts.selector);
      }
    }, opts.maxLevels * 150); // Wait for all sequential clicks
  });
}
