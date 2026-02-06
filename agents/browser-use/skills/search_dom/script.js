function(options) {
  var opts = Object.assign({text: null, selector: null, maxResults: 20, includeHidden: true}, options || {});

  var results = [];

  function getInfo(el) {
    var rect = el.getBoundingClientRect();
    var style = window.getComputedStyle(el);
    var visible = style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0;
    return {
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      class: el.className ? (typeof el.className === 'string' ? el.className.substring(0, 80) : '') : null,
      text: el.textContent.trim().substring(0, 100),
      visible: visible,
      clickable: el.tagName === 'BUTTON' || el.tagName === 'A' || el.getAttribute('role') === 'button' || el.onclick !== null || style.cursor === 'pointer',
      pos: {top: Math.round(rect.top), left: Math.round(rect.left)},
      attrs: {}
    };
  }

  if (opts.selector) {
    var els = document.querySelectorAll(opts.selector);
    for (var i = 0; i < els.length && results.length < opts.maxResults; i++) {
      var info = getInfo(els[i]);
      if (opts.includeHidden || info.visible) {
        results.push(info);
      }
    }
  } else if (opts.text) {
    var pattern = opts.text.toLowerCase();
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT, null, false);
    var node;
    while ((node = walker.nextNode()) && results.length < opts.maxResults) {
      var nodeText = node.textContent.trim().toLowerCase();
      // Only match elements where OWN text (not deeply nested) contains pattern
      var ownText = '';
      for (var c = 0; c < node.childNodes.length; c++) {
        if (node.childNodes[c].nodeType === 3) ownText += node.childNodes[c].textContent;
      }
      if (ownText.toLowerCase().indexOf(pattern) !== -1 || (node.children.length === 0 && nodeText.indexOf(pattern) !== -1)) {
        var info = getInfo(node);
        if (opts.includeHidden || info.visible) {
          results.push(info);
        }
      }
    }
  }

  return JSON.stringify({count: results.length, results: results});
}
