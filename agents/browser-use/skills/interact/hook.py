"""
Hook for the interact skill — Tiered Autonomy per-step orchestrator.

Tier 0: Code Scanner (JS, <100ms) — scan DOM for codes, auto-submit
Tier 1: Page Classifier (Gemini Flash, <1.5s) — classify page, get action
Tier 2: Action Dispatcher (Playwright, <1s) — execute action via handlers
        Then re-run Tier 0 to find and submit revealed codes.
Tier 3: Fall through to browser-use LLM agent (3-6s) — for truly novel pages.
"""

import asyncio
import json
import time
import re
import sys
import os

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_step_timings: list[tuple[int, str, float]] = []
_submitted_values: set = set()
_failed_codes: dict = {}  # code -> attempt count; codes that were submitted but didn't advance step
_last_step_number: int = 0
_last_page_hash: str = ""
_same_page_count: int = 0
_classifier_enabled: bool = False
_tier1_attempts: int = 0  # track consecutive Tier 1 failures for fallback
_consecutive_skip_count: int = 0  # bail-out: count consecutive skip_llm on same step
_skip_step_number: int = 0  # which step the consecutive skips are for
_bailout_steps: set = set()  # steps where Tier 1+2 has bailed out — defer to LLM permanently
_start_domain: str = ""  # domain of the starting URL, set on first step

_FALSE_POS = {'SUBMIT', 'BUTTON', 'HIDDEN', 'SCROLL', 'CANVAS', 'COOKIE',
              'ACCEPT', 'REJECT', 'OPTION', 'SELECT', 'SEARCH', 'FILTER',
              'NUMBER', 'REVEAL', 'SHADOW', 'WORKER', 'RENDER', 'CHANGE',
              'PARENT', 'RETURN', 'ESCAPE', 'CURSOR', 'SCREEN', 'CHROME',
              'PLEASE', 'VERIFY', 'ANSWER', 'RESULT', 'ABCDEF', 'DECODE',
              'FINISH'}


def get_step_timings():
    return _step_timings


def set_classifier_enabled(enabled: bool):
    global _classifier_enabled
    _classifier_enabled = enabled


async def _get_mouse(page):
    m = page.mouse
    if asyncio.iscoroutine(m):
        m = await m
    return m


async def _scroll_to_top(page):
    """Scroll window back to top after step advance — ensures new challenge is visible."""
    try:
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass


# JS: find code input (by placeholder), fill it, find Submit Code button, return coords
_FILL_SUBMIT_JS = r"""(value) => {
    var input = null;
    var allInputs = document.querySelectorAll('input[type="text"], input:not([type])');
    for (var i = 0; i < allInputs.length; i++) {
        var inp = allInputs[i];
        var ph = (inp.placeholder || '').toLowerCase();
        if (ph.indexOf('code') >= 0 || ph.indexOf('character') >= 0) {
            var style = window.getComputedStyle(inp);
            if (style.display !== 'none' && style.visibility !== 'hidden') {
                var r = inp.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) { input = inp; break; }
            }
        }
    }
    if (!input) return JSON.stringify({ok: false, reason: 'no_input'});

    input.scrollIntoView({block: 'center', behavior: 'instant'});
    input.focus();
    try {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
    } catch(e) { input.value = value; }
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));

    var submitBtn = null;
    var buttons = document.querySelectorAll('button');
    for (var i = 0; i < buttons.length; i++) {
        var btn = buttons[i];
        var text = btn.textContent.trim();
        if (/^submit\s*code$/i.test(text)) {
            var s = window.getComputedStyle(btn);
            if (s.display !== 'none' && s.visibility !== 'hidden') {
                var br = btn.getBoundingClientRect();
                if (br.width > 0 && br.height > 0) {
                    submitBtn = btn;
                    break;
                }
            }
        }
    }
    if (!submitBtn) return JSON.stringify({ok: false, reason: 'no_submit_btn', filled: true});

    // Click via React onClick (most reliable — bypasses z-index overlays)
    var rk = Object.keys(submitBtn).find(function(k) { return k.indexOf('__reactProps') === 0; });
    if (rk && submitBtn[rk] && submitBtn[rk].onClick) {
        try {
            submitBtn[rk].onClick({
                preventDefault:function(){},stopPropagation:function(){},
                target:submitBtn,currentTarget:submitBtn,
                nativeEvent:{},bubbles:true,type:'click'
            });
        } catch(e) {}
    }
    // Also do native click as backup
    submitBtn.click();

    submitBtn.scrollIntoView({block: 'center', behavior: 'instant'});
    var sr = submitBtn.getBoundingClientRect();
    return JSON.stringify({ok: true, x: Math.round(sr.x + sr.width/2), y: Math.round(sr.y + sr.height/2),
                           text: submitBtn.textContent.trim(), disabled: submitBtn.disabled});
}"""


async def _fill_and_submit(page, code: str) -> bool:
    """Fill input with code and click submit via Playwright trusted click."""
    try:
        raw = await page.evaluate(_FILL_SUBMIT_JS, code)
        result = json.loads(raw) if isinstance(raw, str) else raw
        if not result or not isinstance(result, dict) or not result.get("ok"):
            reason = result.get("reason", "unknown") if isinstance(result, dict) else "no_result"
            print(f"    [hook] fill_submit: {reason}")
            return False

        # If button is disabled, wait for React re-render
        if result.get("disabled"):
            await asyncio.sleep(0.3)
            raw2 = await page.evaluate(r"""() => {
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    if (/^submit\s*code$/i.test(buttons[i].textContent.trim())) {
                        buttons[i].scrollIntoView({block: 'center', behavior: 'instant'});
                        var r = buttons[i].getBoundingClientRect();
                        return JSON.stringify({disabled: buttons[i].disabled,
                            x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
                    }
                }
                return null;
            }""")
            if raw2:
                r2 = json.loads(raw2) if isinstance(raw2, str) else raw2
                if r2:
                    result["x"] = r2["x"]
                    result["y"] = r2["y"]

        print(f"    [hook] Filled '{code}', clicking Submit Code at ({result['x']},{result['y']})")
        mouse = await _get_mouse(page)
        await mouse.click(result["x"], result["y"])
        _submitted_values.add(code)
        return True
    except Exception as e:
        print(f"    [hook] fill_submit error: {e}")
    return False


async def _detect_step(page) -> int:
    """Get current challenge step number."""
    try:
        text = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 3000) : ''")
        m = re.search(r'[Ss]tep\s+(\d+)', text)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 0


async def _detect_total_steps(page) -> int:
    """Get total number of challenge steps (e.g., 30 from 'Step X of 30')."""
    try:
        text = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 3000) : ''")
        m = re.search(r'[Ss]tep\s+\d+\s+of\s+(\d+)', text)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 30  # default


async def _try_navigate_finish(page) -> bool:
    """Navigate to /finish if we're on the last step.

    The challenge's markChallengeComplete returns codes.get(stepNum+1).
    For the last step (30), codes.get(31) doesn't exist, so no code can
    ever be returned or validated. The /finish page is a static congratulations
    with no validation. We just need to navigate there.

    IMPORTANT: This is a React SPA with client-side routing (React Router).
    Using window.location.href causes a full server reload → 404 on Netlify.
    We must trigger React Router's internal navigation.
    """
    try:
        current = await _detect_step(page)
        total = await _detect_total_steps(page)
        if current > 0 and current >= total:
            print(f"    [hook] Last step ({current}/{total}): navigating to /finish")
            # React Router v6 with BrowserRouter listens to popstate events.
            # pushState changes the URL, popstate triggers React Router to re-render.
            # The submit handler in Kv: Cv(c,ne) ? (S(true), setTimeout(() => c < Yr ? o(`/step${c+1}`) : o("/finish")))
            # where o = useNavigate() from React Router v6.
            # We need to call o("/finish") from within React's context.
            #
            # Strategy: Walk the fiber tree from Submit Code button to find the Kv component.
            # Kv uses useNavigate() — the navigate function is stored in its hooks (memoizedState chain).
            # Call it with "/finish".
            result = await page.evaluate(r"""() => {
                var debug = [];
                // Find Submit Code button to start fiber walk
                var submitBtn = null;
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    if (/Submit Code/i.test(btns[i].textContent)) { submitBtn = btns[i]; break; }
                }
                if (!submitBtn) {
                    debug.push('no_submit_btn');
                    // Try any visible button
                    for (var i = 0; i < btns.length; i++) {
                        if (btns[i].offsetParent !== null) { submitBtn = btns[i]; break; }
                    }
                }
                if (!submitBtn) return JSON.stringify({ok: false, debug: debug});

                var sfk = Object.keys(submitBtn).find(function(k) { return k.indexOf('__reactFiber') === 0; });
                if (!sfk) return JSON.stringify({ok: false, debug: ['no_fiber']});

                // Walk UP to find navigate function (from useNavigate hook)
                // useNavigate returns a function stored as memoizedState in a hook
                // We look for functions that when called with '/finish' would navigate
                var navigated = false;
                for (var f = submitBtn[sfk]; f; f = f.return) {
                    if (!f.memoizedState) continue;
                    var ms = f.memoizedState;
                    var hookIdx = 0;
                    while (ms && hookIdx < 30) {
                        hookIdx++;
                        if (typeof ms.memoizedState === 'function') {
                            var fnStr = String(ms.memoizedState).substring(0, 200);
                            debug.push('fn_hook_' + hookIdx + ':' + fnStr.substring(0, 60));
                            try {
                                ms.memoizedState('/finish');
                                navigated = true;
                                debug.push('called_navigate');
                                break;
                            } catch(e) {
                                debug.push('err:' + e.message);
                            }
                        }
                        ms = ms.next;
                    }
                    if (navigated) break;
                }
                return JSON.stringify({ok: navigated, debug: debug});
            }""")
            nav_data = json.loads(result) if isinstance(result, str) else result
            print(f"    [hook] /finish navigation: {nav_data}")
            await asyncio.sleep(2.0)

            # Verify we reached the finish page
            finish_text = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 500) : ''")
            if "YOU ARE HERE" in finish_text or "Congratulations" in finish_text or "completed" in finish_text.lower():
                print(f"    [hook] FINISH PAGE REACHED! Challenge complete!")
                return True
            print(f"    [hook] /finish page text: '{finish_text[:100]}'")

            # Fallback: pushState + popstate (might work depending on React Router version)
            try:
                await page.evaluate(r"""() => {
                    window.history.pushState({}, '', '/finish');
                    window.dispatchEvent(new PopStateEvent('popstate', {state: {}}));
                }""")
                await asyncio.sleep(2.0)
                finish_text = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 500) : ''")
                if "YOU ARE HERE" in finish_text or "Congratulations" in finish_text or "completed" in finish_text.lower():
                    print(f"    [hook] FINISH PAGE REACHED (pushState fallback)! Challenge complete!")
                    return True
            except Exception:
                pass

            print(f"    [hook] Navigate to /finish failed")
        return False
    except Exception as e:
        print(f"    [hook] navigate_finish error: {e}")
        return False


_INLINE_SCAN_JS = r"""() => {
    var codeRE = /[A-HJ-NP-Z2-9]{6}/g;
    var FALSE_POS = new Set(['SUBMIT','BUTTON','HIDDEN','SCROLL','CANVAS','COOKIE','ACCEPT','REJECT',
        'OPTION','SELECT','SEARCH','FILTER','NUMBER','REVEAL','SHADOW','WORKER','RENDER','CHANGE',
        'PARENT','RETURN','ESCAPE','CURSOR','SCREEN','CHROME','PLEASE','VERIFY','ANSWER','RESULT',
        'ABCDEF','DECODE']);
    var codes = [];
    var seen = new Set();
    function add(t, src) {
        if (!t) return;
        var m = String(t).match(codeRE);
        if (!m) return;
        for (var i = 0; i < m.length; i++) {
            if (seen.has(m[i]) || FALSE_POS.has(m[i]) || !/[A-Z]/.test(m[i])) continue;
            seen.add(m[i]);
            codes.push({text: m[i], source: src});
        }
    }
    var allEls = document.querySelectorAll('*');
    for (var i = 0; i < allEls.length; i++) {
        var attrs = allEls[i].attributes;
        for (var j = 0; j < attrs.length; j++) {
            if (attrs[j].name.indexOf('data-') === 0) add(attrs[j].value, 'data-attr');
        }
    }
    var ariaEls = document.querySelectorAll('[aria-label],[aria-description]');
    for (var i = 0; i < ariaEls.length; i++) {
        add(ariaEls[i].getAttribute('aria-label'), 'aria');
        add(ariaEls[i].getAttribute('aria-description'), 'aria');
    }
    var metas = document.querySelectorAll('meta[content]');
    for (var i = 0; i < metas.length; i++) add(metas[i].content, 'meta');
    try {
        var w = document.createTreeWalker(document.body||document, NodeFilter.SHOW_COMMENT);
        var n; while(n=w.nextNode()) add(n.textContent, 'comment');
    } catch(e){}
    var hidden = document.querySelectorAll('[style*="display:none"],[style*="display: none"],[hidden],.hidden,[style*="opacity:0"],[style*="opacity: 0"]');
    for (var i = 0; i < hidden.length; i++) add(hidden[i].textContent, 'hidden-el');
    var styled = document.querySelectorAll('body *');
    for (var i = 0; i < styled.length && i < 300; i++) {
        try {
            var bf = window.getComputedStyle(styled[i], '::before').content;
            if (bf && bf !== 'none' && bf !== 'normal') add(bf.replace(/['"]/g,''), 'css');
            var af = window.getComputedStyle(styled[i], '::after').content;
            if (af && af !== 'none' && af !== 'normal') add(af.replace(/['"]/g,''), 'css');
        } catch(e){}
    }
    for (var i = 0; i < allEls.length; i++) {
        if (allEls[i].shadowRoot) {
            add(allEls[i].shadowRoot.textContent, 'shadow');
        }
    }
    add(document.body ? document.body.innerText : '', 'visible');
    // pageInfo
    var bodyText = document.body ? document.body.innerText.substring(0, 3000) : '';
    var btns = document.querySelectorAll('button');
    var btnTexts = new Set();
    for (var i = 0; i < btns.length && btnTexts.size < 15; i++) {
        var t = btns[i].textContent.trim();
        if (t && btns[i].offsetWidth > 0 && !btns[i].disabled && t.length < 30) btnTexts.add(t);
    }
    var hasShadow = false;
    for (var i = 0; i < allEls.length; i++) if (allEls[i].shadowRoot) { hasShadow = true; break; }
    // Structural detection signals
    var iframes = document.querySelectorAll('iframe');
    var hasIframes = iframes.length > 0;
    var iframeDepth = 0;
    if (hasIframes) { try { var f = iframes[0]; var d = 1; while (f.contentDocument && f.contentDocument.querySelector('iframe')) { f = f.contentDocument.querySelector('iframe'); d++; if (d > 5) break; } iframeDepth = d; } catch(e) { iframeDepth = 1; } }
    var tabBtns = document.querySelectorAll('button[data-tab]');
    var tabByText = 0; btns.forEach(function(b) { if (/^Tab\s+\d+$/i.test(b.textContent.trim())) tabByText++; });
    var hasTabButtons = tabBtns.length > 0 || tabByText >= 2;
    var tabCount = Math.max(tabBtns.length, tabByText);
    var hasStatusIndicator = !!(bodyText.match(/[●○]/) || bodyText.match(/connected|disconnected/i));
    var preEls = document.querySelectorAll('pre, code'); var hasTerminalOutput = false;
    for (var i = 0; i < preEls.length; i++) { if (preEls[i].textContent.trim().length > 10) { hasTerminalOutput = true; break; } }
    var hasProgressSteps = !!(bodyText.match(/[○✓]/) && bodyText.match(/click|hover|type|scroll/i)) || !!bodyText.match(/\d+\/\d+/);
    var hasTimerOrCountdown = !!(bodyText.match(/wait\s+\d+\s*s/i) || bodyText.match(/\d+:\d{2}/) || bodyText.match(/countdown/i));
    var hasMathExpression = !!(bodyText.match(/\d+\s*[\+\-\*x×÷\/]\s*\d+\s*=\s*\?/));
    var hasNestedLayers = (function() { var divs = document.querySelectorAll('div[onclick], div[class*="cursor-pointer"]'); for (var i = 0; i < divs.length; i++) { var d = divs[i], depth = 0, p = d.parentElement; while (p && p !== document.body) { if (p.onclick || (p.className && /cursor-pointer/.test(p.className))) depth++; p = p.parentElement; } if (depth >= 2) return true; } return false; })();
    var hasMultiStepFlow = !!(bodyText.match(/1\.\s+\w+.*2\.\s+\w+/s) || (bodyText.match(/Register/i) && bodyText.match(/Retrieve/i)) || (bodyText.match(/Step\s+\d/i)));
    var overlayCount = 0; var fixedEls = document.querySelectorAll('div'); for (var i = 0; i < fixedEls.length; i++) { var cs = window.getComputedStyle(fixedEls[i]); if ((cs.position === 'fixed' || cs.position === 'absolute') && parseFloat(cs.zIndex) > 50 && fixedEls[i].offsetWidth > 200 && fixedEls[i].offsetHeight > 100) overlayCount++; }
    var hasRadioButtons = document.querySelectorAll('input[type="radio"]').length > 0;
    var interactiveEls = document.querySelectorAll('button, a[href], input, select, textarea, [onclick], [draggable="true"], [role="button"]');
    var interactiveElementCount = 0; for (var i = 0; i < interactiveEls.length; i++) { var t = (interactiveEls[i].textContent || '').trim(); if (!/^(Next|Continue|Proceed|Advance|Go Forward|Move Forward|Keep Going)$/i.test(t)) interactiveElementCount++; }
    return JSON.stringify({
        ok: codes.length > 0,
        codes: codes,
        clickTargets: [],
        hoverCoords: [],
        keySequence: [],
        dragPairs: [],
        canvasStrokes: [],
        scrollTarget: 0,
        typeActions: [],
        actions: [],
        detected: ['inline-scan'],
        pageInfo: {
            url: window.location.href,
            title: document.title,
            hasCanvas: !!document.querySelector('canvas'),
            hasDraggables: !!document.querySelector('[draggable="true"]'),
            hasShadowRoots: hasShadow,
            inputCount: document.querySelectorAll('input').length,
            buttonCount: btns.length,
            hasIframes: hasIframes,
            iframeDepth: iframeDepth,
            hasTabButtons: hasTabButtons,
            tabCount: tabCount,
            hasStatusIndicator: hasStatusIndicator,
            hasTerminalOutput: hasTerminalOutput,
            hasProgressSteps: hasProgressSteps,
            hasTimerOrCountdown: hasTimerOrCountdown,
            hasMathExpression: hasMathExpression,
            hasNestedLayers: hasNestedLayers,
            hasMultiStepFlow: hasMultiStepFlow,
            overlayCount: overlayCount,
            hasRadioButtons: hasRadioButtons,
            interactiveElementCount: interactiveElementCount,
            buttonTexts: Array.from(btnTexts),
            visibleTextPreview: (function() {
                var challengeText = '';
                document.querySelectorAll('div').forEach(function(el) {
                    var cls = el.className || '';
                    if (typeof cls !== 'string') return;
                    if (/z-\[100\d\d\]/.test(cls) || /relative/.test(cls) && /z-/.test(cls)) {
                        var t = el.innerText || '';
                        if (t.length > 20 && t.length < 1000 && !/^Section \d+/.test(t.substring(0, 50))) {
                            challengeText += t.substring(0, 300) + ' ';
                        }
                    }
                });
                if (challengeText.length > 50) return challengeText.substring(0, 600);
                return bodyText.substring(0, 500);
            })()
        }
    });
}"""


async def _run_code_scan(page) -> dict | None:
    """Tier 0: Run JS code scanner and return results.

    Tries the injected interact skill first, falls back to inline scan.
    """
    try:
        raw = await asyncio.wait_for(
            page.evaluate(
                "() => window.__skills && window.__skills.interact ? window.__skills.interact({probeOnly: false}) : null"
            ),
            timeout=5.0
        )
        if raw:
            result = json.loads(raw) if isinstance(raw, str) else raw
            if result and result.get("codes"):
                return result
            # interact skill ran but found no codes — try inline scan for codes
            # but keep interact's pageInfo and action data
            raw2 = await asyncio.wait_for(page.evaluate(_INLINE_SCAN_JS), timeout=3.0)
            if raw2:
                inline = json.loads(raw2) if isinstance(raw2, str) else raw2
                if inline and inline.get("codes"):
                    result["codes"] = inline["codes"]
                    result["detected"] = result.get("detected", []) + ["inline-fallback"]
            return result
    except asyncio.TimeoutError:
        print(f"    [hook] interact skill TIMEOUT (5s)")
    except Exception as e:
        print(f"    [hook] interact skill error: {e}")

    # Skill not available or timed out — use inline scan only
    try:
        raw = await asyncio.wait_for(page.evaluate(_INLINE_SCAN_JS), timeout=3.0)
        if raw:
            return json.loads(raw) if isinstance(raw, str) else raw
    except asyncio.TimeoutError:
        print(f"    [hook] inline scan TIMEOUT (3s)")
    except Exception as e:
        print(f"    [hook] inline scan error: {e}")
    return None


def _pick_best_code(codes: list[dict]) -> dict | None:
    """Pick the best code from scan results, filtering false positives."""
    if not codes:
        return None
    for c in codes:
        text = c.get("text", "")
        if text in _submitted_values:
            continue
        if text in _FALSE_POS:
            continue
        if not re.search(r'[A-Z]', text):
            continue
        if _failed_codes.get(text, 0) >= 2:
            continue
        return c
    # Log all filtered codes for debugging
    if codes:
        filtered = [(c.get("text",""), c.get("source","")) for c in codes]
        print(f"    [hook] All codes filtered: {filtered}, submitted={_submitted_values}")
    return None


async def _try_submit(page, codes: list[dict]) -> tuple[bool, int]:
    """Try to find and submit a code. Returns (success, new_step)."""
    global _failed_codes
    best = _pick_best_code(codes)
    if not best:
        return False, 0
    code = best["text"]
    # Skip codes that have been tried too many times without advancing
    if _failed_codes.get(code, 0) >= 2:
        return False, 0
    print(f"    [hook] Code: '{code}' ({best['source']})")
    if await _fill_and_submit(page, code):
        # Wait for page transition with retry
        for wait_ms in [1000, 1000, 800]:
            await asyncio.sleep(wait_ms / 1000.0)
            new_step = await _detect_step(page)
            if new_step > _last_step_number:
                # Step advanced — clear caches but keep current code as submitted
                # (prevents re-submitting the same code if old challenge page is still visible)
                _submitted_values.clear()
                _submitted_values.add(code)
                _failed_codes.clear()
                _bailout_steps.clear()
                # Aggressively clear old challenge DOM to prevent stale detection.
                # The SPA sometimes retains old challenge elements after step advance.
                await asyncio.sleep(0.5)
                try:
                    await page.evaluate("""(oldCode) => {
                        var CODE_RE = /^[A-HJ-NP-Z2-9]{6}$/;
                        var all = document.querySelectorAll('*');
                        for (var i = 0; i < all.length; i++) {
                            var el = all[i];
                            var t = el.textContent.trim();
                            // Hide elements with the old code
                            if (t === oldCode && el.children.length === 0) {
                                el.style.display = 'none';
                                el.textContent = '';
                            }
                            // Also hide "Puzzle solved" / "Code revealed" stale text
                            if (el.children.length === 0 && el.tagName !== 'SCRIPT' &&
                                (/Puzzle solved/i.test(t) || /Code revealed/i.test(t))) {
                                el.style.display = 'none';
                                el.textContent = '';
                            }
                        }
                        window.scrollTo(0, 0);
                    }""", code)
                except Exception:
                    pass
                await asyncio.sleep(0.5)
                return True, new_step
        # Step didn't advance — track as failed, don't retry more than twice
        _failed_codes[code] = _failed_codes.get(code, 0) + 1
        print(f"    [hook] Code '{code}' didn't advance step (attempt {_failed_codes[code]})")
        return True, 0
    return False, 0


async def _onComplete_bypass(page, challenge_type: str = "unknown") -> list[dict]:
    """Generic onComplete bypass for ANY challenge type.

    Walks the React fiber tree from visible buttons, finds onComplete callbacks,
    and calls them with proper metadata ({type, timestamp, data} all required).
    Returns codes found from onComplete return values.

    For the LAST step, onComplete returns null (no next code exists), but the
    challenge IS marked complete. In that case, we navigate to /finish.
    """
    try:
        bypass_result = await page.evaluate(r"""(challengeType) => {
            var re = /^[A-HJ-NP-Z2-9]{6}$/;
            var codes = [];
            var tried = [];
            var called = false;
            // Find ANY visible button to start fiber walk
            var buttons = document.querySelectorAll('button');
            var startEls = [];
            for (var i = 0; i < buttons.length; i++) {
                if (buttons[i].offsetParent !== null) startEls.push(buttons[i]);
            }
            // Also try the #root element
            var root = document.getElementById('root');
            if (root) startEls.push(root);
            var seen = {};
            for (var si = 0; si < Math.min(startEls.length, 10); si++) {
                var fk = Object.keys(startEls[si]).find(function(k) { return k.indexOf('__reactFiber') === 0; });
                if (!fk) continue;
                for (var f = startEls[si][fk]; f; f = f.return) {
                    if (!f.memoizedProps || typeof f.memoizedProps.onComplete !== 'function') continue;
                    // Deduplicate by function reference
                    var fnStr = String(f.memoizedProps.onComplete).substring(0, 50);
                    if (seen[fnStr]) continue;
                    seen[fnStr] = true;
                    var ret = null;
                    try {
                        ret = f.memoizedProps.onComplete({
                            type: challengeType,
                            timestamp: Date.now(),
                            data: {method: challengeType}
                        });
                        called = true;
                    } catch(e) { tried.push('err:' + e.message); }
                    tried.push(challengeType + ':' + (ret || 'void'));
                    if (typeof ret === 'string' && re.test(ret) && /[A-HJ-NP-Z]/.test(ret)) {
                        codes.push(ret);
                    }
                }
                if (codes.length > 0) break;
            }
            return JSON.stringify({codes: codes, tried: tried, called: called});
        }""", challenge_type)
        data = json.loads(bypass_result) if isinstance(bypass_result, str) else bypass_result
        codes = data.get('codes', [])
        called = data.get('called', False)
        if codes:
            print(f"    [hook] onComplete bypass ({challenge_type}): found codes {codes}")
            return [{"text": c, "source": f"onComplete-{challenge_type}"} for c in codes]
        # onComplete was called but returned null — likely the last step
        # (markChallengeComplete returns codes.get(stepNum+1) which doesn't exist for step 30)
        if called and not codes:
            print(f"    [hook] onComplete bypass ({challenge_type}): called but no code returned (last step?)")
            finished = await _try_navigate_finish(page)
            if finished:
                return [{"text": "FINISH", "source": "navigate-finish"}]
        return []
    except Exception as e:
        print(f"    [hook] onComplete bypass error: {e}")
        return []


async def _force_reveal_codes(page) -> list[dict]:
    """Force-reveal any hidden elements that contain 6-char challenge codes.

    Challenges hide the code div (display:none) until onComplete fires.
    React re-renders may prevent native addEventListener from triggering,
    so we force-reveal hidden codes after handlers act.
    """
    try:
        revealed_codes = await page.evaluate(r"""() => {
            var found = [];
            var CODE_RE = /^[A-HJ-NP-Z2-9]{6}$/;
            function checkAndReveal(el) {
                var t = el.textContent.trim();
                if (CODE_RE.test(t) && /[A-Z]/.test(t)) {
                    el.style.display = 'block';
                    if (found.indexOf(t) === -1) found.push(t);
                    return true;
                }
                // Check children
                var children = el.querySelectorAll('span, div, p');
                for (var j = 0; j < children.length; j++) {
                    var ct = children[j].textContent.trim();
                    if (CODE_RE.test(ct) && /[A-Z]/.test(ct)) {
                        el.style.display = 'block';
                        if (found.indexOf(ct) === -1) found.push(ct);
                        return true;
                    }
                }
                return false;
            }
            // Strategy 1: style attribute selector
            var els = document.querySelectorAll('div[style*="display: none"], div[style*="display:none"], span[style*="display: none"], span[style*="display:none"]');
            for (var i = 0; i < els.length; i++) checkAndReveal(els[i]);
            // Strategy 2: check computed style for ALL divs/spans (catches React inline styles)
            if (found.length === 0) {
                var allEls = document.querySelectorAll('div, span, p');
                for (var i = 0; i < allEls.length; i++) {
                    var cs = window.getComputedStyle(allEls[i]);
                    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') {
                        checkAndReveal(allEls[i]);
                    }
                }
            }
            return JSON.stringify(found);
        }""")
        codes = json.loads(revealed_codes) if isinstance(revealed_codes, str) else (revealed_codes or [])
        if codes:
            print(f"    [hook] Force-revealed {len(codes)} hidden codes: {codes}")
            return [{"text": c, "source": "force-reveal"} for c in codes]
        # If no codes found by DOM reveal, try onComplete bypass as last resort
        return await _onComplete_bypass(page, "unknown")
    except Exception:
        return await _onComplete_bypass(page, "unknown")


async def _fiber_scan_codes(page) -> list[dict]:
    """Deep scan React fiber tree for 6-char challenge codes in props, state, and nested objects."""
    try:
        fiber_codes_raw = await page.evaluate(r"""() => {
            var found = [];
            var re = /^[A-HJ-NP-Z2-9]{6}$/;
            var seen = {};
            function addCode(c) { if (!seen[c]) { seen[c] = true; found.push(c); } }
            function checkStr(s) { if (typeof s === 'string' && re.test(s) && /[A-HJ-NP-Z]/.test(s)) addCode(s); }
            function checkObj(o, depth) {
                if (!o || typeof o !== 'object' || depth > 2) return;
                try {
                    var keys = Object.keys(o);
                    for (var k = 0; k < keys.length; k++) {
                        var v = o[keys[k]];
                        if (typeof v === 'string') checkStr(v);
                        else if (typeof v === 'object' && v !== null) checkObj(v, depth + 1);
                    }
                } catch(e) {}
            }
            var rootEls = document.querySelectorAll('#root, [id*="app"], body');
            for (var ri = 0; ri < rootEls.length; ri++) {
                var fk = Object.keys(rootEls[ri]).find(function(k) { return k.indexOf('__reactFiber') === 0; });
                if (!fk) continue;
                var queue = [rootEls[ri][fk]];
                while (queue.length > 0) {
                    var node = queue.shift();
                    if (!node) continue;
                    // Check props (2 levels deep)
                    if (node.memoizedProps) checkObj(node.memoizedProps, 0);
                    // Check all hook states
                    var st = node.memoizedState;
                    var si = 0;
                    while (st && si < 20) {
                        si++;
                        if (st.memoizedState != null) {
                            if (typeof st.memoizedState === 'string') checkStr(st.memoizedState);
                            else if (typeof st.memoizedState === 'object') checkObj(st.memoizedState, 0);
                        }
                        if (st.queue && st.queue.lastRenderedState != null) {
                            if (typeof st.queue.lastRenderedState === 'string') checkStr(st.queue.lastRenderedState);
                            else if (typeof st.queue.lastRenderedState === 'object') checkObj(st.queue.lastRenderedState, 0);
                        }
                        st = st.next;
                    }
                    if (node.child) queue.push(node.child);
                    if (node.sibling) queue.push(node.sibling);
                }
            }
            return JSON.stringify(found);
        }""")
        fiber_list = json.loads(fiber_codes_raw) if isinstance(fiber_codes_raw, str) else []
        if fiber_list:
            print(f"    [hook] Fiber scan found codes: {fiber_list}")
            return [{"text": c, "source": "react-fiber"} for c in fiber_list]
    except Exception:
        pass
    return []


async def _do_clicks(page, targets: list[dict]) -> int:
    """Execute click targets from JS skill."""
    if not targets:
        return 0
    mouse = await _get_mouse(page)
    clicked = 0
    for t in targets:
        x, y = t.get("x", 0), t.get("y", 0)
        if y < 0 or y > 900 or x < 0 or x > 1400:
            continue
        label = t.get("label", "")
        wait = t.get("waitAfter", 300) / 1000.0
        try:
            await mouse.click(x, y)
            print(f"    [hook] Clicked '{label}' at ({x},{y})")
            clicked += 1
            if wait > 0:
                await asyncio.sleep(wait)
        except Exception:
            pass
    return clicked


async def _do_hover(page, coords: list[dict]) -> bool:
    """Hover over coordinates for code reveal."""
    if not coords:
        return False
    mouse = await _get_mouse(page)
    for c in coords[:6]:
        x, y = c.get("x", 0), c.get("y", 0)
        dur = c.get("durationMs", 1500) / 1000.0
        print(f"    [hook] Hovering at ({x},{y}) for {dur}s")
        await mouse.move(x, y)
        await asyncio.sleep(dur)
    return True


async def _do_keyboard(page, keys: list[str]) -> bool:
    """Execute keyboard sequence."""
    if not keys:
        return False
    k = page.keyboard
    if asyncio.iscoroutine(k):
        k = await k
    for key in keys:
        key = key.strip()
        if not key:
            continue
        if '+' in key:
            parts = key.split('+')
            modifiers = []
            main_key = parts[-1].strip()
            for mod in parts[:-1]:
                mod = mod.strip().lower()
                if mod in ('ctrl', 'control'):
                    modifiers.append('Control')
                elif mod == 'alt':
                    modifiers.append('Alt')
                elif mod == 'shift':
                    modifiers.append('Shift')
                elif mod in ('meta', 'cmd'):
                    modifiers.append('Meta')
            for mod in modifiers:
                await k.down(mod)
            await k.press(main_key)
            for mod in reversed(modifiers):
                await k.up(mod)
        else:
            await k.press(key)
        await asyncio.sleep(0.2)
        print(f"    [hook] Key: {key}")
    return True


async def _do_canvas(page, strokes: list[dict]) -> bool:
    """Draw strokes on canvas via Playwright mouse."""
    if not strokes:
        return False
    mouse = await _get_mouse(page)
    for stroke in strokes:
        sx, sy = stroke["startX"], stroke["startY"]
        ex, ey = stroke["endX"], stroke["endY"]
        await mouse.move(sx, sy)
        await mouse.down()
        steps = 10
        for i in range(1, steps + 1):
            mx = sx + (ex - sx) * i / steps
            my = sy + (ey - sy) * i / steps
            await mouse.move(mx, my)
            await asyncio.sleep(0.02)
        await mouse.up()
        await asyncio.sleep(0.1)
    print(f"    [hook] Drew {len(strokes)} canvas strokes")
    return True


async def _do_drag(page, pairs: list[dict]) -> bool:
    """Execute drag and drop via Playwright mouse."""
    if not pairs:
        return False
    still = await page.evaluate("() => document.querySelectorAll('[draggable=\"true\"]').length")
    if still == 0:
        print(f"    [hook] Drag-drop: JS dispatch already worked")
        return True

    mouse = await _get_mouse(page)
    for pair in pairs:
        sx, sy = pair["srcX"], pair["srcY"]
        tx, ty = pair["tgtX"], pair["tgtY"]
        print(f"    [hook] Dragging ({sx},{sy}) -> ({tx},{ty})")
        await mouse.move(sx, sy)
        await asyncio.sleep(0.15)
        await mouse.down()
        await asyncio.sleep(0.15)
        steps = 20
        for i in range(1, steps + 1):
            mx = sx + (tx - sx) * i / steps
            my = sy + (ty - sy) * i / steps
            await mouse.move(mx, my)
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.1)
        await mouse.up()
        await asyncio.sleep(0.3)
    return True


async def _execute_js_actions(page, ir: dict) -> bool:
    """Execute any Playwright actions returned by JS code scanner."""
    did_something = False

    if ir.get("hoverCoords"):
        await _do_hover(page, ir["hoverCoords"])
        did_something = True

    if ir.get("keySequence"):
        await _do_keyboard(page, ir["keySequence"])
        did_something = True

    if ir.get("canvasStrokes"):
        await _do_canvas(page, ir["canvasStrokes"])
        did_something = True

    if ir.get("dragPairs"):
        await _do_drag(page, ir["dragPairs"])
        did_something = True

    if ir.get("clickTargets"):
        await _do_clicks(page, ir["clickTargets"])
        did_something = True

    return did_something


async def _run_classifier(page_info: dict) -> dict | None:
    """Tier 1: Run the Gemini Flash classifier on page features (with 429 retry)."""
    try:
        from classifier import classify_page
        for retry in range(3):
            result = await classify_page(page_info)
            # Check for rate limit in the result
            if result and result.get("action") == "unknown" and "429" in result.get("reasoning", ""):
                wait = 2 ** retry  # 1s, 2s, 4s
                print(f"    [hook] classifier 429 retry {retry+1}, waiting {wait}s")
                await asyncio.sleep(wait)
                continue
            return result
        return result  # return last result even if still 429
    except Exception as e:
        print(f"    [hook] classifier error: {e}")
        return None


async def _run_action_handler(page, action: str, params: dict) -> dict:
    """Tier 2: Dispatch action to the appropriate handler."""
    try:
        from action_handlers import dispatch_action
        result = await dispatch_action(page, action, params)
        return result
    except Exception as e:
        print(f"    [hook] action_handler error: {e}")
        return {"ok": False, "detail": f"handler error: {e}"}


async def _chain_submit(page, max_chains: int = 5) -> bool:
    """After a successful submit, chain through subsequent steps that auto-resolve."""
    global _last_step_number
    for chain in range(max_chains):
        await asyncio.sleep(1.5)  # Wait for SPA transition to new challenge
        ir = await _run_code_scan(page)
        if not ir:
            break

        codes = ir.get("codes", [])
        # Execute any JS-detected actions
        did_js = await _execute_js_actions(page, ir)
        if did_js:
            await asyncio.sleep(0.8)
            ir2 = await _run_code_scan(page)
            if ir2:
                codes = ir2.get("codes", [])

        submitted, new_step = await _try_submit(page, codes)
        if submitted and new_step > _last_step_number:
            _last_step_number = new_step
            await _scroll_to_top(page)
            print(f"    [hook] Chain {chain + 1}: -> step {new_step}")
            continue
        else:
            # No code found in chain — try classifier for this step too
            if _classifier_enabled and ir:
                page_info = ir.get("pageInfo", {})
                if page_info:
                    # Add ARIA snapshot for better classification
                    try:
                        aria_snapshot = await page.accessibility.snapshot()
                        if aria_snapshot:
                            page_info["ariaSnapshot"] = _compact_aria(aria_snapshot)
                    except Exception:
                        pass
                    classification = await _run_classifier(page_info)
                    if classification and classification.get("action") != "unknown":
                        action = classification["action"]
                        action_params = classification.get("params", {})
                        # If classifier says "wait" but there's a puzzle, override to fill_form
                        chain_vis = page_info.get("visibleTextPreview", "")
                        if action == "wait" and "= ?" in chain_vis:
                            # Extract expression from page text
                            import re as _re
                            expr_match = _re.search(r'(\d+\s*[\+\-\*x×÷\/]\s*\d+)\s*=\s*\?', chain_vis)
                            if expr_match:
                                expr = expr_match.group(1).replace("×", "*").replace("÷", "/").replace("x", "*")
                                try:
                                    answer = str(int(eval(expr)))
                                    action = "fill_form"
                                    action_params = {"expression": expr_match.group(0), "answer": answer}
                                    print(f"    [hook] Chain {chain + 1} override: wait->fill_form ({expr}={answer})")
                                except Exception:
                                    pass
                        print(f"    [hook] Chain {chain + 1} classifier: {action} ({classification.get('reasoning', '')[:60]})")
                        handler_result = await _run_action_handler(page, action, action_params)
                        print(f"    [hook] Chain {chain + 1} handler: {handler_result.get('detail', '')[:80]}")
                        if handler_result.get("ok"):
                            # Check if handler found codes directly
                            handler_codes = handler_result.get("codes", handler_result.get("codes_found", []))
                            if handler_codes:
                                codes_as_dicts = []
                                for c in handler_codes:
                                    if isinstance(c, dict):
                                        txt = c.get("code", c.get("text", ""))
                                        codes_as_dicts.append({"text": txt, "source": "chain-handler"})
                                    else:
                                        codes_as_dicts.append({"text": str(c), "source": "chain-handler"})
                                sub_h, ns_h = await _try_submit(page, codes_as_dicts)
                                if sub_h and ns_h > _last_step_number:
                                    _last_step_number = ns_h
                                    await _scroll_to_top(page)
                                    print(f"    [hook] Chain {chain + 1}: -> step {ns_h} (handler-codes)")
                                    # Handler-extracted codes (e.g. puzzle fiber) may leave
                                    # stale content in DOM. Break chain to let main flow
                                    # get fresh state on next on_step_start.
                                    break
                            await asyncio.sleep(0.8)
                            ir3 = await _run_code_scan(page)
                            if ir3:
                                codes3 = ir3.get("codes", [])
                                # Force-reveal if needed
                                if not _pick_best_code(codes3):
                                    codes3 = await _force_reveal_codes(page)
                                sub3, ns3 = await _try_submit(page, codes3)
                                if sub3 and ns3 > _last_step_number:
                                    _last_step_number = ns3
                                    await _scroll_to_top(page)
                                    print(f"    [hook] Chain {chain + 1}: -> step {ns3} (post-classify)")
                                    continue
            break
    return True


async def on_step_start(agent):
    """Pre-step hook: Tiered autonomy — scan, classify, act, submit.

    Returns {"skip_llm": True} when the hook has handled the step.
    """
    global _last_step_number, _last_page_hash, _same_page_count, _tier1_attempts, _consecutive_skip_count, _skip_step_number, _start_domain

    t0 = time.time()
    step_n = getattr(agent.state, 'n_steps', 0)
    print(f"    [hook] === on_step_start (step_n={step_n}, last_step={_last_step_number}) ===")

    try:
        page = await agent.browser_session.get_current_page()
    except Exception as e:
        print(f"    [hook] get_current_page error: {e}")
        return None

    # --- 0. URL guard: redirect back if LLM navigated to a completely different domain ---
    # Set starting domain on first step
    try:
        current_url = await page.evaluate("() => window.location.href")
        if not _start_domain and current_url:
            from urllib.parse import urlparse
            _start_domain = urlparse(current_url).netloc
            print(f"    [hook] Start domain: {_start_domain}")
        if current_url and _start_domain:
            from urllib.parse import urlparse
            current_domain = urlparse(current_url).netloc
            if current_domain and current_domain != _start_domain and "duckduckgo" not in current_domain and "google" not in current_domain:
                print(f"    [hook] URL guard: LLM navigated to {current_domain} (start: {_start_domain}), redirecting back")
                await page.evaluate("() => window.history.back()")
                await asyncio.sleep(1.0)
    except Exception:
        pass

    # --- 0.05. Early exit if challenge already complete (on /finish page) ---
    try:
        current_url = await page.evaluate("() => window.location.href")
        if current_url and "/finish" in current_url:
            print(f"    [hook] Already on finish page — challenge complete, stopping")
            return {"skip_llm": True, "reason": "challenge complete (on /finish page)", "done": True}
    except Exception:
        pass

    # --- 0.1. Detect current step and handle resets ---
    current = await _detect_step(page)
    if current > 0:
        if _last_step_number == 0:
            _last_step_number = current
            print(f"    [hook] Initialized step: {current}")
        elif current < _last_step_number:
            # Session was reset (navigation error, etc.) — clear caches
            print(f"    [hook] Session reset detected: step {_last_step_number} -> {current}, clearing caches")
            _last_step_number = current
            _submitted_values.clear()
            _failed_codes.clear()
            _same_page_count = 0
            _tier1_attempts = 0

    # --- 0.5. Hide filler/decoy elements that overlay challenge UI ---
    try:
        await page.evaluate(r"""() => {
            // Completely hide decoy buttons so they don't fill up button lists (8 max)
            // and block real challenge buttons from being detected
            var decoys = /^(Next|Continue|Proceed|Advance|Go Forward|Keep Going|Move On|Move Forward|Next Page|Next Step|Next Section|Proceed Forward|Continue Reading|Continue Journey|Click Here|Link!|Try This!|Click Me!)$/i;
            var btns = document.querySelectorAll('button');
            for (var i = 0; i < btns.length; i++) {
                var btn = btns[i];
                var t = btn.textContent.trim();
                if (decoys.test(t) && !/Submit Code/i.test(t)) {
                    btn.style.display = 'none';
                }
            }
            // Hide floating animated badges/divs that aren't challenge elements
            var allDivs = document.querySelectorAll('div');
            for (var i = 0; i < allDivs.length; i++) {
                var d = allDivs[i];
                var cs = getComputedStyle(d);
                if (cs.position === 'fixed' || cs.position === 'absolute') {
                    var cls = (typeof d.className === 'string') ? d.className : '';
                    // Skip challenge elements (z-[100xx])
                    if (/z-\[100\d\d\]/.test(cls)) continue;
                    // Skip Submit Code container
                    if (d.textContent && /Submit Code/i.test(d.textContent.trim())) continue;
                    // Check if it looks like a floating filler (rotate, colorful, small)
                    var r = d.getBoundingClientRect();
                    if (r.width > 20 && r.width < 200 && r.height > 20 && r.height < 100 &&
                        /rotate|transform|animation/i.test(cls + ' ' + d.style.cssText)) {
                        d.style.pointerEvents = 'none';
                    }
                }
            }
        }""")
    except Exception:
        pass

    # --- 1. Dismiss popups (inline JS — only when popup text detected) ---
    try:
        popup_dismissed = await page.evaluate(r"""() => {
            // Only dismiss if popup-specific text is present
            var bodyText = document.body.innerText || '';
            var hasPopups = /Cookie Consent|popup message|Click X to close|Important Notice|You have won|amazing deals|Subscribe to our|Alert!|Overlay Notice|overlay covers|Modal Dialog|button to dismiss/.test(bodyText);
            if (!hasPopups) return 0;

            var dismissed = 0;
            for (var round = 0; round < 8; round++) {
                var best = null;
                var bestZ = -1;
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    var btn = btns[i];
                    if (btn.offsetWidth === 0 || btn.offsetHeight === 0) continue;
                    var cls = (typeof btn.className === 'string') ? btn.className : '';
                    var t = btn.textContent.trim();
                    // Only target popup-specific close buttons
                    var isPopupClose = false;
                    // Red X circle buttons inside popup containers
                    if (/bg-red/.test(cls) && /rounded-full/.test(cls)) {
                        // Check parent has popup text
                        var parent = btn.parentElement;
                        if (parent) {
                            var pt = parent.textContent || '';
                            if (/popup|Click X|close|notice|alert|deals|prize|cookie|subscribe|newsletter|overlay|modal|dismiss/i.test(pt)) {
                                isPopupClose = true;
                            }
                        }
                    }
                    if (t === 'Dismiss' || t === 'Accept' || t === 'Decline') isPopupClose = true;
                    // "Close" button inside popup/overlay container
                    if (t === 'Close' && !isPopupClose) {
                        var parent = btn.parentElement;
                        while (parent && parent !== document.body) {
                            var pt = parent.textContent || '';
                            if (/popup|notice|alert|overlay|cookie|subscribe|newsletter|prize|deals|modal|dismiss/i.test(pt)) {
                                isPopupClose = true;
                                break;
                            }
                            parent = parent.parentElement;
                        }
                    }
                    if (!isPopupClose) continue;
                    var z = 0;
                    var el = btn;
                    while (el) {
                        var cs = getComputedStyle(el);
                        var zi = parseInt(cs.zIndex);
                        if (!isNaN(zi) && zi > z) z = zi;
                        el = el.parentElement;
                    }
                    if (z > bestZ) { bestZ = z; best = btn; }
                }
                if (!best) break;
                var rk = Object.keys(best).find(function(k) { return k.indexOf('__reactProps') === 0; });
                if (rk && best[rk] && best[rk].onClick) {
                    try { best[rk].onClick({preventDefault:function(){},stopPropagation:function(){},target:best,currentTarget:best,nativeEvent:{}}); }
                    catch(e) { best.click(); }
                } else { best.click(); }
                dismissed++;
            }
            return dismissed;
        }""")
        if popup_dismissed and int(popup_dismissed) > 0:
            print(f"    [hook] Pre-scan: dismissed {popup_dismissed} popups")
            await asyncio.sleep(0.3)
    except Exception:
        pass

    # --- 1b. Handle scrollable radio modal (blocking quiz) ---
    # Use Playwright trusted clicks (React needs trusted events for state updates)
    try:
        radio_info = await page.evaluate(r"""() => {
            // Check for radio buttons (scrollable quiz modal)
            var radios = Array.from(document.querySelectorAll(
                'input[type="radio"], [role="radio"], button[id^="option-"]'
            )).filter(function(r) { return r.offsetWidth > 0; });
            if (radios.length === 0) return JSON.stringify({found: false});

            // Find the "Correct Choice" option
            var best = null;
            var bestText = '';
            for (var i = 0; i < radios.length; i++) {
                var r = radios[i];
                var label = r.closest('label') || r.parentElement;
                var text = (label ? label.textContent : r.textContent) || '';
                if (/\bcorrect\b/i.test(text) && !/incorrect/i.test(text)) {
                    best = r;
                    bestText = text.trim();
                    break;
                }
            }
            if (!best) return JSON.stringify({found: false});

            best.scrollIntoView({block: 'center', behavior: 'instant'});
            var r = best.getBoundingClientRect();

            // Find Submit & Continue button
            var submitBtn = null;
            var btns = document.querySelectorAll('button');
            for (var i = 0; i < btns.length; i++) {
                if (/Submit.*Continue|Confirm/i.test(btns[i].textContent.trim())) {
                    submitBtn = btns[i];
                    break;
                }
            }
            var sbr = submitBtn ? submitBtn.getBoundingClientRect() : null;

            return JSON.stringify({
                found: true,
                label: bestText.substring(0, 50),
                x: Math.round(r.x + r.width/2),
                y: Math.round(r.y + r.height/2),
                submitX: sbr ? Math.round(sbr.x + sbr.width/2) : 0,
                submitY: sbr ? Math.round(sbr.y + sbr.height/2) : 0,
                hasSubmit: !!submitBtn
            });
        }""")
        ri = json.loads(radio_info) if isinstance(radio_info, str) else (radio_info or {})
        if ri.get("found"):
            mouse = await _get_mouse(page)
            # Use Playwright trusted click on radio (triggers React event delegation)
            await mouse.click(ri["x"], ri["y"])
            await asyncio.sleep(0.3)
            print(f"    [hook] Pre-scan: radio clicked '{ri.get('label', '')}'")
            # Click Submit & Continue with trusted click
            if ri.get("hasSubmit"):
                await mouse.click(ri["submitX"], ri["submitY"])
                await asyncio.sleep(0.5)
                print(f"    [hook] Pre-scan: clicked Submit & Continue")
    except Exception as e:
        print(f"    [hook] Pre-scan radio error: {e}")
        pass

    # ═══════════════════════════════════════════════════════════════
    # TIER 0: Code Scanner (JS, <100ms)
    # ═══════════════════════════════════════════════════════════════
    t_scan = time.time()
    ir = await _run_code_scan(page)
    scan_ms = (time.time() - t_scan) * 1000
    if scan_ms > 1000:
        print(f"    [hook] WARNING: code scan took {scan_ms:.0f}ms")
    if not ir:
        ms = (time.time() - t0) * 1000
        _step_timings.append((step_n, 'no_interact', ms))
        print(f"    [hook] Tier 0: no interact skill (step {step_n})")
        return None

    codes = ir.get("codes", [])
    page_info = ir.get("pageInfo", {})
    detected = ir.get("detected", [])
    btns = page_info.get("buttonTexts", [])[:8]
    vis_preview = page_info.get("visibleTextPreview", "")[:200]
    print(f"    [hook] Tier 0: {len(codes)} codes, buttons={btns}, detected={detected}")
    if not codes:
        print(f"    [hook]   vis: {vis_preview}")

    # Deep code extraction when puzzle/challenge is solved but code not found
    # Check full body text (vis_preview is truncated at 200 chars)
    full_body_check = vis_preview
    if not codes:
        try:
            full_body_check = await page.evaluate("() => (document.body ? document.body.innerText.substring(0, 2000) : '')")
        except Exception:
            pass
    if not codes and "Code revealed" in full_body_check:
        print(f"    [hook] 'Code revealed' detected but 0 codes — running deep scan")
        try:
            deep_codes = await page.evaluate(r"""() => {
                var CODE_RE = /^[A-HJ-NP-Z2-9]{6}$/;
                var found = [];
                var seen = new Set();
                function tryAdd(t, src) {
                    t = (t || '').trim();
                    if (CODE_RE.test(t) && /[A-Z]/.test(t) && !seen.has(t)) {
                        seen.add(t);
                        found.push({text: t, source: src});
                    }
                }
                // 1. Check every element's textContent individually (not innerText)
                var allEls = document.querySelectorAll('*');
                for (var i = 0; i < allEls.length; i++) {
                    var el = allEls[i];
                    if (el.children.length > 3) continue;
                    tryAdd(el.textContent, 'deep-text');
                    // Also check childNodes directly
                    for (var c = 0; c < el.childNodes.length; c++) {
                        if (el.childNodes[c].nodeType === 3) {
                            tryAdd(el.childNodes[c].textContent, 'deep-textnode');
                        }
                    }
                }
                // 2. Force-reveal CSS-hidden codes (color tricks, font-size:0, clip-path, etc.)
                for (var i = 0; i < allEls.length; i++) {
                    var el = allEls[i];
                    if (el.children.length > 3) continue;
                    var t = el.textContent.trim();
                    if (!CODE_RE.test(t) || !/[A-Z]/.test(t)) continue;
                    var cs = window.getComputedStyle(el);
                    var hidden = cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0' ||
                        cs.color === cs.backgroundColor || parseInt(cs.fontSize) === 0 ||
                        parseInt(cs.height) === 0 || parseInt(cs.maxHeight) === 0 ||
                        cs.clipPath === 'inset(100%)' || cs.overflow === 'hidden' && el.scrollHeight > el.clientHeight ||
                        (cs.position === 'absolute' && (parseInt(cs.left) < -999 || parseInt(cs.top) < -999)) ||
                        cs.color === 'transparent' || cs.color === 'rgba(0, 0, 0, 0)';
                    if (hidden) {
                        el.style.cssText += 'display:block!important;visibility:visible!important;opacity:1!important;color:#000!important;font-size:16px!important;position:static!important;clip-path:none!important;width:auto!important;height:auto!important;overflow:visible!important;max-height:none!important;';
                        var p = el.parentElement;
                        while (p && p !== document.body) {
                            var ps = window.getComputedStyle(p);
                            if (ps.display === 'none' || ps.visibility === 'hidden' || ps.opacity === '0' ||
                                ps.overflow === 'hidden' || parseInt(ps.maxHeight) === 0 || parseInt(ps.height) === 0) {
                                p.style.cssText += 'display:block!important;visibility:visible!important;opacity:1!important;overflow:visible!important;max-height:none!important;height:auto!important;';
                            }
                            p = p.parentElement;
                        }
                        tryAdd(t, 'deep-reveal');
                    }
                }
                // 3. Check input values and placeholders
                var inputs = document.querySelectorAll('input, textarea');
                for (var i = 0; i < inputs.length; i++) {
                    tryAdd(inputs[i].value, 'input-value');
                    tryAdd(inputs[i].placeholder, 'input-placeholder');
                }
                // 4. Check data attributes on all elements
                for (var i = 0; i < allEls.length; i++) {
                    var attrs = allEls[i].attributes;
                    for (var j = 0; j < attrs.length; j++) {
                        tryAdd(attrs[j].value, 'attr-' + attrs[j].name);
                    }
                }
                return JSON.stringify(found);
            }""")
            deep_list = json.loads(deep_codes) if isinstance(deep_codes, str) else (deep_codes or [])
            if deep_list:
                print(f"    [hook] Deep scan found {len(deep_list)} codes: {[(c['text'], c['source']) for c in deep_list]}")
                codes = deep_list
            else:
                # The deployed site has empty code spans — extract code from React fiber
                try:
                    fiber_codes = await page.evaluate(r"""() => {
                        var CODE_RE = /^[A-HJ-NP-Z2-9]{6}$/;
                        var found = [];
                        // Strategy 1: Find font-mono spans and extract code from React fiber
                        var monos = document.querySelectorAll('.font-mono, [class*="mono"]');
                        for (var i = 0; i < monos.length; i++) {
                            var el = monos[i];
                            // Walk React fiber tree to find code prop
                            var fiberKey = Object.keys(el).find(function(k) {
                                return k.indexOf('__reactFiber') === 0 || k.indexOf('__reactInternalInstance') === 0;
                            });
                            if (fiberKey) {
                                var fiber = el[fiberKey];
                                // Check memoizedProps
                                for (var f = fiber; f && found.length === 0; f = f.return) {
                                    if (f.memoizedProps) {
                                        var props = f.memoizedProps;
                                        // Check children (React passes text content as children)
                                        if (typeof props.children === 'string' && CODE_RE.test(props.children)) {
                                            found.push(props.children);
                                        }
                                        // Check all string props
                                        var keys = Object.keys(props);
                                        for (var k = 0; k < keys.length; k++) {
                                            var v = props[keys[k]];
                                            if (typeof v === 'string' && CODE_RE.test(v) && /[A-Z]/.test(v)) {
                                                found.push(v);
                                            }
                                        }
                                    }
                                    // Also check stateNode
                                    if (f.stateNode && f.stateNode !== el && f.stateNode.state) {
                                        var state = f.stateNode.state;
                                        var skeys = Object.keys(state);
                                        for (var sk = 0; sk < skeys.length; sk++) {
                                            var sv = state[skeys[sk]];
                                            if (typeof sv === 'string' && CODE_RE.test(sv) && /[A-Z]/.test(sv)) {
                                                found.push(sv);
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        // Strategy 2: Walk ALL React fiber trees from root
                        if (found.length === 0) {
                            var roots = document.querySelectorAll('[id]');
                            for (var r = 0; r < roots.length && found.length === 0; r++) {
                                var rootFiberKey = Object.keys(roots[r]).find(function(k) {
                                    return k.indexOf('__reactContainer') === 0;
                                });
                                if (!rootFiberKey) continue;
                                var rootFiber = roots[r][rootFiberKey];
                                // BFS through fiber tree
                                var queue = [rootFiber];
                                var visited = 0;
                                while (queue.length > 0 && visited < 500 && found.length === 0) {
                                    var node = queue.shift();
                                    visited++;
                                    if (!node) continue;
                                    if (node.memoizedProps) {
                                        var mp = node.memoizedProps;
                                        if (typeof mp.children === 'string' && CODE_RE.test(mp.children) && /[A-Z]/.test(mp.children)) {
                                            found.push(mp.children);
                                        }
                                        if (typeof mp.code === 'string' && CODE_RE.test(mp.code)) found.push(mp.code);
                                    }
                                    if (node.child) queue.push(node.child);
                                    if (node.sibling) queue.push(node.sibling);
                                }
                            }
                        }
                        // Strategy 3: Force-reveal and set text on empty code spans
                        if (found.length > 0) {
                            for (var i = 0; i < monos.length; i++) {
                                var el = monos[i];
                                if (!el.textContent.trim() && el.tagName === 'SPAN') {
                                    el.textContent = found[0];
                                    el.style.cssText += 'display:block!important;visibility:visible!important;opacity:1!important;';
                                    if (el.parentElement) {
                                        el.parentElement.style.cssText += 'display:block!important;visibility:visible!important;opacity:1!important;';
                                    }
                                }
                            }
                        }
                        return JSON.stringify(found);
                    }""")
                    fiber_list = json.loads(fiber_codes) if isinstance(fiber_codes, str) else (fiber_codes or [])
                    if fiber_list:
                        print(f"    [hook] Deep scan: extracted {len(fiber_list)} codes from React fiber: {fiber_list}")
                        codes = [{"text": c, "source": "react-fiber"} for c in fiber_list]
                    else:
                        print(f"    [hook] Deep scan: no codes in React fiber either")
                except Exception as e2:
                    print(f"    [hook] Deep scan fiber error: {e2}")
        except Exception as e:
            print(f"    [hook] Deep scan error: {e}")

    # Try immediate code submission
    submitted, new_step = await _try_submit(page, codes)
    if submitted and new_step > _last_step_number:
        _last_step_number = new_step
        _tier1_attempts = 0
        _consecutive_skip_count = 0
        await _scroll_to_top(page)
        print(f"    [hook] -> step {new_step} (Tier 0 auto-submit)")
        # Chain through subsequent auto-resolvable steps
        await _chain_submit(page)
        ms = (time.time() - t0) * 1000
        _step_timings.append((step_n, 'tier0_submit', ms))
        return {"skip_llm": True, "reason": f"Tier 0: auto-submitted, now on step {_last_step_number}"}

    # Execute any JS-detected Playwright actions (from runtime skills)
    did_js_actions = await _execute_js_actions(page, ir)
    if did_js_actions:
        await asyncio.sleep(0.8)
        ir2 = await _run_code_scan(page)
        if ir2:
            codes2 = ir2.get("codes", [])
            # Execute any new click targets from re-scan
            if ir2.get("clickTargets"):
                await _do_clicks(page, ir2["clickTargets"])
                await asyncio.sleep(0.5)
                ir3 = await _run_code_scan(page)
                if ir3:
                    codes2 = ir3.get("codes", [])

            submitted, new_step = await _try_submit(page, codes2)
            if submitted and new_step > _last_step_number:
                _last_step_number = new_step
                _tier1_attempts = 0
                await _scroll_to_top(page)
                print(f"    [hook] -> step {new_step} (Tier 0 post-action)")
                await _chain_submit(page)
                ms = (time.time() - t0) * 1000
                _step_timings.append((step_n, 'tier0_post_action', ms))
                return {"skip_llm": True, "reason": f"Tier 0: post-action submit, now on step {_last_step_number}"}

    # ═══════════════════════════════════════════════════════════════
    # TIER 1+2: Classify + Act loop
    # The classifier uses structural DOM signals (hasCanvas, hasDraggables,
    # hasShadowRoots, hasIframes, hasTabButtons, etc.) to determine the
    # interaction type. No text-pattern overrides — everything routes
    # through the classifier.
    # ═══════════════════════════════════════════════════════════════

    # (old pre-classify overrides removed — classifier handles all routing now)

    if _classifier_enabled and page_info and current not in _bailout_steps:
        tier12_acted = False  # track if we took ANY action

        for attempt in range(3):
            # Get ARIA snapshot for classification
            cur_info = page_info if attempt == 0 else None
            if attempt > 0:
                ir_loop = await _run_code_scan(page)
                if ir_loop:
                    # Check for codes first
                    codes_loop = ir_loop.get("codes", [])
                    submitted, new_step = await _try_submit(page, codes_loop)
                    if submitted and new_step > _last_step_number:
                        _last_step_number = new_step
                        _tier1_attempts = 0
                        await _scroll_to_top(page)
                        print(f"    [hook] -> step {new_step} (Tier 1+2 attempt {attempt+1})")
                        await _chain_submit(page)
                        ms = (time.time() - t0) * 1000
                        _step_timings.append((step_n, 'tier12_submit', ms))
                        return {"skip_llm": True, "reason": f"Tier 1+2: step {_last_step_number}"}
                    cur_info = ir_loop.get("pageInfo", {})

            if not cur_info:
                break

            try:
                aria_snapshot = await page.accessibility.snapshot()
                if aria_snapshot:
                    cur_info["ariaSnapshot"] = _compact_aria(aria_snapshot)
            except Exception:
                pass

            t1 = time.time()
            classification = await _run_classifier(cur_info)
            t1_ms = (time.time() - t1) * 1000

            if not classification or classification.get("action") == "unknown":
                reason = classification.get("reasoning", "no classification") if classification else "classifier failed"
                print(f"    [hook] Tier 1 attempt {attempt+1}: unknown ({reason[:80]})")
                break  # fall through to Tier 3

            action = classification["action"]
            action_params = classification.get("params", {})
            reasoning = classification.get("reasoning", "")
            print(f"    [hook] Tier 1 attempt {attempt+1}: {action} | {reasoning[:80]} ({t1_ms:.0f}ms)")

            # TIER 2: Execute the action
            t2 = time.time()
            handler_result = await _run_action_handler(page, action, action_params)
            t2_ms = (time.time() - t2) * 1000
            print(f"    [hook] Tier 2: {handler_result.get('detail', '')[:100]} ({t2_ms:.0f}ms)")

            if handler_result.get("ok"):
                tier12_acted = True
                _tier1_attempts = 0

                # Check if handler found codes directly (e.g. hover_element scans while hovering)
                handler_codes = handler_result.get("codes", handler_result.get("codes_found", []))
                if handler_codes:
                    codes_as_dicts = []
                    for c in handler_codes:
                        if isinstance(c, dict):
                            # Handler returned dict with 'code' or 'text' key
                            txt = c.get("code", c.get("text", ""))
                            codes_as_dicts.append({"text": txt, "source": "handler"})
                        else:
                            codes_as_dicts.append({"text": str(c), "source": "handler"})
                    # Handler might have triggered onComplete bypass → try last-step nav
                    if not _pick_best_code(codes_as_dicts):
                        finished = await _try_navigate_finish(page)
                        if finished:
                            ms = (time.time() - t0) * 1000
                            _step_timings.append((step_n, f'{action}-finish', ms))
                            return {"skip_llm": True, "reason": f"Tier 1+2: {action} -> /finish (last step)"}
                    submitted, new_step = await _try_submit(page, codes_as_dicts)
                    if submitted and new_step > _last_step_number:
                        _last_step_number = new_step
                        await _scroll_to_top(page)
                        print(f"    [hook] -> step {new_step} (Tier 1+2, handler-codes)")
                        await _chain_submit(page)
                        ms = (time.time() - t0) * 1000
                        _step_timings.append((step_n, 'tier12_submit', ms))
                        return {"skip_llm": True, "reason": f"Tier 1+2: {action} -> step {_last_step_number}"}

                # Wait for page to update after action
                await asyncio.sleep(0.8)

                # Re-scan for codes
                ir_post = await _run_code_scan(page)
                if ir_post:
                    did_post = await _execute_js_actions(page, ir_post)
                    if did_post:
                        await asyncio.sleep(0.5)
                        ir_post = await _run_code_scan(page)

                    codes_post = ir_post.get("codes", []) if ir_post else []
                    submitted, new_step = await _try_submit(page, codes_post)
                    if submitted and new_step > _last_step_number:
                        _last_step_number = new_step
                        await _scroll_to_top(page)
                        print(f"    [hook] -> step {new_step} (Tier 1+2)")
                        await _chain_submit(page)
                        ms = (time.time() - t0) * 1000
                        _step_timings.append((step_n, 'tier12_submit', ms))
                        return {"skip_llm": True, "reason": f"Tier 1+2: {action} -> step {_last_step_number}"}

                # No code found after handler acted — try force-revealing hidden code elements
                if not codes_post or not _pick_best_code(codes_post):
                    codes_rev = await _force_reveal_codes(page)
                    if codes_rev:
                        # Check for FINISH sentinel (last step — /finish navigation)
                        if any(c.get("source") == "navigate-finish" for c in codes_rev):
                            ms = (time.time() - t0) * 1000
                            _step_timings.append((step_n, f'{action}-finish', ms))
                            return {"skip_llm": True, "reason": f"Tier 1+2: {action} -> /finish (last step)"}
                        submitted, new_step = await _try_submit(page, codes_rev)
                        if submitted and new_step > _last_step_number:
                            _last_step_number = new_step
                            await _scroll_to_top(page)
                            print(f"    [hook] -> step {new_step} (Tier 1+2, force-reveal)")
                            await _chain_submit(page)
                            ms = (time.time() - t0) * 1000
                            _step_timings.append((step_n, 'tier12_reveal', ms))
                            return {"skip_llm": True, "reason": f"Tier 1+2: {action} -> step {_last_step_number}"}

                # No code found — for heavy actions, don't retry same thing
                if action in ("drag_drop", "draw_canvas", "shadow_dom", "service_worker",
                             "hover_element", "websocket", "recursive_iframe", "mutation",
                             "sequence", "multi_tab", "encoded_code"):
                    break
                continue
            else:
                _tier1_attempts += 1
                break  # handler failed, fall through

        # If Tier 1+2 took action(s) but didn't find a code,
        # skip the LLM unless we've been stuck too long (bail-out after 2 skips)
        if tier12_acted:
            if current == _skip_step_number:
                _consecutive_skip_count += 1
            else:
                _skip_step_number = current
                _consecutive_skip_count = 1

            if _consecutive_skip_count >= 4:
                _bailout_steps.add(current)
                print(f"    [hook] Bail-out: {_consecutive_skip_count} consecutive skips on step {current}, deferring to LLM permanently")
                _consecutive_skip_count = 0
                # Fall through to Tier 3
            else:
                ms = (time.time() - t0) * 1000
                _step_timings.append((step_n, 'tier12_no_code', ms))
                print(f"    [hook] Tier 1+2 acted but no code — skip LLM ({_consecutive_skip_count}/4), retry next step ({ms:.0f}ms)")
                return {"skip_llm": True, "reason": "Tier 1+2 acted, waiting for code reveal"}

    # ═══════════════════════════════════════════════════════════════
    # LAST STEP CHECK: navigate to /finish if we're on the final step
    # (markChallengeComplete returns codes.get(stepNum+1), but step 31
    #  doesn't exist, so no code can ever be submitted for the last step)
    # ═══════════════════════════════════════════════════════════════
    try:
        finished = await _try_navigate_finish(page)
        if finished:
            ms = (time.time() - t0) * 1000
            _step_timings.append((step_n, 'finish-fallback', ms))
            return {"skip_llm": True, "reason": "navigated to /finish (last step fallback)"}
    except Exception:
        pass

    # ═══════════════════════════════════════════════════════════════
    # TIER 3: Fall through to browser-use LLM agent
    # ═══════════════════════════════════════════════════════════════

    # Stuck detection
    try:
        page_text = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 500) : ''")
        h = hash(page_text)
        if h == _last_page_hash:
            _same_page_count += 1
        else:
            _same_page_count = 0
            _last_page_hash = h
    except Exception:
        pass

    # Stuck recovery: scroll and force-reveal when stuck
    if _same_page_count >= 3:
        if _same_page_count % 2 == 1:
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            print(f"    [hook] Stuck recovery: scrolled to bottom (stuck {_same_page_count} steps)")
        else:
            await page.evaluate("() => window.scrollTo(0, 0)")
            print(f"    [hook] Stuck recovery: scrolled to top (stuck {_same_page_count} steps)")

    if _same_page_count >= 5:
        # Force-reveal hidden elements containing codes
        await page.evaluate("""() => {
            var allEls = document.querySelectorAll('*');
            for (var i = 0; i < allEls.length; i++) {
                var el = allEls[i];
                if (el.offsetWidth > 0 && el.offsetHeight > 0) continue;
                if (el.id && el.id.indexOf('__') === 0) continue;
                var text = el.textContent ? el.textContent.trim() : '';
                if (text.length >= 6 && text.length <= 30 && /[A-HJ-NP-Z2-9]{6}/.test(text) && /[A-HJ-NP-Z]/.test(text)) {
                    el.style.setProperty('display', 'block', 'important');
                    el.style.setProperty('visibility', 'visible', 'important');
                    el.style.setProperty('opacity', '1', 'important');
                    var p = el.parentElement;
                    while (p && p !== document.body) {
                        var cs = getComputedStyle(p);
                        if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') {
                            p.style.setProperty('display', 'block', 'important');
                            p.style.setProperty('visibility', 'visible', 'important');
                            p.style.setProperty('opacity', '1', 'important');
                        }
                        p = p.parentElement;
                    }
                }
            }
        }""")
        print(f"    [hook] Stuck recovery: force-revealed hidden elements")

    if _same_page_count >= 8:
        # Clear submitted codes for current step (allow retry)
        _submitted_values.clear()
        print(f"    [hook] Stuck recovery: cleared submitted codes cache")

    ms = (time.time() - t0) * 1000
    _step_timings.append((step_n, 'pass_to_llm', ms))
    print(f"    [hook] -> Tier 3: passing to LLM (step_n={step_n}, challenge_step={_last_step_number}, same_page={_same_page_count}, {ms:.0f}ms)")
    return None


def _compact_aria(node: dict, depth: int = 0, max_depth: int = 4) -> str:
    """Convert accessibility tree to compact string representation."""
    if depth > max_depth:
        return ""

    role = node.get("role", "")
    name = node.get("name", "")
    children = node.get("children", [])

    parts = []
    if role and name:
        parts.append(f"{'  ' * depth}{role}: {name[:50]}")
    elif role:
        parts.append(f"{'  ' * depth}{role}")

    for child in children[:20]:
        child_str = _compact_aria(child, depth + 1, max_depth)
        if child_str:
            parts.append(child_str)

    return "\n".join(parts)
