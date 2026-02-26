/**
 * React Framework Interaction Primitives
 *
 * Generic utilities for interacting with React applications via the browser DOM.
 * These use React's internal fiber architecture (stable since React 16) to:
 * - Dispatch events through React's synthetic event system
 * - Fill controlled inputs by resetting React's value tracker
 * - Walk the fiber tree to find component callbacks
 * - Extract state from component hooks
 *
 * These patterns work on ANY React app — they rely on React's public-facing
 * internal keys (__reactFiber$, __reactProps$, etc.) which are stable across
 * React 16-19 and widely documented in the React DevTools ecosystem.
 *
 * Usage: Injected via page.evaluate() or as a CDP init script.
 * All functions are namespaced under window.__FW (framework).
 */
(function() {
  'use strict';

  // ═══════════════════════════════════════════════════════════════
  // FRAMEWORK DETECTION
  // ═══════════════════════════════════════════════════════════════

  /**
   * Detect which frontend framework(s) the page uses.
   * @returns {Object} { react: bool, vue: bool, angular: bool, svelte: bool, webcomponent: bool }
   */
  function detectFrameworks() {
    var root = document.getElementById('root') || document.getElementById('app') || document.body;
    var sample = root ? root.firstElementChild || root : document.body;
    var keys = sample ? Object.keys(sample) : [];

    return {
      react:        keys.some(function(k) { return k.indexOf('__reactFiber') === 0 || k.indexOf('__reactInternalInstance') === 0; }),
      vue:          !!sample.__vue__ || !!sample.__vue_app__ || keys.some(function(k) { return k.indexOf('__vue') === 0; }),
      angular:      !!window.ng || keys.some(function(k) { return k.indexOf('__ngContext__') === 0 || k.indexOf('_nghost') === 0; }),
      svelte:       !!sample.__svelte_meta || keys.some(function(k) { return k.indexOf('__svelte') === 0; }),
      webcomponent: !!document.querySelector('[shadowroot], *') && (function() {
        var all = document.querySelectorAll('*');
        for (var i = 0; i < all.length; i++) { if (all[i].shadowRoot) return true; }
        return false;
      })()
    };
  }

  // ═══════════════════════════════════════════════════════════════
  // REACT: KEY LOOKUP
  // ═══════════════════════════════════════════════════════════════

  /**
   * Find the __reactProps$xxx key on a DOM element.
   * @param {Element} el
   * @returns {string|null} The props key, or null if not React
   */
  function getPropsKey(el) {
    if (!el) return null;
    var keys = Object.keys(el);
    for (var i = 0; i < keys.length; i++) {
      if (keys[i].indexOf('__reactProps') === 0) return keys[i];
    }
    return null;
  }

  /**
   * Find the __reactFiber$xxx key on a DOM element.
   * @param {Element} el
   * @returns {string|null} The fiber key, or null if not React
   */
  function getFiberKey(el) {
    if (!el) return null;
    var keys = Object.keys(el);
    for (var i = 0; i < keys.length; i++) {
      if (keys[i].indexOf('__reactFiber') === 0 || keys[i].indexOf('__reactInternalInstance') === 0) return keys[i];
    }
    return null;
  }

  /**
   * Get the React props object for a DOM element.
   * @param {Element} el
   * @returns {Object|null}
   */
  function getProps(el) {
    var key = getPropsKey(el);
    return key ? el[key] : null;
  }

  /**
   * Get the React fiber node for a DOM element.
   * @param {Element} el
   * @returns {Object|null}
   */
  function getFiber(el) {
    var key = getFiberKey(el);
    return key ? el[key] : null;
  }

  // ═══════════════════════════════════════════════════════════════
  // REACT: EVENT DISPATCH
  // ═══════════════════════════════════════════════════════════════

  /** Synthetic event stub for React event handlers */
  function syntheticEvent(el, type) {
    return {
      type: type || 'click',
      target: el,
      currentTarget: el,
      nativeEvent: {},
      preventDefault: function() {},
      stopPropagation: function() {},
      persist: function() {},
      bubbles: true,
      cancelable: true
    };
  }

  /**
   * Dispatch a click via React's __reactProps.onClick.
   * Falls back to native el.click() if no React handler found.
   * @param {Element} el
   * @returns {boolean} true if React onClick was called
   */
  function dispatchClick(el) {
    var props = getProps(el);
    if (props && typeof props.onClick === 'function') {
      try { props.onClick(syntheticEvent(el, 'click')); return true; }
      catch(e) { /* fall through */ }
    }
    el.click();
    return false;
  }

  /**
   * Dispatch an onChange event via React's __reactProps.onChange.
   * @param {Element} el
   * @param {*} value
   * @returns {boolean}
   */
  function dispatchChange(el, value) {
    var props = getProps(el);
    if (props && typeof props.onChange === 'function') {
      try {
        props.onChange({ target: { value: value }, currentTarget: { value: value },
                         preventDefault: function(){}, stopPropagation: function(){} });
        return true;
      } catch(e) { /* fall through */ }
    }
    return false;
  }

  /**
   * Dispatch a drag event pair via React's __reactProps.
   * @param {Element} source - The draggable element
   * @param {Element} target - The drop target
   * @returns {boolean}
   */
  function dispatchDrag(source, target) {
    var sp = getProps(source), tp = getProps(target);
    var ok = false;
    if (sp && typeof sp.onDragStart === 'function') {
      try { sp.onDragStart(syntheticEvent(source, 'dragstart')); ok = true; } catch(e) {}
    }
    if (tp && typeof tp.onDrop === 'function') {
      try { tp.onDrop(syntheticEvent(target, 'drop')); ok = true; } catch(e) {}
    }
    return ok;
  }

  // ═══════════════════════════════════════════════════════════════
  // REACT: CONTROLLED INPUT FILL
  // ═══════════════════════════════════════════════════════════════

  /**
   * Fill a controlled input field in a React app.
   * Uses _valueTracker reset + native setter to make React detect the change.
   * @param {HTMLInputElement} input
   * @param {string} value
   * @returns {boolean} true if successful
   */
  function fillInput(input, value) {
    if (!input) return false;
    var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value'
    );
    if (nativeInputValueSetter && nativeInputValueSetter.set) {
      nativeInputValueSetter.set.call(input, value);
    } else {
      input.value = value;
    }
    // Reset React's _valueTracker so React sees the change
    var tracker = input._valueTracker;
    if (tracker) { tracker.setValue(''); }
    // Dispatch events React listens for
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  // ═══════════════════════════════════════════════════════════════
  // REACT: FIBER TREE TRAVERSAL
  // ═══════════════════════════════════════════════════════════════

  /**
   * Walk UP the fiber tree from an element, calling predicate on each node.
   * Returns the first fiber where predicate returns truthy.
   * @param {Element} el - DOM element to start from
   * @param {Function} predicate - (fiber) => truthy value
   * @param {number} [maxDepth=50] - Safety limit
   * @returns {Object|null} The matching fiber, or null
   */
  function walkFiberUp(el, predicate, maxDepth) {
    var fiber = getFiber(el);
    if (!fiber) return null;
    maxDepth = maxDepth || 50;
    for (var f = fiber, depth = 0; f && depth < maxDepth; f = f.return, depth++) {
      var result = predicate(f);
      if (result) return f;
    }
    return null;
  }

  /**
   * BFS the fiber subtree from an element, calling predicate on each node.
   * @param {Element} el
   * @param {Function} predicate - (fiber) => truthy
   * @param {number} [maxNodes=200]
   * @returns {Object|null}
   */
  function walkFiberDown(el, predicate, maxNodes) {
    var fiber = getFiber(el);
    if (!fiber) return null;
    maxNodes = maxNodes || 200;
    var queue = [fiber], visited = 0;
    while (queue.length > 0 && visited < maxNodes) {
      var f = queue.shift();
      visited++;
      var result = predicate(f);
      if (result) return f;
      if (f.child) queue.push(f.child);
      if (f.sibling) queue.push(f.sibling);
    }
    return null;
  }

  /**
   * Find and call an onComplete callback in the fiber tree above the given element.
   * This is a common React pattern for form/challenge completion callbacks.
   * @param {Element} el - DOM element to start walking from
   * @param {string} type - Proof type string
   * @param {Object} [data={}] - Additional data for the proof
   * @returns {*} Return value from onComplete, or null
   */
  function callOnComplete(el, type, data) {
    var fiber = walkFiberUp(el, function(f) {
      return f.memoizedProps && typeof f.memoizedProps.onComplete === 'function';
    });
    if (!fiber) return null;
    try {
      return fiber.memoizedProps.onComplete({
        type: type || 'unknown',
        timestamp: Date.now(),
        data: data || {}
      });
    } catch(e) { return null; }
  }

  // ═══════════════════════════════════════════════════════════════
  // REACT: STATE EXTRACTION
  // ═══════════════════════════════════════════════════════════════

  /**
   * Extract strings from memoizedState hook chain of a fiber.
   * Useful for finding codes/tokens stored in React state.
   * @param {Object} fiber
   * @param {RegExp} [pattern] - Optional filter regex
   * @returns {string[]}
   */
  function extractHookState(fiber, pattern) {
    var results = [];
    if (!fiber || !fiber.memoizedState) return results;
    var state = fiber.memoizedState;
    for (var depth = 0; state && depth < 20; state = state.next, depth++) {
      var val = state.memoizedState;
      if (typeof val === 'string') {
        if (!pattern || pattern.test(val)) results.push(val);
      }
      // Also check queue.lastRenderedState (class component pattern)
      if (state.queue && typeof state.queue.lastRenderedState === 'string') {
        var lrs = state.queue.lastRenderedState;
        if (!pattern || pattern.test(lrs)) results.push(lrs);
      }
    }
    return results;
  }

  /**
   * Extract strings from the fiber tree above an element.
   * Walks up the tree and collects matching state from each fiber's hooks.
   * @param {Element} el
   * @param {RegExp} [pattern]
   * @returns {string[]}
   */
  function extractStateAbove(el, pattern) {
    var results = [];
    var fiber = getFiber(el);
    if (!fiber) return results;
    for (var f = fiber, depth = 0; f && depth < 50; f = f.return, depth++) {
      var stateResults = extractHookState(f, pattern);
      for (var i = 0; i < stateResults.length; i++) {
        if (results.indexOf(stateResults[i]) === -1) results.push(stateResults[i]);
      }
    }
    return results;
  }

  // ═══════════════════════════════════════════════════════════════
  // SHADOW DOM TRAVERSAL
  // ═══════════════════════════════════════════════════════════════

  /**
   * Recursively walk shadow DOM trees and collect text content.
   * @param {Element} root
   * @param {number} [maxDepth=5]
   * @returns {string[]} Array of text content from shadow roots
   */
  function walkShadowDOM(root, maxDepth) {
    maxDepth = maxDepth || 5;
    var texts = [];
    function walk(el, depth) {
      if (depth > maxDepth) return;
      if (el.shadowRoot) {
        texts.push(el.shadowRoot.textContent || '');
        var children = el.shadowRoot.querySelectorAll('*');
        for (var i = 0; i < children.length; i++) walk(children[i], depth + 1);
      }
    }
    var all = (root || document).querySelectorAll('*');
    for (var i = 0; i < all.length; i++) walk(all[i], 0);
    return texts;
  }

  // ═══════════════════════════════════════════════════════════════
  // VUE: BASIC INTERACTION (future)
  // ═══════════════════════════════════════════════════════════════

  /**
   * Get Vue component instance from a DOM element.
   * Works with Vue 2 (__vue__) and Vue 3 (__vue_app__).
   * @param {Element} el
   * @returns {Object|null}
   */
  function getVueInstance(el) {
    if (el.__vue__) return el.__vue__;
    if (el.__vue_app__) return el.__vue_app__;
    // Vue 3: walk up to find the app
    var parent = el;
    while (parent) {
      if (parent.__vue_app__) return parent.__vue_app__;
      parent = parent.parentElement;
    }
    return null;
  }

  // ═══════════════════════════════════════════════════════════════
  // ANGULAR: BASIC INTERACTION (future)
  // ═══════════════════════════════════════════════════════════════

  /**
   * Get Angular component context from a DOM element.
   * @param {Element} el
   * @returns {Object|null}
   */
  function getAngularContext(el) {
    if (window.ng && window.ng.probe) {
      try { return window.ng.probe(el); } catch(e) {}
    }
    var keys = Object.keys(el);
    for (var i = 0; i < keys.length; i++) {
      if (keys[i].indexOf('__ngContext__') === 0) return el[keys[i]];
    }
    return null;
  }

  // ═══════════════════════════════════════════════════════════════
  // PUBLIC API
  // ═══════════════════════════════════════════════════════════════

  window.__FW = {
    // Detection
    detect: detectFrameworks,

    // React: key lookup
    getPropsKey: getPropsKey,
    getFiberKey: getFiberKey,
    getProps: getProps,
    getFiber: getFiber,

    // React: events
    syntheticEvent: syntheticEvent,
    dispatchClick: dispatchClick,
    dispatchChange: dispatchChange,
    dispatchDrag: dispatchDrag,

    // React: input
    fillInput: fillInput,

    // React: fiber traversal
    walkFiberUp: walkFiberUp,
    walkFiberDown: walkFiberDown,
    callOnComplete: callOnComplete,

    // React: state extraction
    extractHookState: extractHookState,
    extractStateAbove: extractStateAbove,

    // Shadow DOM
    walkShadowDOM: walkShadowDOM,

    // Vue (future)
    getVueInstance: getVueInstance,

    // Angular (future)
    getAngularContext: getAngularContext,
  };
})();
