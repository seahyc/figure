function(options) {
  var opts = Object.assign({
    scope: 'body',       // CSS selector for subtree to watch
    maxBuffer: 200,      // Max events in circular buffer
    reset: false         // Clear buffer after reading
  }, options || {});

  var STORE = '__domChangeLog';
  var OBSERVER_KEY = '__domChangeObserver';

  // Install observer if not already running (idempotent)
  if (!window[OBSERVER_KEY]) {
    window[STORE] = [];

    var root = opts.scope === 'body'
      ? document.body
      : document.querySelector(opts.scope) || document.body;

    var observer = new MutationObserver(function(mutations) {
      var log = window[STORE];
      var now = Date.now();

      for (var i = 0; i < mutations.length; i++) {
        var m = mutations[i];

        if (m.type === 'childList') {
          for (var a = 0; a < m.addedNodes.length; a++) {
            var n = m.addedNodes[a];
            if (n.nodeType !== 1) continue;
            // Skip our own injected elements
            if (n.id === '__dom-changes-summary') continue;
            var text = (n.textContent || '').trim().substring(0, 80);
            if (text.length < 2) continue;
            log.push({ t: now, type: 'added', tag: n.tagName, id: n.id || '', cls: (n.className || '').toString().substring(0, 30), text: text });
          }
          for (var r = 0; r < m.removedNodes.length; r++) {
            var rn = m.removedNodes[r];
            if (rn.nodeType !== 1) continue;
            var rtext = (rn.textContent || '').trim().substring(0, 80);
            if (rtext.length < 2) continue;
            log.push({ t: now, type: 'removed', tag: rn.tagName, id: rn.id || '', text: rtext });
          }
        } else if (m.type === 'characterData') {
          var parent = m.target.parentElement;
          if (!parent) continue;
          var ctext = (m.target.textContent || '').trim().substring(0, 80);
          log.push({ t: now, type: 'text', tag: parent.tagName, id: parent.id || '', cls: (parent.className || '').toString().substring(0, 30), text: ctext });
        }
      }

      // Circular buffer: trim oldest entries
      if (log.length > opts.maxBuffer) {
        window[STORE] = log.slice(log.length - opts.maxBuffer);
      }
    });

    observer.observe(root, {
      childList: true,
      subtree: true,
      characterData: true
    });

    window[OBSERVER_KEY] = observer;
    return 'Observer started on ' + (opts.scope === 'body' ? 'document.body' : opts.scope);
  }

  // Observer already running — return summary of recent changes
  var log = window[STORE] || [];

  if (log.length === 0) {
    return 'No DOM changes recorded';
  }

  // Group by pattern (type + tag + id/class)
  var groups = {};
  for (var j = 0; j < log.length; j++) {
    var e = log[j];
    var key = e.type + ':' + e.tag + (e.id ? '#' + e.id : '') + (e.cls ? '.' + e.cls.split(' ')[0] : '');
    if (!groups[key]) groups[key] = { count: 0, samples: [], first: e.t, last: e.t };
    groups[key].count++;
    groups[key].last = e.t;
    if (groups[key].samples.length < 2) groups[key].samples.push(e.text);
  }

  // Build summary sorted by frequency
  var entries = [];
  for (var k in groups) entries.push({ key: k, data: groups[k] });
  entries.sort(function(a, b) { return b.data.count - a.data.count; });

  var totalMs = log.length > 1 ? log[log.length - 1].t - log[0].t : 0;
  var lines = [log.length + ' changes over ' + (totalMs / 1000).toFixed(1) + 's:'];
  for (var m = 0; m < Math.min(entries.length, 10); m++) {
    var ent = entries[m];
    var sample = ent.data.samples[0] || '';
    if (sample.length > 40) sample = sample.substring(0, 40) + '...';
    lines.push('  ' + ent.key + ': ' + ent.data.count + 'x (e.g. "' + sample + '")');
  }

  if (opts.reset) {
    window[STORE] = [];
  }

  return lines.join('\n');
}
