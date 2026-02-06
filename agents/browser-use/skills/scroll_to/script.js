function(options) {
  var opts = Object.assign({position: null, selector: null, y: null, smooth: false}, options || {});
  var behavior = opts.smooth ? 'smooth' : 'instant';

  if (opts.position === 'top') {
    window.scrollTo({top: 0, left: 0, behavior: behavior});
    return 'Scrolled to top (y=0)';
  }
  if (opts.position === 'bottom') {
    window.scrollTo({top: document.documentElement.scrollHeight, left: 0, behavior: behavior});
    return 'Scrolled to bottom (y=' + document.documentElement.scrollHeight + ')';
  }
  if (opts.selector) {
    var el = document.querySelector(opts.selector);
    if (!el) return 'ERROR: Element not found for selector: ' + opts.selector;
    el.scrollIntoView({behavior: behavior, block: 'center'});
    var rect = el.getBoundingClientRect();
    return 'Scrolled to element at y=' + Math.round(rect.top + window.scrollY) + ' (' + opts.selector + ')';
  }
  if (typeof opts.y === 'number') {
    window.scrollTo({top: opts.y, left: 0, behavior: behavior});
    return 'Scrolled to y=' + opts.y;
  }
  return 'ERROR: Provide position ("top"/"bottom"), selector, or y value';
}
