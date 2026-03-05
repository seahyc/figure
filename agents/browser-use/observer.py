"""
Observer: Extract structured observations from browser pages.

Produces an Observation dataclass with ARIA snapshot, visible text,
page structural features, button texts, and input fields.
Generic — no challenge-specific scanning.
"""

import json
from dataclasses import dataclass, field


@dataclass
class Observation:
    """Structured observation of the current page state."""
    url: str = ""
    title: str = ""
    visible_text: str = ""
    aria_snapshot: str = ""
    button_texts: list[str] = field(default_factory=list)
    input_fields: list[dict] = field(default_factory=list)
    page_features: dict = field(default_factory=dict)
    changes: str = ""  # what changed since last observation

    def diff_from(self, prev: 'Observation') -> str:
        """Compute what changed since the previous observation."""
        diffs = []
        if self.url != prev.url:
            diffs.append(f"URL changed: {prev.url} → {self.url}")
        new_buttons = set(self.button_texts) - set(prev.button_texts)
        gone_buttons = set(prev.button_texts) - set(self.button_texts)
        if new_buttons:
            diffs.append(f"new buttons: {list(new_buttons)[:5]}")
        if gone_buttons:
            diffs.append(f"removed buttons: {list(gone_buttons)[:5]}")
        # Check for new visible text (first 200 chars diff)
        if self.visible_text[:200] != prev.visible_text[:200]:
            diffs.append("page content changed")
        # Input value changes
        prev_vals = {(i.get('name') or i.get('id') or str(idx)): i.get('value', '')
                     for idx, i in enumerate(prev.input_fields)}
        for idx, inp in enumerate(self.input_fields):
            key = inp.get('name') or inp.get('id') or str(idx)
            old_val = prev_vals.get(key, '')
            new_val = inp.get('value', '')
            if old_val != new_val:
                diffs.append(f"input '{key}' value: '{old_val}' → '{new_val}'")
        return "; ".join(diffs) if diffs else "no changes"

    def to_prompt(self) -> str:
        """Format observation as compact text for the planner."""
        parts = []
        if self.url:
            parts.append(f"url: {self.url}")
        if self.title:
            parts.append(f"title: {self.title}")
        if self.changes:
            parts.append(f"changes: {self.changes}")
        if self.visible_text:
            parts.append(f"visible_text: {self.visible_text[:800]}")
        if self.button_texts:
            parts.append(f"buttons: {self.button_texts}")
        if self.input_fields:
            parts.append(f"inputs: {json.dumps(self.input_fields)}")
        if self.page_features:
            feats = ", ".join(f"{k}={v}" for k, v in self.page_features.items()
                             if v is not None and v is not False and v != 0)
            if feats:
                parts.append(f"features: {feats}")
        if self.aria_snapshot:
            parts.append(f"aria_snapshot:\n{self.aria_snapshot[:1500]}")
        return "\n".join(parts)


# JS to extract page structural info — generic, no code regex
_PAGE_SCAN_JS = r"""() => {
    function isVis(el) {
        if (!el || !el.getBoundingClientRect) return false;
        var r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
        var s = window.getComputedStyle(el);
        return s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity) > 0;
    }

    var result = {
        url: window.location.href,
        title: document.title,
        visibleText: '',
        buttonTexts: [],
        inputFields: [],
        features: {}
    };

    // Visible text — first 800 chars of body text
    result.visibleText = document.body ? document.body.innerText.substring(0, 800) : '';

    // Collect visible button texts — prioritize non-decoy buttons
    var btns = document.querySelectorAll('button, [role="button"], a[href]');
    var DECOY = ['next','continue','proceed','advance','move on','go forward','keep going',
                 'click here','next step','next page','next section','continue reading',
                 'continue journey','proceed forward','move on'];
    var primary = new Set();
    var decoy = new Set();
    for (var i = 0; i < btns.length; i++) {
        var t = btns[i].textContent.trim();
        if (!t || !isVis(btns[i]) || btns[i].disabled || t.length > 50) continue;
        if (DECOY.indexOf(t.toLowerCase()) !== -1) { decoy.add(t); }
        else { primary.add(t); }
        if (primary.size + decoy.size >= 40) break;
    }
    result.buttonTexts = Array.from(primary).concat(Array.from(decoy).slice(0, 5));

    // Collect input fields with metadata
    var inputs = document.querySelectorAll('input, textarea, select');
    for (var i = 0; i < inputs.length && result.inputFields.length < 15; i++) {
        var inp = inputs[i];
        if (!isVis(inp)) continue;
        result.inputFields.push({
            type: inp.type || inp.tagName.toLowerCase(),
            placeholder: (inp.placeholder || '').substring(0, 50),
            value: (inp.value || '').substring(0, 30),
            name: inp.name || '',
            id: inp.id || ''
        });
    }

    // Structural features
    var allEls = document.querySelectorAll('*');
    var hasShadow = false;
    for (var i = 0; i < allEls.length; i++) {
        if (allEls[i].shadowRoot) { hasShadow = true; break; }
    }

    result.features = {
        hasCanvas: !!document.querySelector('canvas'),
        hasForm: !!document.querySelector('form'),
        hasDraggables: !!document.querySelector('[draggable="true"]'),
        hasShadowRoots: hasShadow,
        hasIframes: document.querySelectorAll('iframe').length > 0,
        iframeCount: document.querySelectorAll('iframe').length,
        inputCount: document.querySelectorAll('input, textarea').length,
        buttonCount: document.querySelectorAll('button').length,
        linkCount: document.querySelectorAll('a[href]').length,
        hasVideo: !!document.querySelector('video'),
        hasAudio: !!document.querySelector('audio'),
        overlayCount: (function() {
            var count = 0;
            var divs = document.querySelectorAll('div');
            for (var i = 0; i < divs.length; i++) {
                var s = getComputedStyle(divs[i]);
                if ((s.position === 'fixed' || s.position === 'absolute') && parseInt(s.zIndex) >= 1000) count++;
            }
            return count;
        })()
    };

    return JSON.stringify(result);
}"""


async def observe(page) -> Observation:
    """Extract a structured Observation from the current page.

    Args:
        page: A browser-use page object (supports .evaluate() and .content()).

    Returns:
        Observation dataclass with page state.
    """
    obs = Observation()

    # Run page scan JS
    try:
        raw = await page.evaluate(_PAGE_SCAN_JS)
        data = json.loads(raw) if isinstance(raw, str) else raw
        obs.url = data.get("url", "")
        obs.title = data.get("title", "")
        obs.visible_text = data.get("visibleText", "")
        obs.button_texts = data.get("buttonTexts", [])
        obs.input_fields = data.get("inputFields", [])
        obs.page_features = data.get("features", {})
    except Exception as e:
        print(f"    [observer] Page scan error: {e}")

    # Get ARIA snapshot via accessibility tree (if available)
    try:
        aria = await page.evaluate(r"""() => {
            // Build a lightweight ARIA-like snapshot from the DOM
            var lines = [];
            var walk = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
            var count = 0;
            while (walk.nextNode() && count < 200) {
                var el = walk.currentNode;
                var role = el.getAttribute('role') || el.tagName.toLowerCase();
                var label = el.getAttribute('aria-label') || el.getAttribute('alt') || '';
                var text = '';
                // Get direct text content (not children's)
                for (var c = el.firstChild; c; c = c.nextSibling) {
                    if (c.nodeType === 3) text += c.textContent.trim() + ' ';
                }
                text = text.trim().substring(0, 80);
                if (!text && !label) continue;
                var r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                var s = window.getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden') continue;
                var indent = '';
                var depth = 0;
                var p = el.parentElement;
                while (p && p !== document.body && depth < 10) { depth++; p = p.parentElement; }
                indent = '  '.repeat(Math.min(depth, 6));
                var line = indent + role;
                if (label) line += ' [' + label + ']';
                if (text) line += ': ' + text;
                lines.push(line);
                count++;
            }
            return lines.join('\n');
        }""")
        obs.aria_snapshot = aria or ""
    except Exception as e:
        print(f"    [observer] ARIA snapshot error: {e}")

    return obs
