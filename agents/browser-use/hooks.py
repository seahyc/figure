"""
Post-action hooks: run after every executor action, feed information to the planner.
Hooks are pure observers — they don't take actions, they enrich the next observation.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field


@dataclass
class HookResults:
    """Collected results from all hooks for one step."""
    patterns_found: list[dict] = field(default_factory=list)
    suggested_action: dict | None = None
    popups_dismissed: int = 0
    stuck: bool = False
    same_state_for: int = 0
    alternative_buttons: list[str] = field(default_factory=list)

    def to_prompt(self) -> str:
        """Format hook results for planner input."""
        lines = ["HOOKS:"]
        if self.patterns_found:
            lines.append(f"  patterns_found: {json.dumps(self.patterns_found)}")
        if self.suggested_action:
            lines.append(f"  suggested_action: {json.dumps(self.suggested_action)}")
        if self.popups_dismissed > 0:
            lines.append(f"  popups_dismissed: {self.popups_dismissed}")
        if self.stuck:
            lines.append(f"  stuck: true (same page state for {self.same_state_for} steps)")
            if self.alternative_buttons:
                lines.append(f"  try_these_buttons: {self.alternative_buttons}")
        if not self.patterns_found and not self.stuck and self.popups_dismissed == 0:
            lines.append("  (no signals)")
        return "\n".join(lines)


# JS that scans for patterns across visible text, shadow DOM, hidden elements, data attributes
_PATTERN_SCAN_JS = r"""(patterns) => {
    var results = [];
    for (var p = 0; p < patterns.length; p++) {
        var pat = patterns[p];
        var re = new RegExp(pat.regex, 'g');
        var sources = pat.source === 'all'
            ? ['visible_text', 'shadow_dom', 'hidden_elements', 'data_attributes']
            : [pat.source];

        for (var s = 0; s < sources.length; s++) {
            var src = sources[s];
            var text = '';

            if (src === 'visible_text') {
                text = document.body ? document.body.innerText : '';
            } else if (src === 'shadow_dom') {
                document.querySelectorAll('*').forEach(function(el) {
                    if (el.shadowRoot) text += ' ' + (el.shadowRoot.textContent || '');
                });
            } else if (src === 'hidden_elements') {
                document.querySelectorAll('div, span, p').forEach(function(el) {
                    var cs = getComputedStyle(el);
                    if (cs.display === 'none' || cs.visibility === 'hidden' ||
                        cs.opacity === '0' || el.offsetHeight === 0) {
                        text += ' ' + el.textContent;
                    }
                });
            } else if (src === 'data_attributes') {
                document.querySelectorAll('*').forEach(function(el) {
                    for (var j = 0; j < el.attributes.length; j++) {
                        var attr = el.attributes[j];
                        if (attr.name.indexOf('data-') === 0 || attr.name === 'aria-label' ||
                            attr.name === 'title' || attr.name === 'alt') {
                            text += ' ' + attr.value;
                        }
                    }
                });
            }

            var match;
            while ((match = re.exec(text)) !== null) {
                results.push({name: pat.name, value: match[0], source: src});
            }
            re.lastIndex = 0;
        }
    }
    // Deduplicate by value
    var seen = {};
    return results.filter(function(r) {
        if (seen[r.value]) return false;
        seen[r.value] = true;
        return true;
    });
}"""


async def run_pattern_scanner(page, scan_patterns: list[dict]) -> list[dict]:
    """Scan page for configured patterns. Returns list of {name, value, source}."""
    if not scan_patterns:
        return []
    try:
        raw = await page.evaluate(_PATTERN_SCAN_JS, scan_patterns)
        if isinstance(raw, str):
            return json.loads(raw)
        return raw or []
    except Exception:
        return []


async def _check_submit_context(page) -> dict | None:
    """Check if there's a visible input + submit button for auto-suggest."""
    try:
        ctx = await page.evaluate(r"""() => {
            var input = null;
            var inputs = document.querySelectorAll('input[type="text"], input:not([type]), textarea');
            for (var i = 0; i < inputs.length; i++) {
                if (inputs[i].offsetWidth > 0) { input = inputs[i]; break; }
            }
            if (!input) return null;
            var btns = document.querySelectorAll('button');
            for (var i = 0; i < btns.length; i++) {
                var t = btns[i].textContent.trim();
                if (t === 'Submit Code' || t === 'Submit' || t === 'Go') {
                    return JSON.stringify({input_placeholder: input.placeholder || '', submit_text: t});
                }
            }
            return null;
        }""")
        if ctx:
            return json.loads(ctx) if isinstance(ctx, str) else ctx
        return None
    except Exception:
        return None


# ── Popup Dismisser ──────────────────────────────────────────────────────────

_DISMISS_JS = r"""() => {
    var dismissed = 0;
    var labels = [];
    var keywords = ['dismiss', 'close', 'decline', 'reject', 'accept',
                    'no thanks', 'not now', 'maybe later', 'got it', 'ok',
                    'acknowledge', 'i understand', 'confirm', 'agree',
                    'accept all', 'accept cookies', 'allow all'];

    // Click cookie/consent buttons
    document.querySelectorAll('button, [role="button"], a').forEach(function(el) {
        if (el.offsetWidth === 0 || el.offsetHeight === 0) return;
        var text = el.textContent.trim().toLowerCase();
        if (text.length > 50) return;
        for (var i = 0; i < keywords.length; i++) {
            if (text === keywords[i] || text.indexOf(keywords[i]) !== -1) {
                try { el.click(); dismissed++; labels.push(text); } catch(e) {}
                break;
            }
        }
    });

    // Hide overlays with high z-index
    document.querySelectorAll('div').forEach(function(el) {
        var s = getComputedStyle(el);
        if ((s.position === 'fixed' || s.position === 'absolute') &&
            parseInt(s.zIndex) >= 1000 && el.offsetHeight > 200) {
            el.style.display = 'none';
            dismissed++;
        }
    });

    return JSON.stringify({dismissed: dismissed, labels: labels.slice(0, 5)});
}"""


async def run_popup_dismisser(page) -> dict:
    """Dismiss cookie banners, consent popups, overlays. Returns {dismissed, labels}."""
    try:
        raw = await page.evaluate(_DISMISS_JS)
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {"dismissed": 0, "labels": []}


# ── Stuck Detector ───────────────────────────────────────────────────────────

class StuckDetector:
    """Tracks observation fingerprints to detect when the agent is stuck."""

    def __init__(self, threshold: int = 5):
        self.threshold = threshold
        self._history: list[str] = []

    def check(self, url: str, visible_text: str) -> dict:
        """Check if agent is stuck (same state for N steps)."""
        fingerprint = hashlib.md5((url + visible_text[:200]).encode()).hexdigest()[:12]
        self._history.append(fingerprint)

        # Count consecutive same fingerprints from the end
        same_count = 1
        for i in range(len(self._history) - 2, -1, -1):
            if self._history[i] == fingerprint:
                same_count += 1
            else:
                break

        stuck = same_count >= self.threshold
        return {"stuck": stuck, "same_state_for": same_count}

    def reset(self):
        self._history.clear()


# ── Main hook runner ─────────────────────────────────────────────────────────

async def run_hooks(
    page,
    url: str,
    visible_text: str,
    scan_patterns: list[dict] | None = None,
    dismiss_popups: bool = True,
    stuck_detector: StuckDetector | None = None,
) -> HookResults:
    """Run all post-action hooks and return collected results."""
    results = HookResults()

    # 1. Pattern scanner
    if scan_patterns:
        matches = await run_pattern_scanner(page, scan_patterns)
        results.patterns_found = matches

        # Generate suggested action if patterns found + submit context exists
        if matches:
            ctx = await _check_submit_context(page)
            if ctx:
                results.suggested_action = {
                    "action_type": "fill_form",
                    "params": {
                        "value": matches[0]["value"],
                        "submit_button": ctx["submit_text"],
                    },
                }

    # 2. Popup dismisser
    if dismiss_popups:
        popup_result = await run_popup_dismisser(page)
        results.popups_dismissed = popup_result.get("dismissed", 0)

    # 3. Stuck detector
    if stuck_detector:
        stuck_result = stuck_detector.check(url, visible_text)
        results.stuck = stuck_result["stuck"]
        results.same_state_for = stuck_result["same_state_for"]

        # When stuck, find non-decoy buttons as recovery suggestions
        if results.stuck:
            try:
                alt_btns = await page.evaluate(r"""() => {
                    var DECOY = ['next','continue','proceed','advance','move on','go forward',
                                 'keep going','click here','next step','next page','next section',
                                 'continue reading','continue journey','proceed forward',
                                 'dismiss','close','accept','decline','ok','got it'];
                    var good = [];
                    var btns = document.querySelectorAll('button, [role="button"]');
                    for (var i = 0; i < btns.length; i++) {
                        var t = btns[i].textContent.trim();
                        if (!t || t.length > 50 || btns[i].offsetWidth === 0) continue;
                        if (DECOY.indexOf(t.toLowerCase()) === -1 && t !== 'Submit Code') {
                            good.push(t);
                        }
                    }
                    // Deduplicate
                    return [...new Set(good)].slice(0, 5);
                }""")
                results.alternative_buttons = alt_btns or []
            except Exception:
                pass

    return results
