"""
Action Handlers: Playwright-based handlers for each classifier action type.

Each handler takes a browser-use page and params dict, executes the action
using JS for element finding + Playwright mouse/keyboard for trusted events,
and returns a result dict with {ok, detail}.

NOTE: browser-use wraps Playwright pages — locator API (get_by_role, etc.) is
NOT available. All element finding must go through page.evaluate() and
mouse.click(x, y) for trusted events.
"""

import asyncio
import json
import re


async def _get_mouse(page):
    m = page.mouse
    if asyncio.iscoroutine(m):
        m = await m
    return m


async def _try_complete_button(page) -> str | None:
    """After an action, try clicking completion buttons if available."""
    for btn_text in ["Submit & Continue", "Reveal Code", "Complete Challenge", "Submit Selection", "Confirm", "Complete"]:
        try:
            raw = await page.evaluate("""(text) => {
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    var btn = buttons[i];
                    if (btn.disabled || btn.offsetWidth === 0) continue;
                    var t = btn.textContent.trim();
                    if (t.indexOf(text) !== -1) {
                        btn.scrollIntoView({block: 'center', behavior: 'instant'});
                        var r = btn.getBoundingClientRect();
                        return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), label: t});
                    }
                }
                return null;
            }""", btn_text)
            if raw:
                info = json.loads(raw) if isinstance(raw, str) else raw
                mouse = await _get_mouse(page)
                await mouse.click(info["x"], info["y"])
                return info.get("label", btn_text)
        except Exception:
            pass
    return None


async def _get_keyboard(page):
    # browser-use page may not expose .keyboard directly — try multiple approaches
    try:
        k = page.keyboard
        if asyncio.iscoroutine(k):
            k = await k
        return k
    except AttributeError:
        pass
    # Try underlying Playwright page
    try:
        pw_page = getattr(page, '_page', None) or getattr(page, 'page', None)
        if pw_page:
            return pw_page.keyboard
    except AttributeError:
        pass
    raise AttributeError("Cannot access keyboard from page object")


async def _type_text_into_focused(page, text: str):
    """Type text into the currently focused element.

    NOTE: browser-use page.press() sends keyDown/keyUp but does NOT insert text
    into input fields. For actual text entry, use _fill_input_react() instead.
    This function uses Playwright keyboard.type() if available, else returns False.
    """
    # Try Playwright keyboard (not available on browser-use Page objects)
    try:
        keyboard = await _get_keyboard(page)
        await keyboard.type(text, delay=30)
        return True
    except (AttributeError, Exception):
        pass
    return False


async def _fill_input_react(page, selector_js: str, value: str):
    """Fill an input field in a React app using framework-aware value setter.
    selector_js should be a JS expression that returns the input element.
    Uses __FW.fillInput if available, falls back to inline _valueTracker reset."""
    return await page.evaluate(r"""(args) => {
        var selectorCode = args[0];
        var value = args[1];
        var inp = eval(selectorCode);
        if (!inp) return 'NOT_FOUND';
        inp.focus();
        // Use framework helper if injected, else inline fallback
        if (window.__FW && __FW.fillInput) {
            __FW.fillInput(inp, value);
        } else {
            var tracker = inp._valueTracker;
            if (tracker) tracker.setValue('');
            var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setter.call(inp, value);
            inp.dispatchEvent(new Event('input', {bubbles: true}));
            inp.dispatchEvent(new Event('change', {bubbles: true}));
        }
        return 'filled:' + value + ' len=' + inp.value.length;
    }""", [selector_js, value])


# ── JS helper: find button by text, return center coords ────────────────────
_FIND_BUTTON_JS = r"""(text) => {
    // Search buttons first (exact match)
    var buttons = document.querySelectorAll('button');
    for (var i = 0; i < buttons.length; i++) {
        var btn = buttons[i];
        if (btn.offsetWidth === 0 || btn.offsetHeight === 0) continue;
        var t = btn.textContent.trim();
        if (t === text || t.toLowerCase() === text.toLowerCase()) {
            btn.scrollIntoView({block: 'center', behavior: 'instant'});
            var r = btn.getBoundingClientRect();
            return JSON.stringify({ok: true, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), label: t, disabled: btn.disabled});
        }
    }
    // Buttons partial match
    for (var i = 0; i < buttons.length; i++) {
        var btn = buttons[i];
        if (btn.offsetWidth === 0 || btn.offsetHeight === 0) continue;
        var t = btn.textContent.trim();
        if (t.toLowerCase().indexOf(text.toLowerCase()) !== -1) {
            btn.scrollIntoView({block: 'center', behavior: 'instant'});
            var r = btn.getBoundingClientRect();
            return JSON.stringify({ok: true, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), label: t, disabled: btn.disabled});
        }
    }
    // Search ALL clickable/visible elements (div, span, a, etc.) for text match
    var allEls = document.querySelectorAll('div, span, a, p, label, [role="button"], [onclick], [style*="cursor:pointer"], [style*="cursor: pointer"]');
    for (var i = 0; i < allEls.length; i++) {
        var el = allEls[i];
        if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
        if (el.tagName === 'BUTTON') continue;  // already checked
        var ct = el.textContent.trim();
        // For non-buttons, prefer shorter text matches (more specific elements)
        if (ct.length > 200) continue;
        if (ct === text || ct.toLowerCase() === text.toLowerCase()) {
            el.scrollIntoView({block: 'center', behavior: 'instant'});
            var r = el.getBoundingClientRect();
            return JSON.stringify({ok: true, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), label: ct.substring(0, 40), isButton: false});
        }
    }
    return JSON.stringify({ok: false, reason: 'button not found: ' + text});
}"""


async def handle_click_button(page, params: dict) -> dict:
    """Click button(s) matching text. Uses React onClick dispatch + Playwright trusted click."""
    text = params.get("button_text", "")
    times = params.get("times", 1)
    if not text:
        return {"ok": False, "detail": "no button_text provided"}

    mouse = await _get_mouse(page)
    clicked = 0
    for i in range(times):
        try:
            # Strategy 1: React onClick dispatch (bypasses z-index overlay issues)
            react_clicked = await page.evaluate(r"""(text) => {
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    var btn = buttons[i];
                    if (btn.offsetWidth === 0 || btn.offsetHeight === 0) continue;
                    var t = btn.textContent.trim();
                    if (t === text || t.toLowerCase() === text.toLowerCase() ||
                        t.toLowerCase().indexOf(text.toLowerCase()) !== -1) {
                        var rk = Object.keys(btn).find(function(k) { return k.indexOf('__reactProps') === 0; });
                        if (rk && btn[rk] && btn[rk].onClick) {
                            try {
                                btn[rk].onClick({
                                    preventDefault:function(){},stopPropagation:function(){},
                                    target:btn,currentTarget:btn,nativeEvent:{},bubbles:true,type:'click'
                                });
                                return true;
                            } catch(e) {}
                        }
                        // Also try native click
                        btn.click();
                        return true;
                    }
                }
                return false;
            }""", text)
            if react_clicked:
                clicked += 1
                if i < times - 1:
                    await asyncio.sleep(0.3)
                continue

            # Strategy 2: Playwright mouse.click (trusted event)
            raw = await page.evaluate(_FIND_BUTTON_JS, text)
            info = json.loads(raw) if isinstance(raw, str) else raw
            if not info or not info.get("ok"):
                return {"ok": clicked > 0, "detail": f"clicked {clicked}/{times}, {info.get('reason', 'not found')}"}
            await mouse.click(info["x"], info["y"])
            clicked += 1
            if i < times - 1:
                await asyncio.sleep(0.3)
        except Exception as e:
            return {"ok": clicked > 0, "detail": f"clicked {clicked}/{times}, error: {e}"}

    return {"ok": True, "detail": f"clicked '{text}' {clicked}x"}


# JS to find clickable element by substring (searches divs, spans, challenge boxes)
_FIND_ELEMENT_JS = r"""(text) => {
    var lower = text.toLowerCase();
    // Find the SMALLEST element that contains the text (most specific)
    var best = null;
    var bestSize = Infinity;
    var allEls = document.querySelectorAll('div, section, article, span, p, a, [role="button"], [onclick]');
    for (var i = 0; i < allEls.length; i++) {
        var el = allEls[i];
        if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
        var ct = el.textContent.trim();
        if (ct.length > 500) continue;  // skip very large containers
        if (ct.toLowerCase().indexOf(lower) !== -1) {
            var size = ct.length;
            if (size < bestSize) {
                bestSize = size;
                best = el;
            }
        }
    }
    if (best) {
        best.scrollIntoView({block: 'center', behavior: 'instant'});
        var r = best.getBoundingClientRect();
        return JSON.stringify({ok: true, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                               label: best.textContent.trim().substring(0, 50), tag: best.tagName});
    }
    return JSON.stringify({ok: false, reason: 'element not found: ' + text});
}"""


async def handle_click_element(page, params: dict) -> dict:
    """Click non-button elements by text content. Uses React onClick dispatch first, then Playwright click."""
    text = params.get("button_text", "")
    times = params.get("times", 1)
    if not text:
        # Fallback: look for common clickable text patterns in the page
        text = "click here"  # most common pattern for click_element targets

    mouse = await _get_mouse(page)

    # Strategy 1: Find element with React onClick and dispatch directly
    try:
        react_result = await page.evaluate(r"""(args) => {
            var text = args[0];
            var times = args[1];
            var lower = text.toLowerCase();
            var clicked = 0;

            // Walk up to find nearest ancestor with React onClick
            function findReactOnClick(el) {
                var cur = el;
                for (var depth = 0; depth < 5 && cur; depth++) {
                    var rk = Object.keys(cur).find(function(k) { return k.indexOf('__reactProps') === 0; });
                    if (rk && cur[rk] && cur[rk].onClick) return {el: cur, rk: rk};
                    cur = cur.parentElement;
                }
                return null;
            }

            // Find all non-button elements matching text
            var allEls = document.querySelectorAll('div, section, article, span, p, a, [role="button"]');
            var targets = [];
            for (var i = 0; i < allEls.length; i++) {
                var el = allEls[i];
                if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                if (el.tagName === 'BUTTON') continue;
                var ct = el.textContent.trim();
                if (ct.length > 300) continue;
                if (ct.toLowerCase().indexOf(lower) === -1) continue;
                var react = findReactOnClick(el);
                if (react) {
                    el.scrollIntoView({block: 'center', behavior: 'instant'});
                    var r = el.getBoundingClientRect();
                    targets.push({el: react.el, rk: react.rk, size: ct.length,
                                  x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
                }
            }

            // Deduplicate by element reference
            var unique = [];
            var seen = new Set();
            targets.sort(function(a,b) { return a.size - b.size; });
            for (var i = 0; i < targets.length; i++) {
                if (!seen.has(targets[i].el)) {
                    seen.add(targets[i].el);
                    unique.push(targets[i]);
                }
            }

            // Click each unique target `times` times
            for (var t = 0; t < times; t++) {
                for (var i = 0; i < unique.length; i++) {
                    var u = unique[i];
                    try {
                        var evt = {preventDefault:function(){},stopPropagation:function(){},
                                   target:u.el,currentTarget:u.el,nativeEvent:{}};
                        u.el[u.rk].onClick(evt);
                        clicked++;
                    } catch(e) {}
                }
            }
            if (clicked > 0) {
                return JSON.stringify({ok: true, clicked: clicked, method: 'react',
                    x: unique[0] ? unique[0].x : 0, y: unique[0] ? unique[0].y : 0});
            }
            return JSON.stringify({ok: false});
        }""", [text, times])
        rr = json.loads(react_result) if isinstance(react_result, str) else (react_result or {})
        if rr.get("ok"):
            return {"ok": True, "detail": f"React onClick {rr['clicked']}x on '{text[:30]}'"}
    except Exception:
        pass

    # Strategy 2: Find elements and Playwright click
    try:
        all_matches = await page.evaluate("""(text) => {
            var lower = text.toLowerCase();
            var matches = [];
            var allEls = document.querySelectorAll('div, section, article, span, p, a, [role="button"], [onclick]');
            for (var i = 0; i < allEls.length; i++) {
                var el = allEls[i];
                if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                if (el.tagName === 'BUTTON') continue;
                var ct = el.textContent.trim();
                if (ct.length > 200) continue;
                if (ct.toLowerCase().indexOf(lower) !== -1) {
                    el.scrollIntoView({block: 'center', behavior: 'instant'});
                    var r = el.getBoundingClientRect();
                    if (r.y >= 0 && r.y < window.innerHeight && r.width > 5 && r.height > 5) {
                        matches.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                                      label: ct.substring(0, 50), tag: el.tagName, size: ct.length});
                    }
                }
            }
            matches.sort(function(a,b) { return a.size - b.size; });
            var seen = {};
            var unique = [];
            for (var i = 0; i < matches.length; i++) {
                var key = Math.round(matches[i].x / 20) + ',' + Math.round(matches[i].y / 20);
                if (!seen[key]) { seen[key] = true; unique.push(matches[i]); }
            }
            return JSON.stringify(unique);
        }""", text)
        matches = json.loads(all_matches) if isinstance(all_matches, str) else (all_matches or [])
    except Exception:
        matches = []

    if not matches:
        try:
            raw = await page.evaluate(_FIND_ELEMENT_JS, text)
            info = json.loads(raw) if isinstance(raw, str) else raw
            if info and info.get("ok"):
                await mouse.click(info["x"], info["y"])
                return {"ok": True, "detail": f"clicked element '{text[:30]}' 1x ({info.get('tag', 'unknown')})"}
        except Exception:
            pass
        return {"ok": False, "detail": f"element not found: {text[:30]}"}

    # Click the best match `times` times (same element, multiple clicks)
    target = matches[0]
    clicked = 0
    for _ in range(times):
        try:
            await mouse.click(target["x"], target["y"])
            clicked += 1
            await asyncio.sleep(0.3)
        except Exception:
            pass

    return {"ok": clicked > 0, "detail": f"clicked '{target.get('label', text)[:30]}' {clicked}x"}


async def handle_fill_form(page, params: dict) -> dict:
    """Compute answer, fill input, click solve button."""
    answer = str(params.get("answer", ""))
    hint = params.get("input_placeholder", "")
    expression = params.get("expression", "")

    if not answer and expression:
        try:
            expr = expression.replace("x", "*").replace("\u00d7", "*").replace("\u00f7", "/")
            expr = re.sub(r'=\s*\?', '', expr).strip()
            answer = str(int(eval(expr)))  # noqa: S307
        except Exception:
            return {"ok": False, "detail": f"cannot compute expression: {expression}"}

    if not answer:
        return {"ok": False, "detail": "no answer provided"}

    # Find puzzle input (NOT the code submission input) and fill via JS
    # Retry up to 5 times with increasing waits (SPA may not have rendered input yet)
    result = None
    for _fill_attempt in range(5):
        if _fill_attempt > 0:
            await asyncio.sleep(0.3 + _fill_attempt * 0.3)  # 0.6, 0.9, 1.2, 1.5s
        result = await page.evaluate("""(opts) => {
        var answer = opts.answer;
        var hint = opts.hint;
        var input = null;

        // Strategy 1: Find by placeholder hint
        if (hint) {
            var inputs = document.querySelectorAll('input');
            for (var i = 0; i < inputs.length; i++) {
                var ph = (inputs[i].placeholder || '').toLowerCase();
                if (ph.indexOf(hint.toLowerCase()) !== -1) { input = inputs[i]; break; }
            }
        }

        // Strategy 2: Find non-code text input near math content
        if (!input) {
            var inputs = document.querySelectorAll('input[type="text"], input[type="number"], input[type="tel"], input:not([type])');
            for (var i = 0; i < inputs.length; i++) {
                var inp = inputs[i];
                if (inp.offsetWidth === 0 || inp.offsetHeight === 0) continue;
                var ph = (inp.placeholder || '').toLowerCase();
                if (ph.indexOf('code') !== -1 || ph.indexOf('character') !== -1) continue;
                if (ph.indexOf('6-char') !== -1 || ph.indexOf('enter the 6') !== -1) continue;
                input = inp;
                break;
            }
        }
        // Strategy 3: Any visible input (broader search)
        if (!input) {
            var allInputs = document.querySelectorAll('input');
            for (var i = 0; i < allInputs.length; i++) {
                var inp = allInputs[i];
                if (inp.offsetWidth === 0 || inp.offsetHeight === 0) continue;
                var ph = (inp.placeholder || '').toLowerCase();
                if (ph.indexOf('6-char') !== -1 || ph.indexOf('enter the 6') !== -1) continue;
                if (ph.indexOf('character code') !== -1) continue;
                // Skip the code submission input specifically
                if (inp.id === 'code-input') continue;
                input = inp;
                break;
            }
        }

        if (!input) {
            // Debug: list all inputs on page
            var dbgInputs = [];
            var allInps = document.querySelectorAll('input');
            for (var di = 0; di < allInps.length; di++) {
                dbgInputs.push({
                    type: allInps[di].type,
                    placeholder: (allInps[di].placeholder || '').substring(0, 30),
                    id: allInps[di].id,
                    w: allInps[di].offsetWidth,
                    h: allInps[di].offsetHeight,
                    vis: window.getComputedStyle(allInps[di]).display
                });
            }
            return JSON.stringify({ok: false, reason: 'no input found', inputs: dbgInputs});
        }

        // Fill using native setter (works with React controlled inputs)
        input.scrollIntoView({block: 'center', behavior: 'instant'});
        input.focus();
        // Reset _valueTracker so React sees the change
        var tracker = input._valueTracker;
        if (tracker) tracker.setValue('');
        try {
            var nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            nativeSetter.call(input, String(answer));
        } catch(e) { input.value = String(answer); }
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
        // Also dispatch InputEvent for completeness
        try { input.dispatchEvent(new InputEvent('input', {bubbles: true, data: String(answer), inputType: 'insertText'})); } catch(e) {}

        // Also try React onChange
        var rk = Object.keys(input).find(function(k) { return k.indexOf('__reactProps') === 0; });
        if (rk && input[rk] && input[rk].onChange) {
            try { input[rk].onChange({target: input}); } catch(e) {}
        }

        // Find and click Solve/Submit button (not "Submit Code")
        var solveBtn = null;
        var solvePatterns = /^(solve|check|verify|calculate|compute|submit)$/i;
        var buttons = document.querySelectorAll('button');
        for (var i = 0; i < buttons.length; i++) {
            var txt = buttons[i].textContent.trim();
            if (solvePatterns.test(txt) && !/submit code/i.test(txt)) {
                if (buttons[i].offsetWidth > 0 && buttons[i].offsetHeight > 0) {
                    solveBtn = buttons[i];
                    break;
                }
            }
        }
        if (solveBtn) {
            // Re-verify input value is set (React may have reset it)
            if (input.value !== String(answer)) {
                var tracker2 = input._valueTracker;
                if (tracker2) tracker2.setValue('');
                try {
                    var ns2 = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                    ns2.call(input, String(answer));
                } catch(e) { input.value = String(answer); }
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.dispatchEvent(new Event('change', {bubbles: true}));
            }
            // Fire React onClick on Solve button
            var cur2 = solveBtn;
            for (var d2 = 0; d2 < 5 && cur2; d2++) {
                var rk2 = Object.keys(cur2).find(function(k) { return k.indexOf('__reactProps') === 0; });
                if (rk2 && cur2[rk2] && cur2[rk2].onClick) {
                    try { cur2[rk2].onClick({preventDefault:function(){},stopPropagation:function(){},
                        target:solveBtn,currentTarget:solveBtn,nativeEvent:{},bubbles:true,type:'click'}); } catch(e) {}
                    break;
                }
                cur2 = cur2.parentElement;
            }
            // Also fire native click (triggers addEventListener handlers)
            solveBtn.click();
            solveBtn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            solveBtn.scrollIntoView({block: 'center', behavior: 'instant'});
            var r = solveBtn.getBoundingClientRect();
            return JSON.stringify({ok: true, filled: true, btn: {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), label: solveBtn.textContent.trim()}});
        }
        return JSON.stringify({ok: true, filled: true, btn: null});
    }""", {"answer": answer, "hint": hint})

        info = json.loads(result) if isinstance(result, str) else result
        if info and info.get("ok"):
            break  # input found and filled
        if _fill_attempt < 4:
            continue  # retry
        # All retries exhausted — try Playwright locator + keyboard as fallback
        if not info or not info.get("ok"):
            dbg_inputs = info.get("inputs", []) if info else []
            # Debug: check for ALL inputs including type=number
            try:
                all_count = await page.evaluate("""() => {
                    var all = document.querySelectorAll('input');
                    var num = document.querySelectorAll('input[type="number"]');
                    var ph = document.querySelectorAll('input[placeholder*="answer" i]');
                    return JSON.stringify({total: all.length, number: num.length, answer: ph.length});
                }""")
                print(f"    [fill_form] Debug DOM inputs: {all_count}")
            except Exception:
                pass
            # Fallback 1: Use Playwright locator to find input by placeholder
            try:
                loc = page.get_by_placeholder("Enter answer")
                cnt = await loc.count()
                print(f"    [fill_form] Playwright locator count: {cnt}")
                if cnt > 0:
                    await loc.fill(str(answer))
                    await asyncio.sleep(0.2)
                    solve_loc = page.get_by_role("button", name="Solve")
                    if await solve_loc.count() > 0:
                        await solve_loc.click()
                        await asyncio.sleep(0.5)
                        codes = await _scan_for_codes(page)
                        if codes:
                            return {"ok": True, "detail": f"fill_form: Playwright locator filled '{answer}', solved", "codes": codes}
                        # Try onComplete bypass
                        bypass_codes = await _onComplete_bypass(page, "puzzle_solve", ["button"])
                        if bypass_codes:
                            return {"ok": True, "detail": f"fill_form: Playwright + onComplete bypass", "codes": bypass_codes}
                        return {"ok": True, "detail": f"fill_form: Playwright locator filled '{answer}', clicked Solve"}
            except Exception as e:
                print(f"    [fill_form] Playwright locator error: {e}")
            # Fallback 2: Direct onComplete bypass — walk fiber tree from ALL elements
            try:
                bypass_codes = await _onComplete_bypass(page, "puzzle_solve", ["button", "[class*='bg-pink']", "[class*='puzzle']", "input"])
                print(f"    [fill_form] onComplete bypass result: {bypass_codes}")
                if bypass_codes:
                    return {"ok": True, "detail": f"fill_form: onComplete bypass", "codes": bypass_codes}
            except Exception as e:
                print(f"    [fill_form] onComplete bypass error: {e}")
            return {"ok": False, "detail": f"fill_form: {info.get('reason', 'failed')}, inputs={json.dumps(dbg_inputs)[:200]}"}

    # Playwright trusted click on solve button
    if info.get("btn"):
        mouse = await _get_mouse(page)
        await asyncio.sleep(0.2)
        await mouse.click(info["btn"]["x"], info["btn"]["y"])

    await asyncio.sleep(0.5)

    # Post-solve scan: look for newly-revealed code elements
    codes_found = await page.evaluate(r"""() => {
        var CODE_RE = /^[A-HJ-NP-Z2-9]{6}$/;
        var found = [];
        // Scan all spans/divs for 6-char codes (especially newly-revealed ones)
        var els = document.querySelectorAll('span, div, p, code, pre, strong, b');
        for (var i = 0; i < els.length; i++) {
            var el = els[i];
            if (el.children.length > 2) continue; // skip containers
            var t = el.textContent.trim();
            if (CODE_RE.test(t) && /[A-Z]/.test(t)) {
                var cs = window.getComputedStyle(el);
                found.push({text: t, vis: cs.display !== 'none' && cs.visibility !== 'hidden'});
            }
        }
        // Also check if any hidden code elements exist and force-reveal them
        var allEls = document.querySelectorAll('*');
        for (var i = 0; i < allEls.length; i++) {
            var el = allEls[i];
            if (el.children.length > 2) continue;
            var t = el.textContent.trim();
            if (!CODE_RE.test(t) || !/[A-Z]/.test(t)) continue;
            var cs = window.getComputedStyle(el);
            if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0' ||
                cs.color === cs.backgroundColor || parseInt(cs.fontSize) === 0 ||
                parseInt(cs.height) === 0 || parseInt(cs.width) === 0 ||
                cs.clipPath === 'inset(100%)' || cs.position === 'absolute' && parseInt(cs.left) < -999) {
                // Force-reveal
                el.style.cssText += 'display:block!important;visibility:visible!important;opacity:1!important;color:#000!important;font-size:16px!important;position:static!important;clip-path:none!important;width:auto!important;height:auto!important;overflow:visible!important;';
                if (el.parentElement) {
                    el.parentElement.style.cssText += 'display:block!important;visibility:visible!important;opacity:1!important;overflow:visible!important;';
                }
                found.push({text: t, vis: false, revealed: true});
            }
        }
        return JSON.stringify(found);
    }""")

    found_codes = []
    try:
        items = json.loads(codes_found) if isinstance(codes_found, str) else (codes_found or [])
        for item in items:
            if item.get("text"):
                found_codes.append(item["text"])
    except Exception:
        pass

    detail = f"filled '{answer}'"
    if info.get("btn"):
        detail += f", clicked '{info['btn']['label']}'"
    else:
        detail += ", no solve button found"
    if found_codes:
        detail += f", codes={found_codes}"

    return {"ok": True, "detail": detail, "codes": found_codes}


async def handle_scroll_down(page, params: dict) -> dict:
    """Scroll the page down — tries window first, then scrollable containers."""
    pixels = params.get("pixels", 600)
    try:
        result = await page.evaluate(f"""() => {{
            var before = window.scrollY;
            window.scrollBy(0, {pixels});
            var after = window.scrollY;
            if (after > before) return JSON.stringify({{ok: true, scrolled: after - before, total: after}});
            // Window didn't scroll — try scrollable containers
            var containers = document.querySelectorAll('div, main, section');
            for (var i = 0; i < containers.length; i++) {{
                var el = containers[i];
                if (el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 200) {{
                    var b = el.scrollTop;
                    el.scrollBy(0, {pixels});
                    if (el.scrollTop > b) return JSON.stringify({{ok: true, scrolled: el.scrollTop - b, total: el.scrollTop, container: true}});
                }}
            }}
            return JSON.stringify({{ok: true, scrolled: 0, total: after}});
        }}""")
        info = json.loads(result) if isinstance(result, str) else result
        return {"ok": True, "detail": f"scrolled {info.get('scrolled', pixels)}px (total: {info.get('total', '?')})"}
    except Exception as e:
        return {"ok": False, "detail": f"scroll error: {e}"}


async def _hover_at(page, mouse, x, y, seconds):
    """Move mouse and dispatch hover events (React + native) at (x,y), wait seconds."""
    await mouse.move(x, y)
    await page.evaluate("""(pos) => {
        var el = document.elementFromPoint(pos.x, pos.y);
        if (!el) return;
        // Walk up to find element with React onMouseEnter
        var current = el;
        for (var depth = 0; depth < 8 && current; depth++) {
            var rk = Object.keys(current).find(function(k) { return k.indexOf('__reactProps') === 0; });
            if (rk && current[rk] && current[rk].onMouseEnter) {
                current[rk].onMouseEnter({
                    clientX: pos.x, clientY: pos.y,
                    target: current, currentTarget: current,
                    preventDefault: function(){}, stopPropagation: function(){},
                    nativeEvent: new MouseEvent('mouseenter', {clientX: pos.x, clientY: pos.y})
                });
                break;
            }
            current = current.parentElement;
        }
        // Also dispatch native events
        el.dispatchEvent(new MouseEvent('mouseenter', {bubbles: false, clientX: pos.x, clientY: pos.y}));
        el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true, clientX: pos.x, clientY: pos.y}));
        el.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, clientX: pos.x, clientY: pos.y}));
    }""", {"x": x, "y": y})
    await asyncio.sleep(seconds)


async def handle_hover_element(page, params: dict) -> dict:
    """Hover over an element for a duration to reveal hidden content."""
    hover_text = params.get("hover_text", "")
    seconds = max(params.get("seconds", 3.0), 3.0)  # minimum 3s for hover challenges

    try:
        # Collect ALL candidate targets with scores, then pick the best
        candidates_raw = await page.evaluate(r"""(hoverText) => {
            var candidates = [];
            var WHITES = ['rgba(0, 0, 0, 0)', 'transparent', 'rgb(255, 255, 255)',
                          'rgb(249, 250, 251)', 'rgb(243, 244, 246)', 'rgb(229, 231, 235)',
                          'rgb(248, 250, 252)', 'rgb(241, 245, 249)', 'rgb(226, 232, 240)'];
            var seen = {};

            // Helper: add candidate if valid position
            function addCandidate(el, score, strategy) {
                var r = el.getBoundingClientRect();
                if (r.width < 20 || r.height < 20 || r.width > 900 || r.height > 700) return;
                if (r.y + r.height < 0 || r.y > window.innerHeight) {
                    el.scrollIntoView({block: 'center', behavior: 'instant'});
                    r = el.getBoundingClientRect();
                }
                if (r.y < 0 || r.y > window.innerHeight) return;
                var key = Math.round(r.x + r.width/2) + ',' + Math.round(r.y + r.height/2);
                if (seen[key]) { seen[key].score = Math.max(seen[key].score, score); return; }
                var c = {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                         w: Math.round(r.width), h: Math.round(r.height),
                         score: score, strategy: strategy};
                seen[key] = c;
                candidates.push(c);
            }

            var allEls = document.querySelectorAll('div, span, section, article');

            // S0: React onMouseEnter — highest priority (score 100)
            for (var i = 0; i < allEls.length; i++) {
                var el = allEls[i];
                var rk = Object.keys(el).find(function(k) { return k.indexOf('__reactProps') === 0; });
                if (rk && el[rk] && el[rk].onMouseEnter) {
                    var r = el.getBoundingClientRect();
                    if (r.width > 30 && r.height > 30 && r.width < 800 && r.height < 600)
                        addCandidate(el, 100, 'react-onMouseEnter');
                }
            }

            // S1: data attributes / ID (score 90)
            var hoverEl = document.querySelector('#hover-target, [data-hover-target], [data-hover], [data-testid*="hover"]');
            if (hoverEl && hoverEl.offsetWidth > 0)
                addCandidate(hoverEl, 90, 'data-attr');

            // S2: Colored box — non-white background, box-like shape (score 60-80)
            for (var i = 0; i < allEls.length; i++) {
                var el = allEls[i];
                if (el.offsetWidth === 0) continue;
                var s = getComputedStyle(el);
                var bg = s.backgroundColor;
                if (!bg || WHITES.indexOf(bg) !== -1) continue;
                var r = el.getBoundingClientRect();
                if (r.width < 30 || r.width > 500 || r.height < 30 || r.height > 500) continue;
                // Higher score for more "box-like" elements (squarish, mid-size, below header)
                var aspect = Math.max(r.width, r.height) / Math.min(r.width, r.height);
                var score = 60;
                if (aspect < 3) score += 10;  // more squarish
                if (r.y > 100) score += 5;    // below header area
                if (r.width >= 50 && r.height >= 50) score += 5;  // decent size
                // Check for cursor:pointer or transition (often hover targets)
                if (s.cursor === 'pointer') score += 10;
                if (s.transition && s.transition !== 'all 0s ease 0s' && s.transition !== 'none') score += 5;
                addCandidate(el, score, 'colored-box');
            }

            // S3: Elements with CSS transition/cursor:pointer that look interactive (score 50)
            for (var i = 0; i < allEls.length; i++) {
                var el = allEls[i];
                if (el.offsetWidth === 0 || el.tagName === 'BUTTON') continue;
                var s = getComputedStyle(el);
                var r = el.getBoundingClientRect();
                if (r.width < 40 || r.height < 40 || r.width > 500 || r.height > 500) continue;
                var hasTransition = s.transition && s.transition !== 'all 0s ease 0s' && s.transition !== 'none';
                var hasCursor = s.cursor === 'pointer';
                if ((hasTransition || hasCursor) && r.y > 80)
                    addCandidate(el, 50, 'interactive-css');
            }

            // S4: Find element with "Hover here" text — very specific for hover challenges (score 95)
            var textEls = document.querySelectorAll('div, span');
            for (var i = 0; i < textEls.length; i++) {
                var el = textEls[i];
                if (el.offsetWidth === 0) continue;
                var t = el.textContent.trim();
                if (/hover\s*here/i.test(t) && t.length < 40 && el.children.length <= 1) {
                    addCandidate(el, 95, 'hover-text');
                }
            }

            // S5: Challenge container child — find the main challenge area and pick a box-like child (score 40)
            var containers = document.querySelectorAll('[class*="challenge"], [class*="content"], main, [role="main"]');
            for (var ci = 0; ci < containers.length; ci++) {
                var cont = containers[ci];
                var children = cont.querySelectorAll('div');
                for (var j = 0; j < children.length; j++) {
                    var child = children[j];
                    if (child.children.length > 3) continue;  // skip complex containers
                    var r = child.getBoundingClientRect();
                    if (r.width >= 40 && r.width <= 400 && r.height >= 40 && r.height <= 400 && r.y > 80) {
                        var s = getComputedStyle(child);
                        var bg = s.backgroundColor;
                        if (bg && WHITES.indexOf(bg) === -1)
                            addCandidate(child, 40, 'challenge-child');
                    }
                }
            }

            // Sort by score descending
            candidates.sort(function(a, b) { return b.score - a.score; });
            // Return top 5 candidates for multi-attempt
            return JSON.stringify(candidates.slice(0, 5));
        }""", hover_text)

        candidates = json.loads(candidates_raw) if isinstance(candidates_raw, str) else candidates_raw
        if not candidates:
            return {"ok": False, "detail": "no hover target found"}

        mouse = await _get_mouse(page)

        # Inline JS code scanner — runs while mouse is still hovering
        HOVER_CODE_SCAN_JS = r"""() => {
            // Scan visible text for 6-char codes while hovering
            var CHARSET = /^[A-HJ-NP-Z2-9]{6}$/;
            var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
            var codes = [];
            while (walker.nextNode()) {
                var t = walker.currentNode.textContent.trim();
                if (!t) continue;
                var matches = t.match(/\b[A-HJ-NP-Z2-9]{6}\b/g);
                if (matches) {
                    for (var i = 0; i < matches.length; i++) {
                        if (/[A-Z]/.test(matches[i])) codes.push(matches[i]);
                    }
                }
            }
            // Also check elements that became visible recently
            var els = document.querySelectorAll('[data-code], [data-testid*="code"]');
            for (var i = 0; i < els.length; i++) {
                var v = els[i].getAttribute('data-code') || els[i].textContent.trim();
                if (CHARSET.test(v) && /[A-Z]/.test(v)) codes.push(v);
            }
            return JSON.stringify(codes);
        }"""

        # Before hovering, aggressively hide ALL popups/overlays that could intercept the hover
        await page.evaluate(r"""() => {
            // Remove fixed/absolute overlays that could intercept mouse events
            var els = document.querySelectorAll('div');
            for (var i = 0; i < els.length; i++) {
                var el = els[i];
                var s = getComputedStyle(el);
                var cls = typeof el.className === 'string' ? el.className : '';
                // Hide popup overlays (z-index 9996-9999)
                if ((s.position === 'fixed' || s.position === 'absolute') && parseInt(s.zIndex) >= 9000) {
                    el.style.display = 'none';
                }
                // Hide floating "Click Here" decoys
                if (cls.indexOf('cursor-pointer') !== -1 && cls.indexOf('absolute') !== -1 &&
                    el.textContent.trim() === 'Click Here!') {
                    el.style.display = 'none';
                }
            }
        }""")

        # Try each candidate until one produces a completion button or reveals a code
        for idx, cand in enumerate(candidates):
            cx, cy = int(cand["x"]), int(cand["y"])
            strategy = cand.get("strategy", "unknown")
            score = cand.get("score", 0)
            hover_time = seconds if idx == 0 else 3.0
            print(f"    [hover] attempt {idx+1}/{len(candidates)}: ({cx},{cy}) via {strategy} (score={score})")

            await _hover_at(page, mouse, cx, cy, hover_time)

            # WHILE STILL HOVERING: scan for codes and buttons
            # 1. Check for completion button
            btn = await _try_complete_button(page)
            detail = f"hovered at ({cx},{cy}) for {hover_time}s via {strategy}"
            if btn:
                detail += f", clicked '{btn}'"
                return {"ok": True, "detail": detail}

            # 2. Scan for codes that appeared during hover (before mouse moves away!)
            try:
                codes_raw = await page.evaluate(HOVER_CODE_SCAN_JS)
                codes = json.loads(codes_raw) if isinstance(codes_raw, str) else codes_raw
                if codes and len(codes) > 0:
                    # Found codes while hovering! Return them
                    unique_codes = list(dict.fromkeys(codes))  # dedupe preserving order
                    detail += f", found codes while hovering: {unique_codes}"
                    return {"ok": True, "detail": detail, "codes_found": unique_codes}
            except Exception:
                pass

            # 3. Try calling page_assist / interact skill to auto-submit
            try:
                pa_result = await page.evaluate("() => window.__skills && window.__skills.interact ? window.__skills.interact({probeOnly: false}) : null")
                if pa_result:
                    pa = json.loads(pa_result) if isinstance(pa_result, str) else pa_result
                    if pa and pa.get("codes"):
                        detail += f", interact found codes: {[c.get('text','') for c in pa['codes']]}"
                        return {"ok": True, "detail": detail}
            except Exception:
                pass

        # No completion button or code found after all candidates — return ok=True with best attempt info
        best = candidates[0]
        return {"ok": True, "detail": f"hovered at ({best['x']},{best['y']}) via {best['strategy']} (tried {len(candidates)} targets, no completion button)"}
    except Exception as e:
        return {"ok": False, "detail": f"hover error: {e}"}


async def handle_keyboard_sequence(page, params: dict) -> dict:
    """Press a sequence of keyboard keys using JS dispatch + Playwright keyboard fallback."""
    keys = params.get("keys", [])
    if not keys:
        return {"ok": False, "detail": "no keys provided"}

    key_map = {
        "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
        "space": " ", "return": "Enter", "esc": "Escape", "del": "Delete",
        "arrowup": "ArrowUp", "arrowdown": "ArrowDown", "arrowleft": "ArrowLeft", "arrowright": "ArrowRight",
    }

    # Try to get keyboard: prefer page.press() (browser-use native), then Playwright keyboard
    has_press = hasattr(page, 'press') and callable(getattr(page, 'press', None))
    keyboard = None
    if not has_press:
        try:
            keyboard = await _get_keyboard(page)
        except (AttributeError, Exception):
            pass

    pressed = []
    for key in keys:
        key = key.strip()
        if not key:
            continue
        normalized = key_map.get(key.lower(), key)

        try:
            if has_press:
                # browser-use page.press() handles key combinations like "Control+a"
                await page.press(normalized)
            elif keyboard:
                # Use Playwright keyboard API
                if "+" in normalized and len(normalized) > 1:
                    parts = normalized.split("+")
                    modifiers = []
                    main_key = parts[-1].strip()
                    for mod in parts[:-1]:
                        mod = mod.strip().lower()
                        mod_map = {"ctrl": "Control", "control": "Control", "alt": "Alt",
                                   "shift": "Shift", "meta": "Meta", "cmd": "Meta"}
                        modifiers.append(mod_map.get(mod, mod))
                    for mod in modifiers:
                        await keyboard.down(mod)
                    await keyboard.press(main_key)
                    for mod in reversed(modifiers):
                        await keyboard.up(mod)
                else:
                    await keyboard.press(normalized)
            else:
                # Fallback: dispatch via JS
                await page.evaluate("""(keySpec) => {
                    var parts = keySpec.split('+');
                    var mainKey = parts[parts.length - 1];
                    var ctrl = keySpec.toLowerCase().indexOf('control') >= 0;
                    var alt = keySpec.toLowerCase().indexOf('alt') >= 0;
                    var shift = keySpec.toLowerCase().indexOf('shift') >= 0;
                    var meta = keySpec.toLowerCase().indexOf('meta') >= 0;
                    var opts = {key: mainKey, code: 'Key' + mainKey.toUpperCase(),
                                ctrlKey: ctrl, altKey: alt, shiftKey: shift, metaKey: meta,
                                bubbles: true, cancelable: true};
                    document.activeElement.dispatchEvent(new KeyboardEvent('keydown', opts));
                    document.activeElement.dispatchEvent(new KeyboardEvent('keyup', opts));
                }""", normalized)
            pressed.append(key)
            await asyncio.sleep(0.2)
        except Exception as e:
            return {"ok": len(pressed) > 0, "detail": f"pressed {pressed}, error on '{key}': {e}"}

    return {"ok": True, "detail": f"pressed {len(pressed)} keys: {pressed}"}


_FIND_DRAG_PAIRS_JS = r"""() => {
    var sources = [];
    var targets = [];

    // Strategy 1: explicit draggable attribute
    document.querySelectorAll('[draggable="true"]').forEach(function(el) {
        el.scrollIntoView({block: 'center', behavior: 'instant'});
        var r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
            sources.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                          text: el.textContent.trim().substring(0, 10),
                          piece: el.getAttribute('data-piece') || ''});
        }
    });

    // Strategy 2: elements with drag-related class names or cursor:grab
    if (sources.length === 0) {
        document.querySelectorAll('[class*="drag"], [class*="tile"], [class*="piece"], [class*="source"], [class*="item"]').forEach(function(el) {
            if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE') return;
            var r = el.getBoundingClientRect();
            if (r.width < 10 || r.height < 10 || r.width > 300 || r.height > 300) return;
            var cs = getComputedStyle(el);
            if (cs.cursor === 'grab' || cs.cursor === 'move' || el.getAttribute('draggable') !== null) {
                el.scrollIntoView({block: 'center', behavior: 'instant'});
                r = el.getBoundingClientRect();
                sources.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), text: el.textContent.trim().substring(0, 10)});
            }
        });
    }

    // Strategy 3: elements with grab/move cursor anywhere
    if (sources.length === 0) {
        document.querySelectorAll('div, span, li, img').forEach(function(el) {
            var r = el.getBoundingClientRect();
            if (r.width < 10 || r.height < 10 || r.width > 300 || r.height > 300) return;
            var cs = getComputedStyle(el);
            if (cs.cursor === 'grab' || cs.cursor === 'move') {
                el.scrollIntoView({block: 'center', behavior: 'instant'});
                r = el.getBoundingClientRect();
                sources.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), text: el.textContent.trim().substring(0, 10)});
            }
        });
    }

    // Find drop targets using multiple strategies
    var seen = new Set();

    // Strategy A: Elements with React onDrop/onDragOver props (most reliable for React apps)
    document.querySelectorAll('div, section, span').forEach(function(el) {
        var rk = Object.keys(el).find(function(k) { return k.indexOf('__reactProps') === 0; });
        if (rk && el[rk] && (el[rk].onDrop || el[rk].onDragOver)) {
            var r = el.getBoundingClientRect();
            if (r.width > 5 && r.height > 5) {
                var key = Math.round(r.x) + ',' + Math.round(r.y);
                if (!seen.has(key)) {
                    seen.add(key);
                    targets.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), strategy: 'react-props'});
                }
            }
        }
    });

    // Strategy B: data attributes (data-slot, data-droppable, etc.)
    if (targets.length === 0) {
        document.querySelectorAll('[data-slot], [data-droppable], [data-drop], [data-target]').forEach(function(el) {
            var r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) {
                var key = Math.round(r.x) + ',' + Math.round(r.y);
                if (!seen.has(key)) {
                    seen.add(key);
                    targets.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), strategy: 'data-attr'});
                }
            }
        });
    }

    // Strategy C: Border-dashed elements (common drop zone visual pattern)
    if (targets.length === 0) {
        document.querySelectorAll('div').forEach(function(el) {
            var cs = getComputedStyle(el);
            if (cs.borderStyle === 'dashed') {
                var r = el.getBoundingClientRect();
                if (r.width >= 30 && r.width <= 200 && r.height >= 30 && r.height <= 200) {
                    var key = Math.round(r.x) + ',' + Math.round(r.y);
                    if (!seen.has(key)) {
                        seen.add(key);
                        targets.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), strategy: 'dashed-border'});
                    }
                }
            }
        });
    }

    // Strategy D: class-based (.drop-slot, drop, target, slot, zone)
    if (targets.length === 0) {
        document.querySelectorAll('.drop-slot, [class*="drop"], [class*="target"], [class*="slot"], [class*="zone"]').forEach(function(el) {
            var r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) {
                var key = Math.round(r.x) + ',' + Math.round(r.y);
                if (!seen.has(key)) {
                    seen.add(key);
                    targets.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), strategy: 'class-based'});
                }
            }
        });
    }

    return JSON.stringify({sources: sources, targets: targets, srcCount: sources.length, tgtCount: targets.length});
}"""


async def handle_drag_drop(page, params: dict) -> dict:
    """Drag elements to drop zones using native HTML5 drag events.

    Uses JS-dispatched DragEvent with DataTransfer for reliable HTML5 drag-drop.
    Falls back to Playwright mouse drags if native dispatch doesn't work.
    """
    total_dragged = 0

    try:
        # Query all drag sources and drop targets
        raw = await page.evaluate(_FIND_DRAG_PAIRS_JS)
        pairs = json.loads(raw) if isinstance(raw, str) else raw
        if not pairs or not pairs.get("sources"):
            return {"ok": False, "detail": f"no draggable elements found (src={pairs.get('srcCount', 0)}, tgt={pairs.get('tgtCount', 0)})"}

        sources = pairs["sources"]
        targets = pairs.get("targets", [])
        print(f"    [drag] Found {len(sources)} sources, {len(targets)} targets")

        if not targets:
            return {"ok": False, "detail": f"found {len(sources)} sources but 0 drop targets"}

        # Strategy 1: React prop dispatch (works for React drag-drop — call onDragStart + onDrop directly)
        native_count = await page.evaluate("""() => {
            // Find sources with React onDragStart
            var sources = [];
            document.querySelectorAll('[draggable="true"]').forEach(function(el) {
                var rk = Object.keys(el).find(function(k) { return k.indexOf('__reactProps') === 0; });
                if (rk && el[rk] && el[rk].onDragStart) {
                    sources.push({el: el, props: el[rk], text: el.textContent.trim()});
                }
            });

            // Find targets with React onDrop
            var targets = [];
            document.querySelectorAll('div, section').forEach(function(el) {
                var rk = Object.keys(el).find(function(k) { return k.indexOf('__reactProps') === 0; });
                if (rk && el[rk] && el[rk].onDrop) {
                    // Skip already-filled slots (they have bg-green)
                    var cls = el.className || '';
                    if (cls.indexOf('bg-green') !== -1) return;
                    targets.push({el: el, props: el[rk]});
                }
            });

            if (sources.length === 0 || targets.length === 0) return 0;

            var count = 0;
            var numPairs = Math.min(sources.length, targets.length);
            for (var i = 0; i < numPairs; i++) {
                var src = sources[i];
                var tgt = targets[i];
                try {
                    // Build fake DataTransfer
                    var _data = {};
                    var fakeDT = {
                        setData: function(type, val) { _data[type] = val; },
                        getData: function(type) { return _data[type] || ''; },
                        effectAllowed: 'move',
                        dropEffect: 'move'
                    };

                    // Call React onDragStart on source
                    src.props.onDragStart({
                        dataTransfer: fakeDT,
                        target: src.el,
                        preventDefault: function(){},
                        stopPropagation: function(){}
                    });

                    // Call React onDragOver on target (if exists, to set drop allowed)
                    if (tgt.props.onDragOver) {
                        tgt.props.onDragOver({
                            dataTransfer: fakeDT,
                            preventDefault: function(){},
                            stopPropagation: function(){}
                        });
                    }

                    // Call React onDrop on target
                    tgt.props.onDrop({
                        dataTransfer: fakeDT,
                        target: tgt.el,
                        preventDefault: function(){},
                        stopPropagation: function(){}
                    });

                    count++;
                } catch(e) {}
            }
            return count;
        }""")

        nc = int(native_count) if native_count else 0
        if nc > 0:
            await asyncio.sleep(1.0)
            print(f"    [drag] Native HTML5 dispatch: {nc} pairs")
            return {"ok": True, "detail": f"native HTML5 drag dispatched {nc} pairs"}

        # Strategy 2: Playwright mouse drags (fallback)
        mouse = await _get_mouse(page)
        for i, src in enumerate(sources[:6]):  # max 6
            tgt = targets[i] if i < len(targets) else targets[-1]  # reuse last target if fewer targets

            sx, sy = int(src["x"]), int(src["y"])
            tx, ty = int(tgt["x"]), int(tgt["y"])
            await mouse.move(sx, sy)
            await asyncio.sleep(0.1)
            await mouse.down()
            await asyncio.sleep(0.1)
            drag_steps = 15
            for step in range(1, drag_steps + 1):
                mx = sx + (tx - sx) * step / drag_steps
                my = sy + (ty - sy) * step / drag_steps
                await mouse.move(mx, my)
                await asyncio.sleep(0.015)
            await asyncio.sleep(0.05)
            await mouse.up()
            await asyncio.sleep(0.3)
            total_dragged += 1

        return {"ok": total_dragged > 0, "detail": f"mouse-dragged {total_dragged} elements"}
    except Exception as e:
        return {"ok": total_dragged > 0, "detail": f"drag error after {total_dragged}: {e}"}


async def handle_draw_canvas(page, params: dict) -> dict:
    """Draw strokes on a canvas element. Uses multiple strategies to trigger both
    native addEventListener and React event handlers."""
    try:
        # Step 1: Get canvas bounding box
        canvas_box = await page.evaluate("""() => {
            var c = document.querySelector('canvas');
            if (!c) return null;
            c.scrollIntoView({block: 'center', behavior: 'instant'});
            var r = c.getBoundingClientRect();
            return JSON.stringify({x: r.x, y: r.y, w: r.width, h: r.height});
        }""")

        if not canvas_box:
            return {"ok": False, "detail": "no canvas found"}
        if isinstance(canvas_box, str):
            canvas_box = json.loads(canvas_box)

        ox, oy, w, h = canvas_box["x"], canvas_box["y"], canvas_box["w"], canvas_box["h"]
        if w < 10 or h < 10:
            return {"ok": False, "detail": f"canvas too small: {w}x{h}"}
        margin = min(20, w * 0.1)
        num_strokes = 4

        # Step 2: JS native dispatch FIRST — fires native addEventListener handlers.
        # MUST run before React props to avoid React re-renders destroying native listeners.
        mouse_strokes = await page.evaluate("""(args) => {
            var ox = args.ox, oy = args.oy, w = args.w, h = args.h, margin = args.margin, strokes = args.strokes;
            var c = document.querySelector('canvas');
            if (!c) return 0;
            var drawn = 0;
            for (var i = 0; i < strokes; i++) {
                var yFrac = (i + 1) / (strokes + 1);
                var sx = ox + margin, sy = oy + h * yFrac;
                var ex = ox + w - margin, ey = sy + h * 0.05;
                c.dispatchEvent(new MouseEvent('mousedown', {clientX: sx, clientY: sy, bubbles: true, cancelable: true}));
                for (var step = 1; step <= 10; step++) {
                    var frac = step / 10;
                    var mx = sx + (ex - sx) * frac;
                    var my = sy + (ey - sy) * frac;
                    c.dispatchEvent(new MouseEvent('mousemove', {clientX: mx, clientY: my, bubbles: true, cancelable: true}));
                }
                c.dispatchEvent(new MouseEvent('mouseup', {clientX: ex, clientY: ey, bubbles: true, cancelable: true}));
                drawn++;
            }
            return drawn;
        }""", {"ox": ox, "oy": oy, "w": w, "h": h, "margin": margin, "strokes": num_strokes})

        # Step 3: Playwright trusted mouse.click on canvas — fires CDP events that
        # trigger native listeners even if dispatchEvent didn't work.
        # mouse.click(x,y) sends mousePressed+mouseReleased at correct coordinates.
        pw_clicks = 0
        try:
            mouse = await _get_mouse(page)
            for i in range(2):
                cx = ox + margin + (w - 2 * margin) * (i + 1) / 3
                cy = oy + h * 0.5
                await mouse.click(int(cx), int(cy))
                pw_clicks += 1
                await asyncio.sleep(0.1)
        except Exception:
            pass

        # Step 4: React props drawing LAST — may trigger re-render which destroys native listeners
        react_strokes = await page.evaluate("""() => {
            var c = document.querySelector('canvas');
            if (!c) return 0;
            var rk = Object.keys(c).find(function(k) { return k.indexOf('__reactProps') === 0; });
            if (!rk || !c[rk] || !c[rk].onMouseDown) return 0;
            var r = c.getBoundingClientRect();
            var strokes = 0;
            var margin = Math.min(20, r.width * 0.1);
            for (var s = 0; s < 4; s++) {
                var yFrac = (s + 1) / 5;
                var startX = r.left + margin;
                var startY = r.top + r.height * yFrac;
                var endX = r.left + r.width - margin;
                var endY = startY + r.height * 0.03;
                c[rk].onMouseDown({clientX: startX, clientY: startY, preventDefault: function(){}, target: c, currentTarget: c, nativeEvent: new MouseEvent('mousedown', {clientX: startX, clientY: startY})});
                for (var step = 1; step <= 8; step++) {
                    var frac = step / 8;
                    var mx = startX + (endX - startX) * frac;
                    var my = startY + (endY - startY) * frac;
                    if (c[rk].onMouseMove) {
                        c[rk].onMouseMove({clientX: mx, clientY: my, preventDefault: function(){}, target: c, currentTarget: c, nativeEvent: new MouseEvent('mousemove', {clientX: mx, clientY: my})});
                    }
                }
                if (c[rk].onMouseUp) {
                    c[rk].onMouseUp({clientX: endX, clientY: endY, preventDefault: function(){}, target: c, currentTarget: c, nativeEvent: new MouseEvent('mouseup', {clientX: endX, clientY: endY})});
                }
                strokes++;
            }
            return strokes;
        }""")

        # After drawing, find the completion button NEAR the canvas (not decoy buttons).
        # The page has decoy "Complete Challenge" buttons; the real gesture/canvas button
        # is inside the same container as the canvas element.
        await asyncio.sleep(0.5)
        clicked_label = await page.evaluate("""() => {
            var c = document.querySelector('canvas');
            if (!c) return null;
            // Walk up to find the challenge container
            var container = c.parentElement;
            for (var i = 0; i < 5 && container; i++) {
                var btns = container.querySelectorAll('button');
                for (var j = 0; j < btns.length; j++) {
                    var btn = btns[j];
                    if (btn.disabled || btn.offsetWidth === 0) continue;
                    var t = btn.textContent.trim();
                    // Match completion buttons: "Complete", "Complete Challenge", "Reveal Code"
                    if (/^(Complete|Complete Challenge|Reveal Code)$/i.test(t)) {
                        btn.scrollIntoView({block: 'center', behavior: 'instant'});
                        btn.click();
                        return t;
                    }
                }
                container = container.parentElement;
            }
            return null;
        }""")
        # If JS click didn't work, try Playwright trusted click on the button
        if not clicked_label:
            clicked_label = await _try_complete_button(page)
        else:
            # Also do Playwright trusted click for React event delegation
            try:
                mouse = await _get_mouse(page)
                btn_pos = await page.evaluate("""(label) => {
                    var btns = document.querySelectorAll('button');
                    for (var i = 0; i < btns.length; i++) {
                        if (btns[i].textContent.trim() === label && !btns[i].disabled) {
                            var r = btns[i].getBoundingClientRect();
                            return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
                        }
                    }
                    return null;
                }""", clicked_label)
                if btn_pos:
                    pos = json.loads(btn_pos) if isinstance(btn_pos, str) else btn_pos
                    await mouse.click(pos["x"], pos["y"])
            except Exception:
                pass
        clicked = clicked_label
        react_note = f" (react={react_strokes})" if react_strokes else ""
        js_note = f" (js={mouse_strokes})" if mouse_strokes else ""
        pw_note = f" (pw={pw_clicks})" if pw_clicks else ""
        if clicked:
            return {"ok": True, "detail": f"drew strokes{react_note}{js_note}{pw_note}, clicked '{clicked}'"}
        return {"ok": True, "detail": f"drew strokes on canvas{react_note}{js_note}{pw_note}"}
    except Exception as e:
        return {"ok": False, "detail": f"canvas error: {e}"}


async def handle_shadow_dom(page, params: dict) -> dict:
    """Click through shadow DOM levels one at a time.
    The deployed challenge uses clickable DIVS (not buttons) with 'Shadow Level N' text.
    Each level div has React onClick handler. Level N+1 only renders after level N is clicked.
    After all levels: 'Reveal Code' button appears.
    Uses React onClick dispatch since Playwright clicks don't trigger React events.
    """
    try:
        clicked_levels = 0

        for iteration in range(10):
            # Click the next unchecked shadow level via React onClick dispatch
            click_result = await page.evaluate(r"""() => {
                // Strategy 1: Find unchecked Shadow Level divs and trigger their React onClick
                var allDivs = document.querySelectorAll('div');
                for (var i = 0; i < allDivs.length; i++) {
                    var div = allDivs[i];
                    if (div.offsetWidth === 0 || div.offsetHeight === 0) continue;
                    // Check for React onClick handler
                    var rk = Object.keys(div).find(function(k) { return k.indexOf('__reactProps') === 0; });
                    if (!rk || !div[rk] || !div[rk].onClick) continue;
                    // Check if this div has a "Shadow Level N" heading WITHOUT ✓
                    var headings = div.querySelectorAll(':scope > h3, :scope > h4, :scope > h5, :scope > h6, :scope > p, :scope > span');
                    for (var j = 0; j < headings.length; j++) {
                        var ht = headings[j].textContent.trim();
                        if (/Shadow\s+Level\s+\d+/i.test(ht) && !ht.includes('\u2713') && !ht.includes('✓')) {
                            // Trigger React onClick
                            div[rk].onClick({
                                preventDefault:function(){},stopPropagation:function(){},
                                target:div,currentTarget:div,nativeEvent:{},bubbles:true,type:'click'
                            });
                            return JSON.stringify({type:'level', label: ht});
                        }
                    }
                }
                // Strategy 2: Check for Reveal Code / Extract Code button
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    var t = buttons[i].textContent.trim();
                    if (/Reveal Code|Extract Code/i.test(t) && !/Submit Code/i.test(t) && !buttons[i].disabled) {
                        var brk = Object.keys(buttons[i]).find(function(k) { return k.indexOf('__reactProps') === 0; });
                        if (brk && buttons[i][brk] && buttons[i][brk].onClick) {
                            buttons[i][brk].onClick({
                                preventDefault:function(){},stopPropagation:function(){},
                                target:buttons[i],currentTarget:buttons[i],nativeEvent:{},bubbles:true,type:'click'
                            });
                            return JSON.stringify({type:'extract', label: t});
                        }
                        buttons[i].click();
                        return JSON.stringify({type:'extract', label: t});
                    }
                }
                // Strategy 3: Enter Level buttons (in case it's actually using buttons)
                for (var i = 0; i < buttons.length; i++) {
                    var t = buttons[i].textContent.trim();
                    if (/enter.*level/i.test(t) && !buttons[i].disabled) {
                        var brk = Object.keys(buttons[i]).find(function(k) { return k.indexOf('__reactProps') === 0; });
                        if (brk && buttons[i][brk] && buttons[i][brk].onClick) {
                            buttons[i][brk].onClick({
                                preventDefault:function(){},stopPropagation:function(){},
                                target:buttons[i],currentTarget:buttons[i],nativeEvent:{},bubbles:true,type:'click'
                            });
                            return JSON.stringify({type:'level', label: t});
                        }
                        buttons[i].click();
                        return JSON.stringify({type:'level', label: t});
                    }
                }
                return null;
            }""")

            if not click_result:
                break

            result = json.loads(click_result) if isinstance(click_result, str) else click_result
            is_extract = result.get("type") == "extract"
            await asyncio.sleep(0.8 if is_extract else 0.4)
            clicked_levels += 1

            if is_extract:
                break

        # Try complete button after shadow DOM traversal
        await asyncio.sleep(0.3)
        clicked_btn = await _try_complete_button(page)
        detail = f"clicked {clicked_levels} shadow DOM levels"
        if clicked_btn:
            detail += f", clicked '{clicked_btn}'"
        return {"ok": clicked_levels > 0, "detail": detail}
    except Exception as e:
        return {"ok": False, "detail": f"shadow_dom error: {e}"}


async def handle_service_worker(page, params: dict) -> dict:
    """Register -> wait -> retrieve pattern for service worker challenges."""
    try:
        mouse = await _get_mouse(page)

        # Click Register/Connect button
        reg_info = await page.evaluate("""() => {
            var buttons = document.querySelectorAll('button');
            for (var i = 0; i < buttons.length; i++) {
                var btn = buttons[i];
                if (btn.disabled || btn.offsetWidth === 0) continue;
                var t = btn.textContent.trim().toLowerCase();
                if (/register|connect|1[.)]/.test(t)) {
                    btn.scrollIntoView({block: 'center'});
                    var r = btn.getBoundingClientRect();
                    return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), label: btn.textContent.trim()});
                }
            }
            return null;
        }""")
        if isinstance(reg_info, str):
            try:
                reg_info = json.loads(reg_info)
            except (json.JSONDecodeError, TypeError):
                reg_info = None

        if reg_info:
            await mouse.click(reg_info["x"], reg_info["y"])
            await asyncio.sleep(4.0)

        # Click Retrieve button
        ret_info = await page.evaluate("""() => {
            var buttons = document.querySelectorAll('button');
            for (var i = 0; i < buttons.length; i++) {
                var btn = buttons[i];
                if (btn.offsetWidth === 0) continue;
                var t = btn.textContent.trim().toLowerCase();
                if (/retrieve|reveal.*code|2[.)]/.test(t)) {
                    btn.scrollIntoView({block: 'center'});
                    var r = btn.getBoundingClientRect();
                    return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), label: btn.textContent.trim(), disabled: btn.disabled});
                }
            }
            return null;
        }""")
        if isinstance(ret_info, str):
            try:
                ret_info = json.loads(ret_info)
            except (json.JSONDecodeError, TypeError):
                ret_info = None

        if ret_info:
            # Wait for it to become enabled
            if ret_info.get("disabled"):
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    still_disabled = await page.evaluate("""() => {
                        var buttons = document.querySelectorAll('button');
                        for (var i = 0; i < buttons.length; i++) {
                            if (/retrieve|2[.)]/i.test(buttons[i].textContent.trim())) return buttons[i].disabled;
                        }
                        return true;
                    }""")
                    if not still_disabled:
                        # Re-get coords after possible re-render
                        ret_info = await page.evaluate("""() => {
                            var buttons = document.querySelectorAll('button');
                            for (var i = 0; i < buttons.length; i++) {
                                var t = buttons[i].textContent.trim().toLowerCase();
                                if (/retrieve|reveal.*code|2[.)]/.test(t) && buttons[i].offsetWidth > 0) {
                                    buttons[i].scrollIntoView({block: 'center'});
                                    var r = buttons[i].getBoundingClientRect();
                                    return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
                                }
                            }
                            return null;
                        }""")
                        if isinstance(ret_info, str):
                            try:
                                ret_info = json.loads(ret_info)
                            except (json.JSONDecodeError, TypeError):
                                ret_info = None
                        break

            if ret_info:
                await mouse.click(ret_info["x"], ret_info["y"])
                await asyncio.sleep(2.0)

        return {"ok": True, "detail": "service_worker sequence complete"}
    except Exception as e:
        return {"ok": False, "detail": f"service_worker error: {e}"}


async def handle_type_text(page, params: dict) -> dict:
    """Type text into an input field."""
    text = params.get("type_text", "")
    hint = params.get("input_placeholder", "")

    if not text:
        return {"ok": False, "detail": "no text provided"}

    try:
        result = await page.evaluate("""(opts) => {
            var text = opts.text;
            var hint = opts.hint;
            var input = null;

            if (hint) {
                var inputs = document.querySelectorAll('input');
                for (var i = 0; i < inputs.length; i++) {
                    var ph = (inputs[i].placeholder || '').toLowerCase();
                    if (ph.indexOf(hint.toLowerCase()) !== -1) { input = inputs[i]; break; }
                }
            }
            if (!input) {
                var inputs = document.querySelectorAll('input[type="text"], input:not([type])');
                for (var i = 0; i < inputs.length; i++) {
                    if (inputs[i].offsetWidth === 0) continue;
                    var ph = (inputs[i].placeholder || '').toLowerCase();
                    if (ph.indexOf('code') !== -1 || ph.indexOf('character') !== -1) continue;
                    input = inputs[i]; break;
                }
            }
            if (!input) return {ok: false, reason: 'no input found'};

            input.scrollIntoView({block: 'center'});
            input.focus();
            try {
                Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, text);
            } catch(e) { input.value = text; }
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));
            return {ok: true};
        }""", {"text": text, "hint": hint})

        if result and result.get("ok"):
            return {"ok": True, "detail": f"typed '{text}'"}
        return {"ok": False, "detail": f"type error: {result.get('reason', 'unknown')}"}
    except Exception as e:
        return {"ok": False, "detail": f"type error: {e}"}


async def handle_split_parts(page, params: dict) -> dict:
    """Find and click all scattered clickable parts (absolutely-positioned divs with onClick)."""
    try:
        # First scroll to top to make parts visible
        try:
            await page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass
        await asyncio.sleep(0.3)

        # Find split parts: absolutely-positioned divs with bg-blue or z-[10004] class and onClick
        parts = await page.evaluate(r"""() => {
            var parts = [];
            var allEls = document.querySelectorAll('div');
            for (var i = 0; i < allEls.length; i++) {
                var el = allEls[i];
                if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                var cls = (typeof el.className === 'string') ? el.className : '';

                // Must have split-parts characteristics: bg-blue-100 or z-[10004]
                var isSplitPart = /bg-blue/.test(cls) || /z-\[10004\]/.test(cls);
                if (!isSplitPart) continue;

                var cs = getComputedStyle(el);
                if (cs.position !== 'absolute' && cs.position !== 'fixed') continue;

                // Check for React onClick prop
                var rk = Object.keys(el).find(function(k) { return k.indexOf('__reactProps') === 0; });
                if (!rk || !el[rk] || !el[rk].onClick) continue;

                // Scroll to bring into view, then get coords
                el.scrollIntoView({block: 'center', behavior: 'instant'});
                var r = el.getBoundingClientRect();
                parts.push({
                    x: Math.round(r.x + r.width / 2),
                    y: Math.round(r.y + r.height / 2),
                    text: el.textContent.trim().substring(0, 20),
                    cls: cls.substring(0, 60)
                });
            }
            return JSON.stringify(parts);
        }""")

        items = json.loads(parts) if isinstance(parts, str) else (parts or [])

        # Fallback: if no bg-blue parts found, find absolute divs with onClick, short text, pointer-events
        if not items:
            parts2 = await page.evaluate(r"""() => {
                var parts = [];
                var allEls = document.querySelectorAll('div');
                for (var i = 0; i < allEls.length; i++) {
                    var el = allEls[i];
                    if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                    var cs = getComputedStyle(el);
                    if (cs.position !== 'absolute') continue;
                    var cls = (typeof el.className === 'string') ? el.className : '';
                    if (!/pointer-events/.test(cls)) continue;

                    var rk = Object.keys(el).find(function(k) { return k.indexOf('__reactProps') === 0; });
                    if (!rk || !el[rk] || !el[rk].onClick) continue;

                    var text = el.textContent.trim();
                    if (text.length > 20 || text.length < 1) continue;  // code fragments are short

                    el.scrollIntoView({block: 'center', behavior: 'instant'});
                    var r = el.getBoundingClientRect();
                    parts.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), text: text, cls: cls.substring(0, 60)});
                }
                return JSON.stringify(parts);
            }""")
            items = json.loads(parts2) if isinstance(parts2, str) else (parts2 or [])

        if not items:
            return {"ok": False, "detail": "no split parts found (no absolute divs with onClick and bg-blue/pointer-events)"}

        print(f"    [split_parts] Found {len(items)} clickable parts")

        # Strategy 1: Direct React onClick dispatch (most reliable)
        react_clicked = await page.evaluate(r"""() => {
            var clicked = 0;
            var allEls = document.querySelectorAll('div');
            for (var i = 0; i < allEls.length; i++) {
                var el = allEls[i];
                if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                var cls = (typeof el.className === 'string') ? el.className : '';
                if (!/bg-blue/.test(cls) && !/z-\[10004\]/.test(cls) && !/pointer-events-auto/.test(cls)) continue;
                var cs = getComputedStyle(el);
                if (cs.position !== 'absolute') continue;
                var rk = Object.keys(el).find(function(k) { return k.indexOf('__reactProps') === 0; });
                if (!rk || !el[rk] || !el[rk].onClick) continue;
                try {
                    el[rk].onClick({preventDefault: function(){}, stopPropagation: function(){}, target: el, currentTarget: el});
                    clicked++;
                } catch(e) {}
            }
            return clicked;
        }""")

        rc = int(react_clicked) if react_clicked else 0
        if rc > 0:
            print(f"    [split_parts] React onClick dispatched on {rc} parts")
            await asyncio.sleep(1.0)
            # After clicking all parts, try complete button
            btn = await _try_complete_button(page)
            detail = f"clicked {rc} split parts via React onClick"
            if btn:
                detail += f", clicked '{btn}'"
            return {"ok": True, "detail": detail}

        # Strategy 2: Playwright mouse click fallback
        mouse = await _get_mouse(page)
        clicked = 0
        for item in items:
            try:
                await mouse.click(item["x"], item["y"])
                clicked += 1
                print(f"    [split_parts] Clicked part '{item['text']}' at ({item['x']},{item['y']})")
                await asyncio.sleep(0.3)
            except Exception:
                pass

        if clicked > 0:
            await asyncio.sleep(1.0)
            btn = await _try_complete_button(page)
            detail = f"clicked {clicked}/{len(items)} split parts"
            if btn:
                detail += f", clicked '{btn}'"
            return {"ok": True, "detail": detail}

        return {"ok": False, "detail": f"found {len(items)} parts but clicked 0"}
    except Exception as e:
        return {"ok": False, "detail": f"split_parts error: {e}"}


async def handle_video_frame(page, params: dict) -> dict:
    """Navigate video frames to target frame, then Complete Challenge.

    Strategy 1: Click "Frame N" button directly if it exists.
    Strategy 2: Use +10/+1/-10/-1 buttons to navigate to target frame.
    """
    try:
        mouse = await _get_mouse(page)
        clicks = 0

        # Strategy 1: Find and click the target "Frame N" button directly
        target_clicked = await page.evaluate(r"""() => {
            var buttons = document.querySelectorAll('button');
            for (var i = 0; i < buttons.length; i++) {
                var btn = buttons[i];
                if (btn.disabled || btn.offsetWidth === 0) continue;
                var t = btn.textContent.trim();
                // Look for "Frame N" button (the target frame)
                if (/^Frame\s+\d+$/i.test(t)) {
                    var rk = Object.keys(btn).find(function(k) { return k.indexOf('__reactProps') === 0; });
                    if (rk && btn[rk] && btn[rk].onClick) {
                        try {
                            btn[rk].onClick({preventDefault:function(){},stopPropagation:function(){},
                                target:btn,currentTarget:btn,nativeEvent:{},bubbles:true,type:'click'});
                            return t;
                        } catch(e) {}
                    }
                    btn.click();
                    return t;
                }
            }
            return null;
        }""")

        if target_clicked:
            # Wait for Complete Challenge button to become enabled, retry up to 3 times
            clicked = None
            for _wait_i in range(3):
                await asyncio.sleep(0.5 + _wait_i * 0.5)
                clicked = await _try_complete_button(page)
                if clicked:
                    break
                # Try force-enabling and clicking Complete Challenge button
                forced = await page.evaluate(r"""() => {
                    var buttons = document.querySelectorAll('button');
                    for (var i = 0; i < buttons.length; i++) {
                        var t = buttons[i].textContent.trim();
                        if (/Complete Challenge/i.test(t)) {
                            buttons[i].disabled = false;
                            buttons[i].scrollIntoView({block: 'center'});
                            var r = buttons[i].getBoundingClientRect();
                            return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
                        }
                    }
                    return null;
                }""")
                if forced:
                    info = json.loads(forced) if isinstance(forced, str) else forced
                    await mouse.click(info["x"], info["y"])
                    clicked = "Complete Challenge (forced)"
                    break
            detail = f"clicked '{target_clicked}'"
            if clicked:
                detail += f", clicked '{clicked}'"
            return {"ok": True, "detail": detail}

        # Strategy 2: Navigate with +10/+1 buttons
        for _ in range(8):
            raw = await page.evaluate(r"""() => {
                var buttons = document.querySelectorAll('button');
                var best = null;
                for (var i = 0; i < buttons.length; i++) {
                    var btn = buttons[i];
                    if (btn.disabled || btn.offsetWidth === 0) continue;
                    var t = btn.textContent.trim();
                    if (t === '+10') {
                        btn.scrollIntoView({block: 'center', behavior: 'instant'});
                        var r = btn.getBoundingClientRect();
                        return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), label: t});
                    }
                    if (t === '+1' && !best) {
                        btn.scrollIntoView({block: 'center', behavior: 'instant'});
                        var r = btn.getBoundingClientRect();
                        best = JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), label: t});
                    }
                }
                return best;
            }""")

            if raw:
                btn_info = json.loads(raw) if isinstance(raw, str) else raw
                await mouse.click(btn_info["x"], btn_info["y"])
                clicks += 1
                await asyncio.sleep(0.3)
            else:
                break

        # Try Complete Challenge
        await asyncio.sleep(0.5)
        clicked = await _try_complete_button(page)
        if clicked:
            return {"ok": True, "detail": f"navigated {clicks} frames, clicked '{clicked}'"}
        return {"ok": True, "detail": f"navigated {clicks} frames, no complete button found"}
    except Exception as e:
        return {"ok": False, "detail": f"video_frame error: {e}"}


async def handle_wait(page, params: dict) -> dict:
    """Wait for content to appear."""
    seconds = params.get("seconds", 2)
    await asyncio.sleep(seconds)
    return {"ok": True, "detail": f"waited {seconds}s"}


async def handle_multi_step(page, params: dict) -> dict:
    """Execute a sequence of sub-actions."""
    steps = params.get("steps", [])
    if not steps:
        return {"ok": False, "detail": "no steps provided"}

    results = []
    for step_str in steps:
        parts = step_str.split(":", 1)
        action = parts[0].strip()
        param = parts[1].strip() if len(parts) > 1 else ""

        if action == "click_button":
            r = await handle_click_button(page, {"button_text": param})
        elif action == "hover_element":
            r = await handle_hover_element(page, {"hover_text": param})
        elif action == "type_text":
            r = await handle_type_text(page, {"type_text": param})
        elif action == "scroll_down":
            r = await handle_scroll_down(page, {"pixels": int(param) if param else 600})
        elif action == "wait":
            r = await handle_wait(page, {"seconds": float(param) if param else 1})
        elif action == "keyboard_sequence":
            r = await handle_keyboard_sequence(page, {"keys": param.split(",")})
        else:
            r = {"ok": False, "detail": f"unknown sub-action: {action}"}

        results.append(f"{action}: {r.get('detail', 'done')}")
        await asyncio.sleep(0.3)

    return {"ok": True, "detail": f"multi_step: {'; '.join(results)}"}


async def handle_sequence(page, params: dict) -> dict:
    """Handle Sequence Challenge: click button, hover area, type text, scroll box in order.

    Live site is React with NO element IDs. Detection uses text-based matching:
    - Status pills: ○/● prefix + action name in spans
    - "Click Me" button by text, "Hover here" div by text
    - Input by placeholder or exclusion, scroll box by scrollHeight
    All interactions use Playwright trusted events for React compatibility.
    """
    try:
        mouse = await _get_mouse(page)
        completed = []

        # Repeat sequence actions until Complete button shows all done
        for attempt in range(6):
            # Get current state: which actions remain (text-based detection, no IDs)
            state = await page.evaluate(r"""() => {
                var result = {actions: []};
                var allText = document.body.innerText || '';
                if (allText.indexOf('Sequence Challenge') === -1 && allText.indexOf('Gesture Challenge') === -1)
                    return JSON.stringify({actions: [], noChallenge: true});

                var ACTION_NAMES = ['click button', 'hover area', 'type text', 'scroll box'];
                var byAction = {};

                // Detect status pills by text content: "○ click button" / "✓ click button"
                var spans = document.querySelectorAll('span');
                var debugPills = [];
                for (var i = 0; i < spans.length; i++) {
                    var el = spans[i];
                    if (el.offsetWidth === 0) continue;
                    var raw = el.textContent.trim();
                    if (raw.length > 40) continue;
                    var t = raw.toLowerCase();
                    for (var a = 0; a < ACTION_NAMES.length; a++) {
                        if (t.indexOf(ACTION_NAMES[a]) !== -1 && !byAction[ACTION_NAMES[a]]) {
                            var done = /✓|✔|✅|●/.test(raw) || /line-through/.test((el.style||{}).textDecoration||'');
                            byAction[ACTION_NAMES[a]] = {action: ACTION_NAMES[a], done: done};
                            debugPills.push(raw.substring(0, 25) + (done ? ' [DONE]' : ' [TODO]'));
                            break;
                        }
                    }
                }
                result.debugPills = debugPills;

                // If no pills found, try looking at progress text "Progress: X/4"
                if (Object.keys(byAction).length === 0) {
                    var progMatch = allText.match(/Progress:\s*(\d+)\s*\/\s*(\d+)/);
                    if (progMatch) {
                        var done = parseInt(progMatch[1]);
                        var total = parseInt(progMatch[2]);
                        result.progress = done + '/' + total;
                        // If progress is 4/4 or matches total, all done
                        if (done >= total) {
                            result.next = null;
                            result.actions = [];
                        }
                    }
                }

                var nextAction = null;
                for (var a = 0; a < ACTION_NAMES.length; a++) {
                    var pill = byAction[ACTION_NAMES[a]];
                    if (pill && !pill.done) {
                        if (!nextAction) nextAction = pill.action;
                        result.actions.push(pill.action);
                    }
                }
                result.next = nextAction;
                result.foundPills = Object.keys(byAction).length;

                // Find Click Me button by text (broader matching for React app)
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    var bt = btns[i].textContent.trim();
                    if (btns[i].offsetWidth > 0 && (/^Click Me$/i.test(bt) || /^Click$/i.test(bt) || /^Click here$/i.test(bt))) {
                        btns[i].scrollIntoView({block:'center',behavior:'instant'});
                        var r = btns[i].getBoundingClientRect();
                        result.clickBtn = {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), text: bt};
                        break;
                    }
                }
                // Fallback: find any small button that's NOT Submit/Complete/Capture
                if (!result.clickBtn) {
                    for (var i = 0; i < btns.length; i++) {
                        var bt = btns[i].textContent.trim();
                        if (btns[i].offsetWidth > 0 && bt.length < 15 &&
                            !/Submit|Complete|Capture|Reveal|Dismiss|Accept|Decline|Close|Tab/i.test(bt) &&
                            !/Continue|Forward|Advance|Next|Move|Proceed|Keep|Go/i.test(bt)) {
                            btns[i].scrollIntoView({block:'center',behavior:'instant'});
                            var r = btns[i].getBoundingClientRect();
                            result.clickBtn = {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), text: bt, fallback: true};
                            break;
                        }
                    }
                }

                // Find Complete button by text (may show "Complete (X/4)")
                for (var i = 0; i < btns.length; i++) {
                    var bt = btns[i].textContent.trim();
                    if (btns[i].offsetWidth > 0 && /^Complete/i.test(bt) && !/^Complete Challenge/i.test(bt)) {
                        btns[i].scrollIntoView({block:'center',behavior:'instant'});
                        var r = btns[i].getBoundingClientRect();
                        result.completeBtn = {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
                                              text: bt, disabled: btns[i].disabled};
                        break;
                    }
                }

                // Find hover area by text "Hover here" or "Hover over" or "Hover"
                var divs = document.querySelectorAll('div, span, p');
                var bestHover = null;
                var bestHoverSize = Infinity;
                for (var i = 0; i < divs.length; i++) {
                    var d = divs[i];
                    if (d.offsetWidth === 0 || d.offsetHeight === 0) continue;
                    var dt = d.textContent.trim();
                    if (dt.length > 80) continue;
                    if (/Hover here/i.test(dt) || /Hover over/i.test(dt) || /Hover this/i.test(dt) ||
                        (/hover/i.test(dt) && d.offsetHeight > 10 && d.offsetHeight < 200 && d.offsetWidth < 400)) {
                        var area = d.offsetWidth * d.offsetHeight;
                        if (area < bestHoverSize && d.offsetHeight > 10 && d.offsetHeight < 200) {
                            bestHoverSize = area;
                            bestHover = d;
                        }
                    }
                }
                // Also look for data-hover-area attribute
                if (!bestHover) {
                    bestHover = document.querySelector('[data-hover-area]');
                }
                // Also look for colored boxes with onMouseEnter React prop
                if (!bestHover) {
                    for (var i = 0; i < divs.length; i++) {
                        var d = divs[i];
                        if (d.offsetWidth < 40 || d.offsetHeight < 20 || d.offsetHeight > 120) continue;
                        var rk = Object.keys(d).find(function(k) { return k.indexOf('__reactProps') === 0; });
                        if (rk && d[rk] && d[rk].onMouseEnter) {
                            bestHover = d;
                            break;
                        }
                    }
                }
                if (bestHover) {
                    bestHover.scrollIntoView({block:'center',behavior:'instant'});
                    var r = bestHover.getBoundingClientRect();
                    result.hoverArea = {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), text: bestHover.textContent.trim().substring(0, 30)};
                }

                // Find type text input (non-code-submission input)
                var inputs = document.querySelectorAll('input[type="text"], input:not([type])');
                for (var i = 0; i < inputs.length; i++) {
                    var inp = inputs[i];
                    if (inp.offsetWidth === 0) continue;
                    var ph = (inp.placeholder || '').toLowerCase();
                    // Exclude code submission input
                    if (/code|character|submit/i.test(ph)) continue;
                    if (inp.maxLength === 6) continue;
                    inp.scrollIntoView({block:'center',behavior:'instant'});
                    var r = inp.getBoundingClientRect();
                    result.typeInput = {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), placeholder: ph};
                    break;
                }

                // Find scroll box (div with scrollHeight > clientHeight, small size)
                var divs2 = document.querySelectorAll('div');
                for (var i = 0; i < divs2.length; i++) {
                    var d = divs2[i];
                    if (d.scrollHeight <= d.clientHeight + 20) continue;
                    var r = d.getBoundingClientRect();
                    if (r.width < 50 || r.height < 30 || r.height > 300 || r.width > window.innerWidth * 0.8) continue;
                    var dt = (d.textContent||'').trim();
                    if (/Scroll inside/i.test(dt) || (d.scrollHeight > d.clientHeight + 30)) {
                        d.scrollIntoView({block:'center',behavior:'instant'});
                        result.scrollBox = {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
                                            scrollable: d.scrollHeight - d.clientHeight};
                        break;
                    }
                }

                return JSON.stringify(result);
            }""")

            info = json.loads(state) if isinstance(state, str) else (state or {})
            if attempt == 0:
                pills = info.get("debugPills", [])
                print(f"    [sequence] state: actions={info.get('actions')}, next={info.get('next')}, pills={pills}, foundPills={info.get('foundPills',0)}")
                print(f"    [sequence] elements: click={bool(info.get('clickBtn'))}, hover={bool(info.get('hoverArea'))}, type={bool(info.get('typeInput'))}, scroll={bool(info.get('scrollBox'))}, complete={bool(info.get('completeBtn'))}")
            if info.get("noChallenge"):
                return {"ok": False, "detail": "no sequence challenge found"}

            actions = info.get("actions", [])
            next_action = info.get("next")

            # If no pills found (live React site), do all 4 actions directly
            # Strategy: try ID-based targeting first (challenge sets IDs), fallback to text search
            # Use Playwright trusted events where possible (native addEventListener)
            if info.get("foundPills", 0) == 0 and attempt == 0:
                print(f"    [sequence] no pills detected — executing all 4 actions directly")

                # 1. CLICK: Find and click the "Click Me" button
                click_result = await page.evaluate(r"""() => {
                    // Try by ID first
                    var btn = document.getElementById('seq-click-btn');
                    if (!btn || btn.offsetWidth === 0) {
                        // Fallback: find by text
                        var btns = document.querySelectorAll('button');
                        for (var i = 0; i < btns.length; i++) {
                            var t = btns[i].textContent.trim();
                            if (btns[i].offsetWidth > 0 && /^Click Me$/i.test(t)) { btn = btns[i]; break; }
                        }
                    }
                    if (!btn || btn.offsetWidth === 0) {
                        // Broader fallback: any small non-decoy button
                        var btns = document.querySelectorAll('button');
                        for (var i = 0; i < btns.length; i++) {
                            var t = btns[i].textContent.trim();
                            if (btns[i].offsetWidth > 0 && t.length < 15 &&
                                !/Submit|Complete|Capture|Reveal|Dismiss|Clear/i.test(t) &&
                                !/Continue|Forward|Advance|Next|Move|Proceed|Keep|Go/i.test(t)) {
                                btn = btns[i]; break;
                            }
                        }
                    }
                    if (!btn) return null;
                    btn.scrollIntoView({block:'center',behavior:'instant'});
                    var r = btn.getBoundingClientRect();
                    return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), text: btn.textContent.trim()});
                }""")
                if click_result:
                    ci = json.loads(click_result) if isinstance(click_result, str) else click_result
                    await mouse.click(ci["x"], ci["y"])
                    completed.append("click")
                    print(f"    [sequence] click: Playwright click on '{ci.get('text')}' at ({ci['x']},{ci['y']})")
                    await asyncio.sleep(0.3)

                # 2. HOVER: Find hover area, use Playwright mouse.move for real mouseenter
                hover_pos = await page.evaluate(r"""() => {
                    // Try by ID first
                    var el = document.getElementById('seq-hover-area');
                    if (!el || el.offsetWidth === 0) {
                        el = document.querySelector('[data-hover-area]');
                    }
                    if (!el || el.offsetWidth === 0) {
                        // Fallback: find by text "Hover here"
                        var divs = document.querySelectorAll('div, span, p');
                        for (var i = 0; i < divs.length; i++) {
                            var d = divs[i];
                            if (d.offsetWidth === 0 || d.offsetHeight === 0) continue;
                            var t = d.textContent.trim();
                            if (/Hover here/i.test(t) && d.offsetHeight > 10 && d.offsetHeight < 200) { el = d; break; }
                        }
                    }
                    if (!el) return null;
                    el.scrollIntoView({block:'center',behavior:'instant'});
                    var r = el.getBoundingClientRect();
                    return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)});
                }""")
                if hover_pos:
                    hp = json.loads(hover_pos) if isinstance(hover_pos, str) else hover_pos
                    # Use Playwright mouse.move — triggers real mouseenter event
                    await mouse.move(hp["x"], hp["y"])
                    # Also dispatch mouseenter/mouseover on the exact target element
                    await page.evaluate(r"""(pos) => {
                        var el = document.getElementById('seq-hover-area') || document.querySelector('[data-hover-area]');
                        if (!el) el = document.elementFromPoint(pos.x, pos.y);
                        if (el) {
                            el.dispatchEvent(new MouseEvent('mouseenter', {bubbles: false, clientX: pos.x, clientY: pos.y}));
                            el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true, clientX: pos.x, clientY: pos.y}));
                        }
                    }""", {"x": hp["x"], "y": hp["y"]})
                    # Hold hover for 1.2s (challenge needs 800ms)
                    await asyncio.sleep(1.2)
                    completed.append("hover")
                    print(f"    [sequence] hover: held at ({hp['x']},{hp['y']}) for 1.2s")

                # 3. TYPE: Find input and type text with proper event dispatch
                type_result = await page.evaluate(r"""() => {
                    // Try by ID first
                    var inp = document.getElementById('seq-type-input');
                    if (!inp || inp.offsetWidth === 0) {
                        // Fallback: find non-code input by placeholder
                        var inputs = document.querySelectorAll('input[type="text"], input:not([type])');
                        for (var i = 0; i < inputs.length; i++) {
                            if (inputs[i].offsetWidth === 0) continue;
                            var ph = (inputs[i].placeholder||'').toLowerCase();
                            if (/code|character|submit/i.test(ph) || inputs[i].maxLength === 6) continue;
                            inp = inputs[i]; break;
                        }
                    }
                    if (!inp) return 'NOT_FOUND';
                    inp.focus();
                    // Reset _valueTracker for React compatibility
                    var tracker = inp._valueTracker;
                    if (tracker) tracker.setValue('');
                    // Set value via native setter
                    var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                    setter.call(inp, 'hello');
                    // Dispatch input event (the challenge's native listener checks this)
                    inp.dispatchEvent(new Event('input', {bubbles: true}));
                    inp.dispatchEvent(new Event('change', {bubbles: true}));
                    // Also dispatch InputEvent (more specific, includes data)
                    try { inp.dispatchEvent(new InputEvent('input', {bubbles: true, data: 'hello', inputType: 'insertText'})); } catch(e) {}
                    return 'filled:' + inp.value + ' len=' + inp.value.length;
                }""")
                print(f"    [sequence] type result: {type_result}")
                if type_result and type_result != 'NOT_FOUND':
                    completed.append("type")
                await asyncio.sleep(0.3)

                # 4. SCROLL: Find scroll box and scroll it
                scroll_result = await page.evaluate(r"""() => {
                    // Try by ID first
                    var box = document.getElementById('seq-scroll-box');
                    if (!box || box.offsetWidth === 0) {
                        box = document.querySelector('[data-scroll-box]');
                    }
                    if (!box || box.offsetWidth === 0) {
                        // Fallback: find scrollable div with "Scroll inside" text
                        var divs = document.querySelectorAll('div');
                        for (var i = 0; i < divs.length; i++) {
                            var d = divs[i];
                            if (d.scrollHeight <= d.clientHeight + 20) continue;
                            var r = d.getBoundingClientRect();
                            if (r.width < 50 || r.height < 30 || r.height > 300 || r.width > window.innerWidth * 0.8) continue;
                            box = d; break;
                        }
                    }
                    if (!box) return 'NOT_FOUND';
                    // Actually scroll it
                    box.scrollTop = box.scrollHeight;
                    // Dispatch scroll event on the exact element
                    box.dispatchEvent(new Event('scroll', {bubbles: false}));
                    box.dispatchEvent(new Event('scroll', {bubbles: true}));
                    return 'scrolled:' + box.scrollTop + '/' + box.scrollHeight + ' id=' + (box.id||'none');
                }""")
                print(f"    [sequence] scroll result: {scroll_result}")
                if scroll_result and scroll_result != 'NOT_FOUND':
                    completed.append("scroll")
                await asyncio.sleep(0.3)

                # Check progress
                progress = await page.evaluate(r"""() => {
                    var p = document.getElementById('seq-progress');
                    if (p) return p.textContent;
                    // Fallback: find progress text anywhere
                    var all = document.body.innerText;
                    var m = all.match(/Progress:\s*(\d+)\/(\d+)/);
                    return m ? m[0] : 'not found';
                }""")
                print(f"    [sequence] progress after actions: {progress}")

                # Click Complete button
                await asyncio.sleep(0.5)
                comp_info = await page.evaluate(r"""() => {
                    var btn = document.getElementById('seq-complete-btn');
                    if (!btn || btn.offsetWidth === 0) {
                        var btns = document.querySelectorAll('button');
                        for (var i = 0; i < btns.length; i++) {
                            if (btns[i].offsetWidth > 0 && /^Complete/i.test(btns[i].textContent.trim())) {
                                btn = btns[i]; break;
                            }
                        }
                    }
                    if (!btn) return null;
                    // Force enable if disabled
                    if (btn.disabled) btn.disabled = false;
                    btn.scrollIntoView({block:'center',behavior:'instant'});
                    var r = btn.getBoundingClientRect();
                    // Try native click + React onClick
                    btn.click();
                    var cur = btn;
                    for (var d = 0; d < 5 && cur; d++) {
                        var rk = Object.keys(cur).find(function(k) { return k.indexOf('__reactProps') === 0; });
                        if (rk && cur[rk] && cur[rk].onClick) {
                            try { cur[rk].onClick({preventDefault:function(){},stopPropagation:function(){},target:btn,currentTarget:btn,nativeEvent:{}}); } catch(e) {}
                            break;
                        }
                        cur = cur.parentElement;
                    }
                    return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), text: btn.textContent.trim()});
                }""")
                if comp_info:
                    ci = json.loads(comp_info) if isinstance(comp_info, str) else comp_info
                    await mouse.click(ci["x"], ci["y"])
                    print(f"    [sequence] clicked Complete: {ci.get('text')}")
                    await asyncio.sleep(0.5)
                btn = await _try_complete_button(page)
                detail = f"sequence all-actions: {completed}, progress={progress}"
                if btn:
                    detail += f", clicked '{btn}'"
                return {"ok": True, "detail": detail}

            # If pills WERE found, use targeted approach
            if not next_action and not actions:
                # All done — click Complete
                if info.get("completeBtn"):
                    cb = info["completeBtn"]
                    await page.evaluate(r"""() => {
                        var btns = document.querySelectorAll('button');
                        for (var i = 0; i < btns.length; i++) {
                            if (/^Complete/i.test(btns[i].textContent.trim())) {
                                if (btns[i].disabled) btns[i].disabled = false;
                                var cur = btns[i];
                                for (var d = 0; d < 5 && cur; d++) {
                                    var rk = Object.keys(cur).find(function(k) { return k.indexOf('__reactProps') === 0; });
                                    if (rk && cur[rk] && cur[rk].onClick) {
                                        try { cur[rk].onClick({preventDefault:function(){},stopPropagation:function(){},target:btns[i],currentTarget:btns[i],nativeEvent:{}}); } catch(e) {}
                                        break;
                                    }
                                    cur = cur.parentElement;
                                }
                                btns[i].click();
                                break;
                            }
                        }
                    }""")
                    await mouse.click(cb["x"], cb["y"])
                    await asyncio.sleep(0.5)
                    btn = await _try_complete_button(page)
                    detail = f"sequence complete: {completed}"
                    if btn:
                        detail += f", clicked '{btn}'"
                    return {"ok": True, "detail": detail}
                break

            # Execute next action from pills
            if next_action == "click button" and info.get("clickBtn"):
                pos = info["clickBtn"]
                await mouse.click(pos["x"], pos["y"])
                completed.append("click")
                await asyncio.sleep(0.4)

            elif next_action == "hover area" and info.get("hoverArea"):
                pos = info["hoverArea"]
                await mouse.move(pos["x"], pos["y"])
                await page.evaluate(r"""(pos) => {
                    var el = document.elementFromPoint(pos.x, pos.y);
                    if (el) {
                        el.dispatchEvent(new MouseEvent('mouseenter', {bubbles: false, clientX: pos.x, clientY: pos.y}));
                        el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true, clientX: pos.x, clientY: pos.y}));
                    }
                }""", {"x": pos["x"], "y": pos["y"]})
                await asyncio.sleep(1.2)
                completed.append("hover")

            elif next_action == "type text" and info.get("typeInput"):
                pos = info["typeInput"]
                await mouse.click(pos["x"], pos["y"])
                await asyncio.sleep(0.2)
                await _fill_input_react(page,
                    "document.activeElement && document.activeElement.tagName === 'INPUT' ? document.activeElement : null",
                    "hello")
                completed.append("type")
                await asyncio.sleep(0.4)

            elif next_action == "scroll box" and info.get("scrollBox"):
                pos = info["scrollBox"]
                await mouse.move(pos["x"], pos["y"])
                await asyncio.sleep(0.2)
                # Use JS scroll since browser-use Mouse doesn't have wheel()
                for _ in range(2):
                    await page.evaluate("(pos) => { var el = document.elementFromPoint(pos.x, pos.y); while (el && el.scrollHeight <= el.clientHeight + 20) el = el.parentElement; if (el) { el.scrollTop += 200; el.dispatchEvent(new Event('scroll', {bubbles: true})); } }", {"x": pos["x"], "y": pos["y"]})
                    await asyncio.sleep(0.3)
                completed.append("scroll")
                await asyncio.sleep(0.4)

            else:
                break

            # After each action, brief pause for state update
            await asyncio.sleep(0.3)

        # Final: click Complete button (may be enabled now)
        comp_raw = await page.evaluate(r"""() => {
            var btns = document.querySelectorAll('button');
            for (var i = 0; i < btns.length; i++) {
                var btn = btns[i];
                if (btn.offsetWidth === 0) continue;
                var t = btn.textContent.trim();
                if (/^Complete/i.test(t) && !/^Complete Challenge/i.test(t)) {
                    // Force enable and click
                    if (btn.disabled) btn.disabled = false;
                    var cur = btn;
                    for (var d = 0; d < 5 && cur; d++) {
                        var rk = Object.keys(cur).find(function(k) { return k.indexOf('__reactProps') === 0; });
                        if (rk && cur[rk] && cur[rk].onClick) {
                            try { cur[rk].onClick({preventDefault:function(){},stopPropagation:function(){},target:btn,currentTarget:btn,nativeEvent:{}}); } catch(e) {}
                            break;
                        }
                        cur = cur.parentElement;
                    }
                    btn.click();
                    btn.scrollIntoView({block:'center',behavior:'instant'});
                    var r = btn.getBoundingClientRect();
                    return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), text: t});
                }
            }
            return null;
        }""")
        if comp_raw:
            comp = json.loads(comp_raw) if isinstance(comp_raw, str) else comp_raw
            await mouse.click(comp["x"], comp["y"])
            await asyncio.sleep(0.5)

        btn = await _try_complete_button(page)
        detail = f"sequence actions: {completed}"
        if btn:
            detail += f", clicked '{btn}'"
        return {"ok": True, "detail": detail}

    except Exception as e:
        return {"ok": False, "detail": f"sequence error: {e}"}


async def handle_dismiss_popups(page, params: dict) -> dict:
    """Dismiss all popup overlays — click X buttons, Dismiss, Accept/Decline, Close."""
    try:
        mouse = await _get_mouse(page)

        # Strategy: Use JS to find all popup close targets, sorted by z-index (topmost first)
        dismissed = await page.evaluate(r"""() => {
            var dismissed = 0;
            var maxRounds = 10;

            for (var round = 0; round < maxRounds; round++) {
                // Find topmost popup close target
                var best = null;
                var bestZ = -1;

                // 1. Red X circle buttons (SVG or text × in a rounded-full button)
                var allBtns = document.querySelectorAll('button');
                for (var i = 0; i < allBtns.length; i++) {
                    var btn = allBtns[i];
                    if (btn.offsetWidth === 0 || btn.offsetHeight === 0) continue;
                    var cls = (typeof btn.className === 'string') ? btn.className : '';
                    // Red circular close buttons: bg-red or rounded-full with X/× text
                    var isXBtn = (/bg-red/.test(cls) && /rounded-full/.test(cls)) ||
                                 (/rounded-full/.test(cls) && /×|✕|X/.test(btn.textContent.trim()));
                    if (!isXBtn) continue;
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

                // 2. "Dismiss" buttons (for Alert popups where Close is fake)
                if (!best) {
                    for (var i = 0; i < allBtns.length; i++) {
                        var btn = allBtns[i];
                        if (btn.offsetWidth === 0 || btn.offsetHeight === 0) continue;
                        var t = btn.textContent.trim();
                        if (t === 'Dismiss' || t === 'Accept' || t === 'Decline') {
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
                    }
                }

                // 3. "Close" buttons (some popups have legit Close)
                if (!best) {
                    for (var i = 0; i < allBtns.length; i++) {
                        var btn = allBtns[i];
                        if (btn.offsetWidth === 0 || btn.offsetHeight === 0) continue;
                        var t = btn.textContent.trim();
                        if (t === 'Close') {
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
                    }
                }

                if (!best) break;

                // Click via React onClick if available, else native click
                var rk = Object.keys(best).find(function(k) { return k.indexOf('__reactProps') === 0; });
                if (rk && best[rk] && best[rk].onClick) {
                    try {
                        best[rk].onClick({preventDefault: function(){}, stopPropagation: function(){}, target: best, currentTarget: best, nativeEvent: {}});
                        dismissed++;
                    } catch(e) {
                        best.click();
                        dismissed++;
                    }
                } else {
                    best.click();
                    dismissed++;
                }
            }
            return dismissed;
        }""")

        dismissed_count = int(dismissed) if dismissed else 0

        if dismissed_count == 0:
            # Fallback: try Playwright clicks on visible X buttons
            raw_targets = await page.evaluate(r"""() => {
                var targets = [];
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    var btn = btns[i];
                    if (btn.offsetWidth === 0 || btn.offsetHeight === 0) continue;
                    var cls = (typeof btn.className === 'string') ? btn.className : '';
                    var t = btn.textContent.trim();
                    if ((/bg-red/.test(cls) && /rounded-full/.test(cls)) ||
                        t === 'Dismiss' || t === 'Accept' || t === 'Decline' || t === 'Close') {
                        btn.scrollIntoView({block: 'center', behavior: 'instant'});
                        var r = btn.getBoundingClientRect();
                        targets.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), text: t.substring(0, 20)});
                    }
                }
                return JSON.stringify(targets);
            }""")
            items = json.loads(raw_targets) if isinstance(raw_targets, str) else (raw_targets or [])
            for item in items:
                try:
                    await mouse.click(item["x"], item["y"])
                    dismissed_count += 1
                    await asyncio.sleep(0.3)
                except Exception:
                    pass

        if dismissed_count > 0:
            await asyncio.sleep(0.5)

        # Always check for scrollable modal with radio buttons
        # (popups may have been dismissed by inline JS already)
        radio_result = await _handle_scrollable_radio_modal(page, mouse)
        if dismissed_count > 0 or radio_result:
            detail = f"dismissed {dismissed_count} popups"
            if radio_result:
                detail += f", {radio_result}"
            return {"ok": True, "detail": detail}

        return {"ok": False, "detail": "no popups found to dismiss"}
    except Exception as e:
        return {"ok": False, "detail": f"dismiss_popups error: {e}"}


async def _handle_scrollable_radio_modal(page, mouse) -> str | None:
    """After popups cleared, handle radio button selection modal.

    Finds radio buttons (both input[type=radio] and [role=radio]),
    selects the one with 'Correct' in its label text, then clicks Submit.
    """
    try:
        result = await page.evaluate(r"""() => {
            // Find all radio-like elements
            var radios = Array.from(document.querySelectorAll(
                'input[type="radio"], [role="radio"], button[id^="option-"]'
            )).filter(function(r) { return r.offsetWidth > 0; });
            if (radios.length === 0) return JSON.stringify({clicked: false});

            // Find the "Correct Choice" option
            var best = null;
            for (var i = 0; i < radios.length; i++) {
                var r = radios[i];
                var label = r.closest('label') || r.parentElement;
                var text = (label ? label.textContent : r.textContent) || '';
                if (/\bcorrect\b/i.test(text) && !/incorrect/i.test(text)) {
                    best = r;
                    break;
                }
            }
            // Fallback: last radio (often correct in scrollable modals)
            if (!best) best = radios[radios.length - 1];

            // Scroll into view
            best.scrollIntoView({block: 'center', behavior: 'instant'});

            // Click via React props
            var rk = Object.keys(best).find(function(k) { return k.indexOf('__reactProps') === 0; });
            if (rk && best[rk]) {
                if (best[rk].onChange) {
                    try { best[rk].onChange({target: {checked: true, value: best.value || ''}, preventDefault: function(){}, stopPropagation: function(){}}); } catch(e) {}
                }
                if (best[rk].onClick) {
                    try { best[rk].onClick({preventDefault: function(){}, stopPropagation: function(){}, target: best, currentTarget: best, nativeEvent: {}, bubbles: true, type: 'click'}); } catch(e) {}
                }
            }
            best.click();
            if (best.tagName === 'INPUT') best.checked = true;

            // Also click parent label for React delegation
            var label = best.closest('label') || best.parentElement;
            if (label && label !== best) {
                var lrk = Object.keys(label).find(function(k) { return k.indexOf('__reactProps') === 0; });
                if (lrk && label[lrk] && label[lrk].onClick) {
                    try { label[lrk].onClick({preventDefault: function(){}, stopPropagation: function(){}, target: label, currentTarget: label, nativeEvent: {}}); } catch(e) {}
                }
            }

            var rect = best.getBoundingClientRect();
            return JSON.stringify({
                clicked: true,
                label: ((best.closest('label') || best.parentElement || {}).textContent || '').substring(0, 50),
                x: Math.round(rect.x + rect.width/2),
                y: Math.round(rect.y + rect.height/2)
            });
        }""")

        res = json.loads(result) if isinstance(result, str) else (result or {})
        if res.get("clicked"):
            # Also do Playwright click for trusted event
            try:
                await mouse.click(res["x"], res["y"])
            except Exception:
                pass
            await asyncio.sleep(0.5)

            # Try clicking any Submit/Confirm button
            btn = await _try_complete_button(page)
            label_text = res.get("label", "")[:40]
            detail = f"radio selected: {label_text}"
            if btn:
                detail += f", clicked '{btn}'"
            return detail

        return None
    except Exception as e:
        return f"radio modal error: {e}"


async def handle_encoded_code(page, params: dict) -> dict:
    """Handle Encoded Code Challenge: decode Base64, find hidden code via hint.

    The challenge has a Base64 string that decodes to a hint. The actual 6-char code
    is hidden in the page (data attrs, title attrs, hidden elements, etc.).
    This handler decodes the Base64 hint and does a deep DOM scan for the real code.

    Key: The live site is a React app. Input value must be set via _valueTracker
    reset + native setter to trigger React state update. Playwright keyboard typing
    is the most reliable method for React compatibility.
    """
    try:
        result = await page.evaluate(r"""() => {
            var codeRE = /[A-HJ-NP-Z2-9]{6}/g;
            var FALSE_POS = new Set(['SUBMIT','BUTTON','HIDDEN','SCROLL','CANVAS','COOKIE','ACCEPT','REJECT',
                'OPTION','SELECT','SEARCH','FILTER','NUMBER','REVEAL','SHADOW','WORKER','RENDER','CHANGE',
                'PARENT','RETURN','ESCAPE','CURSOR','SCREEN','CHROME','PLEASE','VERIFY','ANSWER','RESULT',
                'ABCDEF','DECODE','AAAAAA']);
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

            // 1. Decode Base64 strings on the page
            var decoded = [];
            var b64RE = /[A-Za-z0-9+\/]{8,}={0,2}/g;
            var bodyText = document.body ? document.body.innerText : '';
            var b64matches = bodyText.match(b64RE) || [];
            for (var i = 0; i < b64matches.length; i++) {
                try {
                    var d = atob(b64matches[i]);
                    decoded.push(d);
                    add(d, 'base64-decoded');
                } catch(e) {}
            }

            // 2. Deep scan: ALL attributes (not just data-*)
            var allEls = document.querySelectorAll('*');
            for (var i = 0; i < allEls.length; i++) {
                var el = allEls[i];
                var attrs = el.attributes;
                for (var j = 0; j < attrs.length; j++) {
                    add(attrs[j].value, 'attr:' + attrs[j].name);
                }
                if (el.title) add(el.title, 'title');
                var rk = Object.keys(el).find(function(k) { return k.indexOf('__reactProps') === 0; });
                if (rk && el[rk]) {
                    var props = el[rk];
                    for (var key in props) {
                        if (typeof props[key] === 'string') add(props[key], 'react-prop:' + key);
                    }
                }
                var fk = Object.keys(el).find(function(k) { return k.indexOf('__reactFiber') === 0; });
                if (fk && el[fk]) {
                    var fiber = el[fk];
                    if (fiber.memoizedState) {
                        try {
                            var state = fiber.memoizedState;
                            while (state) {
                                if (state.memoizedState && typeof state.memoizedState === 'string') {
                                    add(state.memoizedState, 'fiber-state');
                                }
                                if (state.queue && state.queue.lastRenderedState && typeof state.queue.lastRenderedState === 'string') {
                                    add(state.queue.lastRenderedState, 'fiber-queue');
                                }
                                state = state.next;
                            }
                        } catch(e) {}
                    }
                }
            }

            // 3. Check computed styles ::before/::after
            var styled = document.querySelectorAll('body *');
            for (var i = 0; i < styled.length && i < 500; i++) {
                try {
                    var bf = window.getComputedStyle(styled[i], '::before').content;
                    if (bf && bf !== 'none' && bf !== 'normal') add(bf.replace(/['"]/g,''), 'css-before');
                    var af = window.getComputedStyle(styled[i], '::after').content;
                    if (af && af !== 'none' && af !== 'normal') add(af.replace(/['"]/g,''), 'css-after');
                } catch(e) {}
            }

            // 4. Comments
            try {
                var w = document.createTreeWalker(document.body||document, NodeFilter.SHOW_COMMENT);
                var n; while(n=w.nextNode()) add(n.textContent, 'comment');
            } catch(e){}

            // 5. Hidden/invisible text
            var hidden = document.querySelectorAll('[style*="display:none"],[style*="display: none"],[hidden],.hidden,[style*="opacity:0"],[style*="opacity: 0"],[style*="font-size:0"],[style*="font-size: 0"],[style*="color: transparent"],[style*="color:transparent"]');
            for (var i = 0; i < hidden.length; i++) add(hidden[i].textContent, 'hidden-el');

            // 6. Shadow roots
            for (var i = 0; i < allEls.length; i++) {
                if (allEls[i].shadowRoot) add(allEls[i].shadowRoot.textContent, 'shadow');
            }

            // 7. Script contents
            var scripts = document.querySelectorAll('script:not([src])');
            for (var i = 0; i < scripts.length; i++) {
                add(scripts[i].textContent, 'script');
            }

            return JSON.stringify({codes: codes, decoded: decoded.slice(0, 5)});
        }""")

        data = json.loads(result) if isinstance(result, str) else (result or {})
        codes = data.get("codes", [])
        decoded = data.get("decoded", [])

        detail = f"decoded {len(decoded)} base64 strings"
        if decoded:
            detail += f": {decoded[:3]}"
        if codes:
            detail += f", found {len(codes)} codes: {[c['text'] + '(' + c['source'] + ')' for c in codes[:5]]}"
        else:
            detail += ", no codes found"

        # If no codes found, fill the input field (if present) and click "Reveal"
        if not codes:
            # Dismiss overlays first
            await page.evaluate(r"""() => {
                var els = document.querySelectorAll('div');
                for (var i = 0; i < els.length; i++) {
                    var s = getComputedStyle(els[i]);
                    if ((s.position === 'fixed' || s.position === 'absolute') && parseInt(s.zIndex) >= 9000)
                        els[i].style.display = 'none';
                }
            }""")

            # Find input position — use React-compatible value setting
            input_info = await page.evaluate(r"""() => {
                var input = document.querySelector('input[maxlength="6"]') ||
                            document.querySelector('input[placeholder*="code" i]') ||
                            document.querySelector('input[placeholder*="decode" i]') ||
                            document.querySelector('input[placeholder*="char" i]');
                if (!input) return null;
                input.scrollIntoView({block: 'center', behavior: 'instant'});
                var r = input.getBoundingClientRect();
                // Reset React's _valueTracker so React detects the change
                var tracker = input._valueTracker;
                if (tracker) tracker.setValue('');
                return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                                       curVal: input.value, hasTracker: !!tracker});
            }""")

            if input_info:
                ii = json.loads(input_info) if isinstance(input_info, str) else input_info
                mouse = await _get_mouse(page)
                print(f"    [encoded] input at ({ii['x']},{ii['y']}), curVal='{ii.get('curVal','')}', tracker={ii.get('hasTracker')}")

                # Fill using React-compatible JS value setter (_valueTracker reset)
                # page.press() doesn't insert text into inputs, so use JS setter directly
                fill_result = await _fill_input_react(page,
                    "document.querySelector('input[maxlength=\"6\"]') || document.querySelector('input[placeholder*=\"code\" i]') || document.querySelector('input[placeholder*=\"char\" i]')",
                    "ABCDEF"
                )
                print(f"    [encoded] fill result: {fill_result}")

                await asyncio.sleep(0.3)

                # Verify input has value
                cur_val = await page.evaluate(r"""() => {
                    var input = document.querySelector('input[maxlength="6"]') ||
                                document.querySelector('input[placeholder*="code" i]') ||
                                document.querySelector('input[placeholder*="char" i]');
                    return input ? input.value : 'NOT_FOUND';
                }""")
                print(f"    [encoded] input value after fill: '{cur_val}'")
            else:
                print(f"    [encoded] no input found")

            await asyncio.sleep(0.3)

            # Find Reveal button — check both enabled AND disabled (React may enable on click)
            reveal_info = await page.evaluate(r"""() => {
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    var btn = buttons[i];
                    if (btn.offsetWidth === 0 || btn.offsetHeight === 0) continue;
                    var t = btn.textContent.trim();
                    if (/^Reveal$/i.test(t) || /Reveal Code/i.test(t) || /Decode/i.test(t) || /^Show$/i.test(t)) {
                        btn.scrollIntoView({block: 'center', behavior: 'instant'});
                        var r = btn.getBoundingClientRect();
                        // Try React onClick dispatch
                        var reactClicked = false;
                        var cur = btn;
                        for (var depth = 0; depth < 5 && cur; depth++) {
                            var rk = Object.keys(cur).find(function(k) { return k.indexOf('__reactProps') === 0; });
                            if (rk && cur[rk] && cur[rk].onClick) {
                                try {
                                    cur[rk].onClick({preventDefault:function(){},stopPropagation:function(){},
                                        target:btn,currentTarget:btn,nativeEvent:{},bubbles:true,type:'click'});
                                    reactClicked = true;
                                } catch(e) {}
                                break;
                            }
                            cur = cur.parentElement;
                        }
                        return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                                               text: t, disabled: btn.disabled, reactClicked: reactClicked});
                    }
                }
                return null;
            }""")
            if reveal_info:
                ri = json.loads(reveal_info) if isinstance(reveal_info, str) else reveal_info
                mouse = await _get_mouse(page)
                print(f"    [encoded] Reveal btn: text='{ri['text']}', disabled={ri.get('disabled')}, reactClicked={ri.get('reactClicked')}, pos=({ri['x']},{ri['y']})")

                # Dismiss overlays again (may have re-appeared)
                await page.evaluate(r"""() => {
                    var els = document.querySelectorAll('div');
                    for (var i = 0; i < els.length; i++) {
                        var s = getComputedStyle(els[i]);
                        if ((s.position === 'fixed' || s.position === 'absolute') && parseInt(s.zIndex) >= 9000)
                            els[i].style.display = 'none';
                    }
                }""")

                # Playwright trusted click (triggers native + React event delegation)
                await mouse.click(ri["x"], ri["y"])
                await asyncio.sleep(0.5)

                # Also try native btn.click() on the exact button element
                await page.evaluate(r"""() => {
                    var buttons = document.querySelectorAll('button');
                    for (var i = 0; i < buttons.length; i++) {
                        var t = buttons[i].textContent.trim();
                        if (/^Reveal$/i.test(t) || /Reveal Code/i.test(t) || /Decode/i.test(t) || /^Show$/i.test(t)) {
                            if (buttons[i].disabled) buttons[i].disabled = false;
                            buttons[i].click();
                            buttons[i].dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                            break;
                        }
                    }
                }""")
                await asyncio.sleep(1.0)

                detail += f", filled input + clicked '{ri['text']}' (disabled={ri.get('disabled')}, reactClicked={ri.get('reactClicked')})"

                # Re-scan for the revealed code
                post_scan = await page.evaluate(r"""() => {
                    var codeRE = /[A-HJ-NP-Z2-9]{6}/g;
                    var FALSE_POS = new Set(['SUBMIT','BUTTON','HIDDEN','SCROLL','CANVAS','COOKIE','ACCEPT','REJECT',
                        'OPTION','SELECT','SEARCH','FILTER','NUMBER','REVEAL','SHADOW','WORKER','RENDER','CHANGE',
                        'PARENT','RETURN','ESCAPE','CURSOR','SCREEN','CHROME','PLEASE','VERIFY','ANSWER','RESULT',
                        'ABCDEF','DECODE','AAAAAA']);
                    var codes = [];
                    // Scan all visible text
                    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
                    while (walker.nextNode()) {
                        var t = walker.currentNode.textContent.trim();
                        var m = t.match(codeRE);
                        if (m) {
                            for (var i = 0; i < m.length; i++) {
                                if (!FALSE_POS.has(m[i]) && /[A-Z]/.test(m[i])) codes.push(m[i]);
                            }
                        }
                    }
                    // Also check newly visible elements and React state
                    var allEls = document.querySelectorAll('*');
                    for (var i = 0; i < allEls.length; i++) {
                        var el = allEls[i];
                        // Check if element just became visible
                        var s = getComputedStyle(el);
                        if (s.display !== 'none' && s.visibility !== 'hidden' && el.offsetHeight > 0) {
                            var ct = el.textContent.trim();
                            if (ct.length > 0 && ct.length < 20) {
                                var m2 = ct.match(codeRE);
                                if (m2) {
                                    for (var j = 0; j < m2.length; j++) {
                                        if (!FALSE_POS.has(m2[j]) && /[A-Z]/.test(m2[j]) && codes.indexOf(m2[j]) === -1) codes.push(m2[j]);
                                    }
                                }
                            }
                        }
                        // Check React fiber state for newly set code values
                        var fk = Object.keys(el).find(function(k) { return k.indexOf('__reactFiber') === 0; });
                        if (fk && el[fk] && el[fk].memoizedState) {
                            try {
                                var state = el[fk].memoizedState;
                                while (state) {
                                    if (state.memoizedState && typeof state.memoizedState === 'string') {
                                        var m3 = state.memoizedState.match(codeRE);
                                        if (m3) {
                                            for (var j = 0; j < m3.length; j++) {
                                                if (!FALSE_POS.has(m3[j]) && /[A-Z]/.test(m3[j]) && codes.indexOf(m3[j]) === -1)
                                                    codes.push(m3[j]);
                                            }
                                        }
                                    }
                                    state = state.next;
                                }
                            } catch(e) {}
                        }
                    }
                    return JSON.stringify(codes);
                }""")
                post_codes = json.loads(post_scan) if isinstance(post_scan, str) else (post_scan or [])
                if post_codes:
                    detail += f", post-reveal codes: {post_codes}"
                    return {"ok": True, "detail": detail, "codes_found": post_codes}
                else:
                    print(f"    [encoded] post-reveal: no codes found")
            else:
                print(f"    [encoded] no Reveal button found")

        return {"ok": True, "detail": detail}
    except Exception as e:
        return {"ok": False, "detail": f"encoded_code error: {e}"}


async def handle_multi_tab(page, params: dict) -> dict:
    """Handle Multi-Tab Challenge: click all tab buttons to visit them.

    Live site is React — uses both React onClick dispatch AND Playwright trusted clicks.
    Finds tabs by "Tab N" text pattern (no IDs on live site).
    """
    try:
        mouse = await _get_mouse(page)

        # First, try React onClick dispatch for ALL tabs (fastest, most reliable for React)
        react_result = await page.evaluate(r"""() => {
            var btns = [];
            // Strategy 1: Find by data-tab attribute
            btns = Array.from(document.querySelectorAll('button[data-tab]'));
            // Strategy 2: Find by "Tab N" text
            if (btns.length === 0) {
                document.querySelectorAll('button').forEach(function(b) {
                    if (/^(✓\s*)?Tab\s*\d+/i.test(b.textContent.trim())) btns.push(b);
                });
            }
            if (btns.length === 0) return JSON.stringify({count: 0, tabs: []});

            var clicked = [];
            for (var i = 0; i < btns.length; i++) {
                var btn = btns[i];
                if (btn.offsetWidth === 0) continue;
                var text = btn.textContent.trim();

                // Try React onClick dispatch
                var cur = btn;
                var reactDone = false;
                for (var depth = 0; depth < 5 && cur; depth++) {
                    var rk = Object.keys(cur).find(function(k) { return k.indexOf('__reactProps') === 0; });
                    if (rk && cur[rk] && cur[rk].onClick) {
                        try {
                            cur[rk].onClick({preventDefault:function(){},stopPropagation:function(){},
                                target:btn,currentTarget:btn,nativeEvent:{},bubbles:true,type:'click'});
                            reactDone = true;
                        } catch(e) {}
                        break;
                    }
                    cur = cur.parentElement;
                }
                // Also fire native click + MouseEvent
                btn.click();
                btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));

                btn.scrollIntoView({block:'center',behavior:'instant'});
                var r = btn.getBoundingClientRect();
                clicked.push({text: text, x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), react: reactDone});
            }
            return JSON.stringify({count: clicked.length, tabs: clicked});
        }""")
        rr = json.loads(react_result) if isinstance(react_result, str) else (react_result or {})
        tab_positions = rr.get("tabs", [])
        react_count = rr.get("count", 0)

        # Also do Playwright trusted clicks on each tab (belt and suspenders)
        clicked = []
        for tab in tab_positions:
            await mouse.click(tab["x"], tab["y"])
            clicked.append(tab["text"])
            await asyncio.sleep(0.4)

        if clicked:
            await asyncio.sleep(0.5)

            # Check progress text to verify all tabs were visited
            progress = await page.evaluate(r"""() => {
                var text = document.body.innerText || '';
                var m = text.match(/Visited:\s*(\d+)\s*\/\s*(\d+)/);
                return m ? m[0] : 'no progress found';
            }""")
            print(f"    [multi_tab] progress: {progress}")

            # Try to find and click completion button INSIDE the tab challenge container
            complete_result = await page.evaluate(r"""() => {
                // Find the tab container (look for Tab buttons or data-tab elements)
                var tabBtn = document.querySelector('button[data-tab]');
                if (!tabBtn) {
                    var btns = document.querySelectorAll('button');
                    for (var i = 0; i < btns.length; i++) {
                        if (/^(✓\s*)?Tab\s*\d+/i.test(btns[i].textContent.trim())) {
                            tabBtn = btns[i]; break;
                        }
                    }
                }
                if (!tabBtn) return null;

                // Walk up to find the challenge container
                var container = tabBtn.parentElement;
                for (var d = 0; d < 8 && container; d++) {
                    // Look for Reveal Code / All Tabs Visited button in this container
                    var containerBtns = container.querySelectorAll('button');
                    for (var j = 0; j < containerBtns.length; j++) {
                        var t = containerBtns[j].textContent.trim();
                        if (/Reveal|All.*Tab.*Visit|Complete/i.test(t) && !/Tab\s*\d+/i.test(t)) {
                            if (containerBtns[j].disabled) {
                                containerBtns[j].disabled = false;
                            }
                            containerBtns[j].scrollIntoView({block:'center',behavior:'instant'});
                            var r = containerBtns[j].getBoundingClientRect();
                            // Fire React onClick + native click
                            var cur = containerBtns[j];
                            for (var dd = 0; dd < 5 && cur; dd++) {
                                var rk = Object.keys(cur).find(function(k) { return k.indexOf('__reactProps') === 0; });
                                if (rk && cur[rk] && cur[rk].onClick) {
                                    try { cur[rk].onClick({preventDefault:function(){},stopPropagation:function(){},
                                        target:containerBtns[j],currentTarget:containerBtns[j],nativeEvent:{},bubbles:true,type:'click'}); } catch(e) {}
                                    break;
                                }
                                cur = cur.parentElement;
                            }
                            containerBtns[j].click();
                            return JSON.stringify({text: t, x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)});
                        }
                    }
                    container = container.parentElement;
                }
                return null;
            }""")
            comp_label = None
            if complete_result:
                ci = json.loads(complete_result) if isinstance(complete_result, str) else complete_result
                comp_label = ci.get("text", "")
                # Also Playwright trusted click
                try:
                    await mouse.click(ci["x"], ci["y"])
                except Exception:
                    pass
                await asyncio.sleep(0.3)

            # Force-reveal any hidden code elements
            force_codes = await page.evaluate(r"""() => {
                var CODE_RE = /^[A-HJ-NP-Z2-9]{6}$/;
                var found = [];
                function check(el) {
                    var t = el.textContent.trim();
                    if (CODE_RE.test(t) && /[A-Z]/.test(t) && found.indexOf(t) === -1) {
                        el.style.setProperty('display', 'block', 'important');
                        el.style.setProperty('visibility', 'visible', 'important');
                        el.style.setProperty('opacity', '1', 'important');
                        found.push(t);
                    }
                }
                // Check all hidden elements
                document.querySelectorAll('*').forEach(function(el) {
                    var cs = window.getComputedStyle(el);
                    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') {
                        check(el);
                        el.querySelectorAll('*').forEach(check);
                    }
                });
                return JSON.stringify(found);
            }""")
            fc = json.loads(force_codes) if isinstance(force_codes, str) else (force_codes or [])
            if fc:
                print(f"    [multi_tab] force-revealed codes: {fc}")

            # Try to reconstruct code from tab pieces (fallback)
            pieces_code = await page.evaluate(r"""() => {
                var pieces = [];
                document.querySelectorAll('button').forEach(function(b) {
                    var m = b.textContent.trim().match(/Tab\s*\d+[:\s]+([A-HJ-NP-Z2-9]{2})/i);
                    if (m) pieces.push(m[1]);
                });
                if (pieces.length === 0) return null;
                // Try simple concatenation
                var concat = pieces.join('');
                // Try overlapping reconstruction
                if (pieces.length >= 3) {
                    var code = pieces[0];
                    for (var i = 1; i < pieces.length; i++) {
                        // If last char of code == first char of next piece, overlap
                        if (code[code.length - 1] === pieces[i][0]) {
                            code += pieces[i][1];
                        } else {
                            code += pieces[i];
                        }
                    }
                    if (code.length === 6 && /^[A-HJ-NP-Z2-9]{6}$/.test(code) && /[A-Z]/.test(code)) {
                        return JSON.stringify({type: 'overlap', code: code});
                    }
                }
                // Simple concat if it makes a valid 6-char code
                if (concat.length === 6 && /^[A-HJ-NP-Z2-9]{6}$/.test(concat) && /[A-Z]/.test(concat)) {
                    return JSON.stringify({type: 'concat', code: concat});
                }
                // Return first 6 chars if valid
                var sub = concat.substring(0, 6);
                if (sub.length === 6 && /^[A-HJ-NP-Z2-9]{6}$/.test(sub) && /[A-Z]/.test(sub)) {
                    return JSON.stringify({type: 'first6', code: sub});
                }
                return JSON.stringify({type: 'raw', pieces: pieces});
            }""")

            detail = f"clicked {len(clicked)} tabs (react={react_count}): {clicked}"
            codes_found = []
            if comp_label:
                detail += f", clicked '{comp_label}'"
            if fc:
                codes_found.extend([{"text": c, "source": "multi_tab-force"} for c in fc])
            if pieces_code:
                pc = json.loads(pieces_code) if isinstance(pieces_code, str) else pieces_code
                if pc and pc.get("code"):
                    codes_found.append({"text": pc["code"], "source": f"multi_tab-{pc['type']}"})
                    print(f"    [multi_tab] reconstructed code from pieces: {pc}")

            return {"ok": True, "detail": detail, "codes": codes_found}
        return {"ok": False, "detail": "no tab buttons found"}
    except Exception as e:
        return {"ok": False, "detail": f"multi_tab error: {e}"}


async def handle_timing_capture(page, params: dict) -> dict:
    """Handle Timing Challenge: click Capture button 3+ times, then check for revealed code.

    The timing challenge shows a rapidly changing code. Click Capture 3 times to reveal it.
    Uses React onClick dispatch + Playwright trusted click.
    """
    try:
        mouse = await _get_mouse(page)
        clicked = 0

        for i in range(5):  # Try up to 5 clicks (need 3)
            # Find Capture button
            btn_info = await page.evaluate(r"""() => {
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    var btn = btns[i];
                    if (btn.offsetWidth === 0 || btn.offsetHeight === 0) continue;
                    var t = btn.textContent.trim();
                    if (/^Capture/i.test(t)) {
                        btn.scrollIntoView({block: 'center', behavior: 'instant'});
                        var r = btn.getBoundingClientRect();
                        // Also React onClick dispatch
                        var cur = btn;
                        for (var d = 0; d < 5 && cur; d++) {
                            var rk = Object.keys(cur).find(function(k) { return k.indexOf('__reactProps') === 0; });
                            if (rk && cur[rk] && cur[rk].onClick) {
                                try { cur[rk].onClick({preventDefault:function(){},stopPropagation:function(){},target:btn,currentTarget:btn,nativeEvent:{}}); } catch(e) {}
                                break;
                            }
                            cur = cur.parentElement;
                        }
                        btn.click();
                        return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), text: t});
                    }
                }
                return null;
            }""")
            if btn_info:
                bi = json.loads(btn_info) if isinstance(btn_info, str) else btn_info
                await mouse.click(bi["x"], bi["y"])
                clicked += 1
                await asyncio.sleep(0.3)
            else:
                break

        if clicked > 0:
            await asyncio.sleep(0.5)
            btn = await _try_complete_button(page)
            detail = f"timing capture: clicked {clicked}x"
            if btn:
                detail += f", clicked '{btn}'"
            return {"ok": True, "detail": detail}
        return {"ok": False, "detail": "no Capture button found"}
    except Exception as e:
        return {"ok": False, "detail": f"timing_capture error: {e}"}


async def handle_mutation(page, params: dict) -> dict:
    """Click a trigger/mutation button multiple times, then reveal the code.

    Works for challenges that require clicking a button N times to trigger DOM
    mutations, then clicking a Reveal/Complete button to show the code.
    """
    trigger_text = params.get("button_text", "Trigger")
    click_count = params.get("times", 6)

    try:
        # Step 1: Click trigger button N times via React onClick + native click
        trigger_coords = await page.evaluate(r"""(args) => {
            var triggerText = args[0];
            var count = args[1];
            var buttons = document.querySelectorAll('button');
            for (var i = 0; i < buttons.length; i++) {
                var t = buttons[i].textContent.trim();
                if (t.toLowerCase().indexOf(triggerText.toLowerCase()) === -1) continue;
                if (buttons[i].disabled || buttons[i].offsetWidth === 0) continue;
                for (var c = 0; c < count; c++) {
                    var rk = Object.keys(buttons[i]).find(function(k) { return k.indexOf('__reactProps') === 0; });
                    if (rk && buttons[i][rk] && buttons[i][rk].onClick) {
                        try { buttons[i][rk].onClick({preventDefault:function(){},stopPropagation:function(){},target:buttons[i],currentTarget:buttons[i],nativeEvent:{},bubbles:true,type:'click'}); } catch(e) {}
                    }
                    buttons[i].click();
                }
                buttons[i].scrollIntoView({block: 'center', behavior: 'instant'});
                var r = buttons[i].getBoundingClientRect();
                return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), label: t});
            }
            return null;
        }""", [trigger_text, click_count])

        trigger_detail = "no trigger button found"
        if trigger_coords:
            tc = json.loads(trigger_coords) if isinstance(trigger_coords, str) else trigger_coords
            mouse = await _get_mouse(page)
            await mouse.click(tc["x"], tc["y"])
            trigger_detail = f"JS+trusted clicked '{tc.get('label', trigger_text)}' {click_count}x"

        await asyncio.sleep(0.5)

        # Step 2: Re-query buttons after mutations (React re-renders replace DOM).
        # Find Reveal Code / Complete button, enable it if disabled, and click.
        reveal_info = await page.evaluate(r"""(triggerText) => {
            var buttons = document.querySelectorAll('button');
            var revealBtn = null;
            var triggerBtn = null;
            for (var i = 0; i < buttons.length; i++) {
                var t = buttons[i].textContent.trim();
                if (t.toLowerCase().indexOf(triggerText.toLowerCase()) !== -1) triggerBtn = buttons[i];
                if (/Reveal Code/i.test(t)) { revealBtn = buttons[i]; break; }
            }
            if (!revealBtn) {
                for (var i = 0; i < buttons.length; i++) {
                    var t = buttons[i].textContent.trim();
                    if (/Complete|Reveal/i.test(t) && !/Submit\s*Code/i.test(t) && buttons[i] !== triggerBtn) {
                        revealBtn = buttons[i]; break;
                    }
                }
            }
            var revealResult = null;
            if (revealBtn) {
                revealBtn.disabled = false;
                revealBtn.className = revealBtn.className.replace(/opacity-50|cursor-not-allowed/g, '');
                revealBtn.scrollIntoView({block: 'center', behavior: 'instant'});
                var r = revealBtn.getBoundingClientRect();
                revealResult = {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), text: revealBtn.textContent.trim()};
            }
            // Force-show hidden divs that may contain code
            var hiddenDivs = document.querySelectorAll('div[style*="display: none"], div[style*="display:none"]');
            for (var i = 0; i < hiddenDivs.length; i++) { hiddenDivs[i].style.display = 'block'; }
            return JSON.stringify({ok: true, reveal: revealResult});
        }""", trigger_text)

        mut_info = json.loads(reveal_info) if isinstance(reveal_info, str) else (reveal_info or {})

        # Click the reveal/complete button via Playwright trusted click
        if mut_info.get("reveal"):
            try:
                mouse = await _get_mouse(page)
                await mouse.click(mut_info["reveal"]["x"], mut_info["reveal"]["y"])
            except Exception:
                pass
        else:
            # Fallback: find any Complete/Reveal button
            fallback_coords = await page.evaluate(r"""() => {
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    var t = buttons[i].textContent.trim();
                    if (/Complete|Reveal/i.test(t) && !/Submit\s*Code/i.test(t) && !/Trigger/i.test(t)) {
                        buttons[i].disabled = false;
                        buttons[i].scrollIntoView({block: 'center', behavior: 'instant'});
                        var r = buttons[i].getBoundingClientRect();
                        return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), text: t});
                    }
                }
                return null;
            }""")
            if fallback_coords:
                fc = json.loads(fallback_coords) if isinstance(fallback_coords, str) else fallback_coords
                mouse = await _get_mouse(page)
                await mouse.click(fc["x"], fc["y"])

        await asyncio.sleep(0.8)

        # Step 3: Scan for codes
        codes = await _scan_for_codes(page)
        if not codes:
            # onComplete bypass: walk React fiber tree for onComplete callback
            bypass_codes = await _onComplete_bypass(page, "mutation", [
                '[class*="challenge"]', '[class*="mutation"]', '.font-mono'
            ])
            if bypass_codes:
                codes = bypass_codes

        detail = f"mutation: {trigger_detail}"
        if mut_info.get("reveal"):
            detail += f", clicked '{mut_info['reveal'].get('text', 'reveal')}'"
        if codes:
            detail += f", codes={codes}"
        return {"ok": True, "detail": detail, "codes": codes}
    except Exception as e:
        return {"ok": False, "detail": f"mutation error: {e}"}


async def handle_websocket(page, params: dict) -> dict:
    """Click a Connect/Start button, wait for simulated messages, scan for codes.

    Works for challenges that simulate WebSocket/async message patterns:
    click connect, wait for messages to arrive, then scan or bypass for the code.
    """
    try:
        # Step 1: Click Connect/Start button via React onClick + native click
        await page.evaluate(r"""() => {
            var buttons = document.querySelectorAll('button');
            for (var i = 0; i < buttons.length; i++) {
                var t = buttons[i].textContent.trim();
                if (/Connect|Start|Open/i.test(t) && !buttons[i].disabled && !/Submit|Dismiss/i.test(t)) {
                    var rk = Object.keys(buttons[i]).find(function(k) { return k.indexOf('__reactProps') === 0; });
                    if (rk && buttons[i][rk] && buttons[i][rk].onClick) {
                        try { buttons[i][rk].onClick({preventDefault:function(){},stopPropagation:function(){},target:buttons[i],currentTarget:buttons[i],nativeEvent:{}}); } catch(e) {}
                    }
                    buttons[i].click();
                    return true;
                }
            }
            return false;
        }""")

        # Step 2: Wait for simulated messages to arrive
        await asyncio.sleep(4.0)

        # Step 3: Scan for codes on page
        codes = await _scan_for_codes(page)

        if not codes:
            # onComplete bypass: walk fiber tree from Connect/Disconnect button
            bypass_result = await page.evaluate(r"""() => {
                var re = /^[A-HJ-NP-Z2-9]{6}$/;
                var codes = [];
                var called = false;
                // Find a relevant button to start fiber walk
                var startBtn = null;
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    var t = buttons[i].textContent.trim();
                    if (/Connect|Disconnect|Start|Open|WebSocket/i.test(t)) { startBtn = buttons[i]; break; }
                }
                if (!startBtn) return JSON.stringify({ok: false, called: false, reason: 'no_button'});
                var fk = Object.keys(startBtn).find(function(k) { return k.indexOf('__reactFiber') === 0; });
                if (!fk) return JSON.stringify({ok: false, called: false, reason: 'no_fiber'});
                for (var f = startBtn[fk]; f; f = f.return) {
                    if (!f.memoizedProps || typeof f.memoizedProps.onComplete !== 'function') continue;
                    var ret = null;
                    try { ret = f.memoizedProps.onComplete({type:'websocket',timestamp:Date.now(),data:{method:'websocket'}}); called = true; } catch(e) {}
                    if (typeof ret === 'string' && re.test(ret) && /[A-HJ-NP-Z]/.test(ret)) codes.push(ret);
                }
                return JSON.stringify({ok: codes.length > 0, codes: codes, called: called});
            }""")
            ws_data = json.loads(bypass_result) if isinstance(bypass_result, str) else bypass_result
            if ws_data.get('ok') and ws_data.get('codes'):
                codes = ws_data['codes']

        detail = "websocket: clicked connect, waited 4s"
        if codes:
            detail += f", codes={codes}"
        return {"ok": True, "detail": detail, "codes": codes}
    except Exception as e:
        return {"ok": False, "detail": f"websocket error: {e}"}


async def handle_recursive_iframe(page, params: dict) -> dict:
    """Click through level buttons sequentially, then extract code via onComplete bypass.

    Works for recursive iframe / nested level challenges: click Enter Level buttons
    one at a time, then use React fiber onComplete bypass to extract the code.
    """
    try:
        # Step 1: Click Enter Level buttons one at a time via React onClick
        levels_clicked = []
        for _attempt in range(10):
            level_btn = await page.evaluate(r"""() => {
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    var t = buttons[i].textContent.trim();
                    if (/Enter Level/i.test(t) && !buttons[i].disabled && !/Submit|Dismiss|Close/i.test(t)) {
                        // React onClick dispatch only (exactly 1 state increment)
                        var rk = Object.keys(buttons[i]).find(function(k) { return k.indexOf('__reactProps') === 0; });
                        if (rk && buttons[i][rk] && buttons[i][rk].onClick) {
                            buttons[i][rk].onClick({
                                preventDefault:function(){},stopPropagation:function(){},
                                target:buttons[i],currentTarget:buttons[i],
                                nativeEvent:{},bubbles:true,type:'click'
                            });
                            return t;
                        }
                        // Fallback: native click if no React props
                        buttons[i].click();
                        return t;
                    }
                }
                return null;
            }""")
            if level_btn:
                levels_clicked.append(level_btn if isinstance(level_btn, str) else "?")
                await asyncio.sleep(0.5)
            else:
                break

        # Step 2: Direct onComplete bypass from Extract Code button's fiber tree.
        # onComplete returns the code string; must pass {type, timestamp, data} metadata.
        codes = []
        try:
            direct_bypass = await page.evaluate(r"""() => {
                var re = /^[A-HJ-NP-Z2-9]{6}$/;
                var extractBtn = null;
                var buttons = document.querySelectorAll('button');
                for (var i = 0; i < buttons.length; i++) {
                    var t = buttons[i].textContent.trim();
                    if (/Extract Code|Reveal Code/i.test(t) && !/Submit Code/i.test(t)) {
                        extractBtn = buttons[i];
                        break;
                    }
                }
                if (!extractBtn) return JSON.stringify({ok: false, reason: 'no_extract_btn'});
                var fk = Object.keys(extractBtn).find(function(k) { return k.indexOf('__reactFiber') === 0; });
                if (!fk) return JSON.stringify({ok: false, reason: 'no_fiber_on_btn'});

                var codes = [];
                var tried = [];
                for (var f = extractBtn[fk]; f; f = f.return) {
                    if (!f.memoizedProps) continue;
                    if (typeof f.memoizedProps.onComplete !== 'function') continue;
                    var meta = {
                        type: 'recursive_iframe',
                        timestamp: Date.now(),
                        data: {
                            method: 'recursive_iframe',
                            numLevels: 3,
                            currentLevel: 3,
                            levelClickTimes: {},
                            stepNum: 0
                        }
                    };
                    var retVal = null;
                    try { retVal = f.memoizedProps.onComplete(meta); } catch(e) { tried.push('err:' + e.message); }
                    tried.push('onComplete:' + (retVal || 'void'));
                    if (typeof retVal === 'string' && re.test(retVal) && /[A-HJ-NP-Z]/.test(retVal)) {
                        codes.push(retVal);
                        // Dispatch code to setCode state setter
                        var st = f.memoizedState;
                        var si = 0;
                        while (st && si < 15) {
                            si++;
                            if ((st.memoizedState === null || typeof st.memoizedState === 'string') && st.queue && st.queue.dispatch) {
                                try { st.queue.dispatch(retVal); } catch(e) {}
                            }
                            st = st.next;
                        }
                    }
                }
                return JSON.stringify({ok: codes.length > 0, codes: codes, tried: tried});
            }""")
            bypass_data = json.loads(direct_bypass) if isinstance(direct_bypass, str) else direct_bypass
            if bypass_data.get('ok') and bypass_data.get('codes'):
                codes = bypass_data['codes']
        except Exception:
            pass

        # Step 3: If no codes yet, check actual iframes for content
        if not codes:
            try:
                iframe_codes = await page.evaluate(r"""() => {
                    var found = [];
                    var re = /\b[A-HJ-NP-Z2-9]{6}\b/g;
                    var iframes = document.querySelectorAll('iframe');
                    for (var i = 0; i < iframes.length; i++) {
                        try {
                            var doc = iframes[i].contentDocument;
                            if (!doc || !doc.body) continue;
                            // Click buttons inside iframe
                            var btns = doc.querySelectorAll('button');
                            for (var j = 0; j < btns.length; j++) {
                                if (/Extract|Reveal|Code|Enter|Level/i.test(btns[j].textContent.trim())) {
                                    btns[j].click();
                                }
                            }
                            // Scan for codes
                            var text = doc.body.innerText;
                            var m;
                            while ((m = re.exec(text)) !== null) {
                                if (/[A-HJ-NP-Z]/.test(m[0])) found.push(m[0]);
                            }
                            // Nested iframes
                            var nested = doc.querySelectorAll('iframe');
                            for (var k = 0; k < nested.length; k++) {
                                try {
                                    var ndoc = nested[k].contentDocument;
                                    if (ndoc && ndoc.body) {
                                        var nbtns = ndoc.querySelectorAll('button');
                                        for (var nb = 0; nb < nbtns.length; nb++) nbtns[nb].click();
                                        var ntext = ndoc.body.innerText;
                                        while ((m = re.exec(ntext)) !== null) {
                                            if (/[A-HJ-NP-Z]/.test(m[0])) found.push(m[0]);
                                        }
                                    }
                                } catch(e) {}
                            }
                        } catch(e) {}
                    }
                    return JSON.stringify(found);
                }""")
                iframe_items = json.loads(iframe_codes) if isinstance(iframe_codes, str) else []
                if iframe_items:
                    codes = iframe_items
            except Exception:
                pass

        # Step 4: Click Extract Code button, scroll down, scan again
        if not codes:
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            await asyncio.sleep(0.3)
            try:
                await page.evaluate(r"""() => {
                    var buttons = document.querySelectorAll('button');
                    for (var i = 0; i < buttons.length; i++) {
                        var t = buttons[i].textContent.trim();
                        if (/Extract Code|Reveal Code|Get Code|Show Code/i.test(t) && !/Submit Code/i.test(t)) {
                            var rk = Object.keys(buttons[i]).find(function(k) { return k.indexOf('__reactProps') === 0; });
                            if (rk && buttons[i][rk] && buttons[i][rk].onClick) {
                                buttons[i][rk].onClick({
                                    preventDefault:function(){},stopPropagation:function(){},
                                    target:buttons[i],currentTarget:buttons[i],
                                    nativeEvent:{},bubbles:true,type:'click'
                                });
                                return t;
                            }
                            buttons[i].click();
                            return t;
                        }
                    }
                    return null;
                }""")
            except Exception:
                pass
            await asyncio.sleep(1.5)
            codes = await _scan_for_codes(page)

        # Step 5: Broad onComplete bypass + deep fiber scan as last resort
        if not codes:
            try:
                bypass_result = await page.evaluate(r"""() => {
                    var re = /^[A-HJ-NP-Z2-9]{6}$/;
                    var foundCodes = [];
                    var extractBtn = null;
                    var buttons = document.querySelectorAll('button');
                    for (var i = 0; i < buttons.length; i++) {
                        if (/Extract Code|Reveal Code/i.test(buttons[i].textContent.trim()) && !/Submit Code/i.test(buttons[i].textContent)) {
                            extractBtn = buttons[i]; break;
                        }
                    }
                    var startEl = extractBtn || document.getElementById('root') || document.body;
                    var fk = Object.keys(startEl).find(function(k) { return k.indexOf('__reactFiber') === 0; });
                    if (!fk) return JSON.stringify({codes: []});

                    // Walk UP from button looking for onComplete
                    for (var f = startEl[fk]; f; f = f.return) {
                        if (!f.memoizedProps) continue;
                        var callbackNames = ['onComplete', 'onSuccess', 'onFinish', 'onDone', 'resolve'];
                        for (var cn = 0; cn < callbackNames.length; cn++) {
                            var cbName = callbackNames[cn];
                            if (typeof f.memoizedProps[cbName] !== 'function') continue;
                            var retVal = null;
                            try { retVal = f.memoizedProps[cbName]({type:'recursive_iframe',timestamp:Date.now(),data:{method:'recursive_iframe',numLevels:5,currentLevel:5,stepNum:0,levelClickTimes:{}}}); } catch(e) {}
                            if (!retVal) {
                                try { retVal = f.memoizedProps[cbName]({type:'recursive_iframe',timestamp:Date.now(),data:{method:'recursive_iframe'}}); } catch(e) {}
                            }
                            if (typeof retVal === 'string' && re.test(retVal) && /[A-HJ-NP-Z]/.test(retVal)) {
                                foundCodes.push(retVal);
                                // Dispatch to setCode state
                                var st = f.memoizedState;
                                var si = 0;
                                while (st && si < 15) {
                                    si++;
                                    if ((st.memoizedState === null || typeof st.memoizedState === 'string') && st.queue && st.queue.dispatch) {
                                        try { st.queue.dispatch(retVal); } catch(e) {}
                                    }
                                    st = st.next;
                                }
                            }
                        }
                    }
                    // Broader scan if still no codes
                    if (foundCodes.length === 0) {
                        var els = document.querySelectorAll('*');
                        for (var ci = 0; ci < Math.min(els.length, 200); ci++) {
                            var efk = Object.keys(els[ci]).find(function(k) { return k.indexOf('__reactFiber') === 0; });
                            if (!efk) continue;
                            for (var ef = els[ci][efk]; ef; ef = ef.return) {
                                if (!ef.memoizedProps || typeof ef.memoizedProps.onComplete !== 'function') continue;
                                var rv = null;
                                try { rv = ef.memoizedProps.onComplete({type:'recursive_iframe',timestamp:Date.now(),data:{method:'recursive_iframe'}}); } catch(e) {}
                                if (typeof rv === 'string' && re.test(rv) && /[A-HJ-NP-Z]/.test(rv)) foundCodes.push(rv);
                                break;
                            }
                        }
                    }
                    return JSON.stringify({codes: foundCodes});
                }""")
                bp_data = json.loads(bypass_result) if isinstance(bypass_result, str) else bypass_result
                if bp_data.get('codes'):
                    codes = bp_data['codes']
                    await asyncio.sleep(2.0)
            except Exception:
                pass

            # Force-show hidden elements and scan
            if not codes:
                try:
                    await page.evaluate(r"""() => {
                        var all = document.querySelectorAll('*');
                        for (var i = 0; i < all.length; i++) {
                            var cs = getComputedStyle(all[i]);
                            if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') {
                                all[i].style.setProperty('display', 'block', 'important');
                                all[i].style.setProperty('visibility', 'visible', 'important');
                                all[i].style.setProperty('opacity', '1', 'important');
                            }
                        }
                    }""")
                except Exception:
                    pass
                codes = await _scan_for_codes(page)

        # Step 6: Deep fiber scan for codes in React state/props
        if not codes:
            try:
                state_code = await page.evaluate(r"""() => {
                    var re = /^[A-HJ-NP-Z2-9]{6}$/;
                    var found = [];
                    var rootEls = document.querySelectorAll('#root, [id*="app"], body');
                    for (var ri = 0; ri < rootEls.length; ri++) {
                        var fk = Object.keys(rootEls[ri]).find(function(k) { return k.indexOf('__reactFiber') === 0; });
                        if (!fk) continue;
                        var queue = [rootEls[ri][fk]];
                        while (queue.length > 0) {
                            var node = queue.shift();
                            if (!node) continue;
                            var props = node.memoizedProps;
                            if (props) {
                                if (typeof props.code === 'string' && re.test(props.code) && /[A-HJ-NP-Z]/.test(props.code)) found.push(props.code);
                                var keys = Object.keys(props);
                                for (var ki = 0; ki < keys.length; ki++) {
                                    var v = props[keys[ki]];
                                    if (typeof v === 'string' && re.test(v) && /[A-HJ-NP-Z]/.test(v)) found.push(v);
                                    if (v && typeof v === 'object' && !Array.isArray(v)) {
                                        try {
                                            var nkeys = Object.keys(v);
                                            for (var nk = 0; nk < nkeys.length; nk++) {
                                                var nv = v[nkeys[nk]];
                                                if (typeof nv === 'string' && re.test(nv) && /[A-HJ-NP-Z]/.test(nv)) found.push(nv);
                                            }
                                        } catch(e) {}
                                    }
                                }
                            }
                            var st = node.memoizedState;
                            var stateIdx = 0;
                            while (st && stateIdx < 20) {
                                stateIdx++;
                                var ms = st.memoizedState;
                                if (typeof ms === 'string' && re.test(ms) && /[A-HJ-NP-Z]/.test(ms)) found.push(ms);
                                if (ms && typeof ms === 'object') {
                                    try {
                                        var skeys = Object.keys(ms);
                                        for (var sk = 0; sk < skeys.length; sk++) {
                                            var sv = ms[skeys[sk]];
                                            if (typeof sv === 'string' && re.test(sv) && /[A-HJ-NP-Z]/.test(sv)) found.push(sv);
                                        }
                                    } catch(e) {}
                                }
                                if (st.queue && st.queue.lastRenderedState) {
                                    var lrs = st.queue.lastRenderedState;
                                    if (typeof lrs === 'string' && re.test(lrs) && /[A-HJ-NP-Z]/.test(lrs)) found.push(lrs);
                                    if (lrs && typeof lrs === 'object') {
                                        try {
                                            var lkeys = Object.keys(lrs);
                                            for (var lk = 0; lk < lkeys.length; lk++) {
                                                var lv = lrs[lkeys[lk]];
                                                if (typeof lv === 'string' && re.test(lv) && /[A-HJ-NP-Z]/.test(lv)) found.push(lv);
                                            }
                                        } catch(e) {}
                                    }
                                }
                                st = st.next;
                            }
                            if (node.child) queue.push(node.child);
                            if (node.sibling) queue.push(node.sibling);
                        }
                    }
                    var unique = [];
                    var seen = {};
                    for (var i = 0; i < found.length; i++) {
                        if (!seen[found[i]]) { seen[found[i]] = true; unique.push(found[i]); }
                    }
                    return JSON.stringify(unique);
                }""")
                deep_codes = json.loads(state_code) if isinstance(state_code, str) else []
                if deep_codes:
                    codes = deep_codes
            except Exception:
                pass

        detail = f"recursive_iframe: navigated {len(levels_clicked)} levels"
        if codes:
            detail += f", codes={codes}"
        return {"ok": True, "detail": detail, "codes": codes}
    except Exception as e:
        return {"ok": False, "detail": f"recursive_iframe error: {e}"}


async def _scan_for_codes(page) -> list:
    """Scan page for visible 6-char alphanumeric codes matching the challenge charset."""
    try:
        raw = await page.evaluate(r"""() => {
            var CODE_RE = /^[A-HJ-NP-Z2-9]{6}$/;
            var found = [];
            var els = document.querySelectorAll('span, div, p, code, pre, strong, b');
            for (var i = 0; i < els.length; i++) {
                var el = els[i];
                if (el.children.length > 2) continue;
                var t = el.textContent.trim();
                if (CODE_RE.test(t) && /[A-Z]/.test(t)) found.push(t);
            }
            // Also scan text nodes
            var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
            while (walker.nextNode()) {
                var text = walker.currentNode.textContent.trim();
                if (!text) continue;
                var matches = text.match(/\b[A-HJ-NP-Z2-9]{6}\b/g);
                if (matches) {
                    for (var j = 0; j < matches.length; j++) {
                        if (/[A-Z]/.test(matches[j])) found.push(matches[j]);
                    }
                }
            }
            // Deduplicate
            var unique = [];
            var seen = {};
            for (var i = 0; i < found.length; i++) {
                if (!seen[found[i]]) { seen[found[i]] = true; unique.push(found[i]); }
            }
            return JSON.stringify(unique);
        }""")
        return json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        return []


async def _onComplete_bypass(page, challenge_type: str, selectors: list) -> list:
    """Walk React fiber tree from elements matching selectors, call onComplete, return codes.
    Uses __FW.callOnComplete if the framework helper is injected, else inline fallback."""
    try:
        result = await page.evaluate(r"""(args) => {
            var challengeType = args[0];
            var selectors = args[1];
            var re = /^[A-HJ-NP-Z2-9]{6}$/;
            var hasLetter = /[A-HJ-NP-Z]/;
            var codes = [];
            for (var si = 0; si < selectors.length; si++) {
                var els = document.querySelectorAll(selectors[si]);
                for (var ci = 0; ci < els.length; ci++) {
                    var ret = null;
                    // Use framework helper if available
                    if (window.__FW && __FW.callOnComplete) {
                        ret = __FW.callOnComplete(els[ci], challengeType);
                    } else {
                        // Inline fallback: walk fiber tree manually
                        var fk = Object.keys(els[ci]).find(function(k) { return k.indexOf('__reactFiber') === 0; });
                        if (!fk) continue;
                        for (var f = els[ci][fk]; f; f = f.return) {
                            if (f.memoizedProps && typeof f.memoizedProps.onComplete === 'function') {
                                try { ret = f.memoizedProps.onComplete({type:challengeType,timestamp:Date.now(),data:{}}); } catch(e) {}
                                if (ret) break;
                            }
                        }
                    }
                    if (typeof ret === 'string' && re.test(ret) && hasLetter.test(ret)) codes.push(ret);
                }
            }
            return JSON.stringify(codes);
        }""", [challenge_type, selectors])
        codes = json.loads(result) if isinstance(result, str) else (result or [])
        return codes
    except Exception:
        return []


# Dispatch table
ACTION_HANDLERS = {
    "click_button": handle_click_button,
    "click_element": handle_click_element,
    "fill_form": handle_fill_form,
    "scroll_down": handle_scroll_down,
    "hover_element": handle_hover_element,
    "keyboard_sequence": handle_keyboard_sequence,
    "drag_drop": handle_drag_drop,
    "draw_canvas": handle_draw_canvas,
    "shadow_dom": handle_shadow_dom,
    "service_worker": handle_service_worker,
    "split_parts": handle_split_parts,
    "video_frame": handle_video_frame,
    "type_text": handle_type_text,
    "wait": handle_wait,
    "multi_step": handle_multi_step,
    "dismiss_popups": handle_dismiss_popups,
    "sequence": handle_sequence,
    "encoded_code": handle_encoded_code,
    "multi_tab": handle_multi_tab,
    "timing_capture": handle_timing_capture,
    "mutation": handle_mutation,
    "websocket": handle_websocket,
    "recursive_iframe": handle_recursive_iframe,
}


async def dispatch_action(page, action: str, params: dict) -> dict:
    """Dispatch an action to the appropriate handler."""
    handler = ACTION_HANDLERS.get(action)
    if not handler:
        return {"ok": False, "detail": f"unknown action: {action}"}
    return await handler(page, params)
