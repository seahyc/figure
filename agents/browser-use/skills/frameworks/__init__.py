"""
Framework Interaction Primitives

Generic utilities for interacting with frontend framework applications (React,
Vue, Angular, Svelte, Web Components) via the browser DOM.

These are standard browser automation patterns used by tools like React DevTools,
Playwright's internal locators, and Cypress. They rely on framework-specific
internal keys that are stable across major versions and widely documented.

Usage:
    from skills.frameworks import inject_framework_helpers, fw

    # Inject once at page load (or via CDP init script)
    await inject_framework_helpers(page)

    # Then use the helpers in page.evaluate calls
    codes = await page.evaluate('''() => {
        var btn = document.querySelector('button');
        __FW.dispatchClick(btn);
        return __FW.extractStateAbove(btn, /^[A-Z0-9]{6}$/);
    }''')
"""

import json
from pathlib import Path

# Load the JS library once at import time
_JS_PATH = Path(__file__).parent / "react.js"
_JS_CODE = _JS_PATH.read_text()


async def inject_framework_helpers(page) -> None:
    """Inject the __FW namespace into the page.

    Safe to call multiple times — the IIFE is idempotent.
    """
    await page.evaluate(_JS_CODE)


async def detect_frameworks(page) -> dict:
    """Detect which frontend frameworks the page uses.

    Returns: {"react": bool, "vue": bool, "angular": bool, "svelte": bool, "webcomponent": bool}
    """
    await inject_framework_helpers(page)
    result = await page.evaluate("() => JSON.stringify(__FW.detect())")
    return json.loads(result) if isinstance(result, str) else (result or {})


async def fw_dispatch_click(page, selector: str) -> int:
    """Click all elements matching selector via React's onClick.

    Returns number of elements clicked.
    """
    await inject_framework_helpers(page)
    result = await page.evaluate(r"""(sel) => {
        var els = document.querySelectorAll(sel);
        var clicked = 0;
        for (var i = 0; i < els.length; i++) {
            if (__FW.dispatchClick(els[i])) clicked++;
        }
        return clicked;
    }""", selector)
    return result or 0


async def fw_fill_input(page, selector: str, value: str) -> bool:
    """Fill input matching selector using React-compatible value setter."""
    await inject_framework_helpers(page)
    result = await page.evaluate(r"""(args) => {
        var inp = document.querySelector(args[0]);
        return inp ? __FW.fillInput(inp, args[1]) : false;
    }""", [selector, value])
    return bool(result)


async def fw_call_on_complete(page, selectors: list, proof_type: str = "unknown") -> list:
    """Walk fiber tree from elements matching selectors, call onComplete, return codes.

    Args:
        selectors: CSS selectors to start walking from
        proof_type: The proof type string for the onComplete call

    Returns: List of 6-char codes found
    """
    await inject_framework_helpers(page)
    result = await page.evaluate(r"""(args) => {
        var selectors = args[0];
        var proofType = args[1];
        var codeRE = /^[A-HJ-NP-Z2-9]{6}$/;
        var codes = [];
        for (var si = 0; si < selectors.length; si++) {
            var els = document.querySelectorAll(selectors[si]);
            for (var ci = 0; ci < els.length; ci++) {
                var ret = __FW.callOnComplete(els[ci], proofType);
                if (typeof ret === 'string' && codeRE.test(ret) && /[A-HJ-NP-Z]/.test(ret)) {
                    codes.push(ret);
                }
            }
        }
        return JSON.stringify(codes);
    }""", [selectors, proof_type])
    return json.loads(result) if isinstance(result, str) else (result or [])


async def fw_extract_state(page, selector: str, pattern: str = None) -> list:
    """Extract strings from React hook state above elements matching selector.

    Args:
        selector: CSS selector for starting elements
        pattern: Optional regex pattern to filter results (as string)

    Returns: List of matching strings from fiber state
    """
    await inject_framework_helpers(page)
    result = await page.evaluate(r"""(args) => {
        var sel = args[0];
        var pat = args[1] ? new RegExp(args[1]) : null;
        var all = [];
        var els = document.querySelectorAll(sel);
        for (var i = 0; i < els.length; i++) {
            var strings = __FW.extractStateAbove(els[i], pat);
            for (var j = 0; j < strings.length; j++) {
                if (all.indexOf(strings[j]) === -1) all.push(strings[j]);
            }
        }
        return JSON.stringify(all);
    }""", [selector, pattern])
    return json.loads(result) if isinstance(result, str) else (result or [])


async def fw_dispatch_drag(page, source_selector: str, target_selector: str) -> int:
    """Dispatch React drag events between source and target elements.

    Returns number of drag pairs dispatched.
    """
    await inject_framework_helpers(page)
    result = await page.evaluate(r"""(args) => {
        var sources = document.querySelectorAll(args[0]);
        var targets = document.querySelectorAll(args[1]);
        var count = 0;
        var used = {};
        for (var si = 0; si < sources.length; si++) {
            for (var ti = 0; ti < targets.length; ti++) {
                if (used[ti]) continue;
                if (__FW.dispatchDrag(sources[si], targets[ti])) {
                    used[ti] = true;
                    count++;
                    break;
                }
            }
        }
        return count;
    }""", [source_selector, target_selector])
    return result or 0


async def fw_walk_shadow_dom(page, pattern: str = None) -> list:
    """Walk all shadow DOM trees and extract text content.

    Args:
        pattern: Optional regex to filter text chunks

    Returns: List of text strings from shadow roots
    """
    await inject_framework_helpers(page)
    result = await page.evaluate(r"""(pat) => {
        var texts = __FW.walkShadowDOM(document);
        if (pat) {
            var re = new RegExp(pat);
            texts = texts.filter(function(t) { return re.test(t); });
        }
        return JSON.stringify(texts);
    }""", pattern)
    return json.loads(result) if isinstance(result, str) else (result or [])


# Convenience aliases
fw = type('fw', (), {
    'inject': staticmethod(inject_framework_helpers),
    'detect': staticmethod(detect_frameworks),
    'click': staticmethod(fw_dispatch_click),
    'fill': staticmethod(fw_fill_input),
    'on_complete': staticmethod(fw_call_on_complete),
    'extract_state': staticmethod(fw_extract_state),
    'drag': staticmethod(fw_dispatch_drag),
    'shadow_dom': staticmethod(fw_walk_shadow_dom),
})()
