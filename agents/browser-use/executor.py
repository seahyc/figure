"""
Executor: Playwright-based action execution for browser automation.

Each handler takes a browser-use page and params dict, executes the action
using JS for element finding + Playwright mouse/keyboard for trusted events,
and returns a result dict with {ok, detail}.

Action types: click, type, fill_form, hover, scroll, drag, draw,
press_keys, wait, evaluate_js, done.

NOTE: browser-use wraps Playwright pages — locator API (get_by_role, etc.) is
NOT available. All element finding must go through page.evaluate() and
mouse.click(x, y) for trusted events.
"""

import asyncio
import json
import re


# ── Internal helpers ─────────────────────────────────────────────────────────

async def _get_mouse(page):
    m = page.mouse
    if asyncio.iscoroutine(m):
        m = await m
    return m


async def _get_keyboard(page):
    try:
        k = page.keyboard
        if asyncio.iscoroutine(k):
            k = await k
        return k
    except AttributeError:
        pass
    try:
        pw_page = getattr(page, '_page', None) or getattr(page, 'page', None)
        if pw_page:
            return pw_page.keyboard
    except AttributeError:
        pass
    raise AttributeError("Cannot access keyboard from page object")


async def _fill_input_react(page, selector_js: str, value: str):
    """Fill an input field in a React app using framework-aware value setter."""
    return await page.evaluate(r"""(args) => {
        var selectorCode = args[0];
        var value = args[1];
        var inp = eval(selectorCode);
        if (!inp) return 'NOT_FOUND';
        inp.focus();
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


# ── JS: find button by text, return center coords ───────────────────────────

_FIND_BUTTON_JS = r"""(text) => {
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
    // Partial match
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
    // Search all clickable elements
    var allEls = document.querySelectorAll('div, span, a, p, label, [role="button"], [onclick]');
    for (var i = 0; i < allEls.length; i++) {
        var el = allEls[i];
        if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
        if (el.tagName === 'BUTTON') continue;
        var ct = el.textContent.trim();
        if (ct.length > 200) continue;
        if (ct === text || ct.toLowerCase() === text.toLowerCase()) {
            el.scrollIntoView({block: 'center', behavior: 'instant'});
            var r = el.getBoundingClientRect();
            return JSON.stringify({ok: true, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), label: ct.substring(0, 40), isButton: false});
        }
    }
    return JSON.stringify({ok: false, reason: 'element not found: ' + text});
}"""

_FIND_ELEMENT_JS = r"""(text) => {
    var lower = text.toLowerCase();
    var best = null;
    var bestSize = Infinity;
    var allEls = document.querySelectorAll('div, section, article, span, p, a, [role="button"], [onclick]');
    for (var i = 0; i < allEls.length; i++) {
        var el = allEls[i];
        if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
        var ct = el.textContent.trim();
        if (ct.length > 500) continue;
        if (ct.toLowerCase().indexOf(lower) !== -1) {
            var size = ct.length;
            if (size < bestSize) { bestSize = size; best = el; }
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


# ══════════════════════════════════════════════════════════════════════════════
# Action Handlers
# ══════════════════════════════════════════════════════════════════════════════

async def execute_click(page, params: dict) -> dict:
    """Click element(s) matching text. Uses React onClick dispatch for React elements,
    Playwright trusted click for non-React elements."""
    text = params.get("text", params.get("button_text", ""))
    times = params.get("times", 1)
    if not text:
        return {"ok": False, "detail": "no click target text provided"}

    mouse = await _get_mouse(page)
    clicked = 0

    for i in range(times):
        # Smart click: find element, check if React, dispatch accordingly
        try:
            result = await page.evaluate(r"""(text) => {
                // Find the matching element
                var match = null;
                var allEls = document.querySelectorAll('button, div, span, a, p, label, [role="button"], [onclick]');
                // Exact match first
                for (var i = 0; i < allEls.length; i++) {
                    var el = allEls[i];
                    if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                    var t = el.textContent.trim();
                    if (t === text || t.toLowerCase() === text.toLowerCase()) {
                        match = el; break;
                    }
                }
                // Partial match if no exact
                if (!match) {
                    for (var i = 0; i < allEls.length; i++) {
                        var el = allEls[i];
                        if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                        var t = el.textContent.trim();
                        if (t.length < 100 && t.toLowerCase().indexOf(text.toLowerCase()) !== -1) {
                            match = el; break;
                        }
                    }
                }
                if (!match) return JSON.stringify({ok: false, reason: 'element not found: ' + text});

                match.scrollIntoView({block: 'center', behavior: 'instant'});
                var r = match.getBoundingClientRect();
                var x = Math.round(r.x + r.width/2);
                var y = Math.round(r.y + r.height/2);

                // Check for React onClick handler
                var rk = Object.keys(match).find(function(k) { return k.indexOf('__reactProps') === 0; });
                var hasReactClick = rk && match[rk] && match[rk].onClick;

                if (hasReactClick) {
                    // React dispatch — works even with overlays
                    try {
                        match[rk].onClick({
                            preventDefault:function(){},stopPropagation:function(){},
                            target:match,currentTarget:match,nativeEvent:{},bubbles:true,type:'click'
                        });
                        return JSON.stringify({ok: true, method: 'react', label: match.textContent.trim().substring(0, 40)});
                    } catch(e) {}
                }

                // Return coords for Playwright trusted click
                return JSON.stringify({ok: true, method: 'playwright', x: x, y: y, label: match.textContent.trim().substring(0, 40)});
            }""", text)

            info = json.loads(result) if isinstance(result, str) else result
            if info and info.get("ok"):
                if info.get("method") == "playwright":
                    await mouse.click(info["x"], info["y"])
                clicked += 1
                if i < times - 1:
                    await asyncio.sleep(0.3)
                continue
        except Exception:
            pass

        # Fallback: simple JS click
        try:
            fallback = await page.evaluate(r"""(text) => {
                var allEls = document.querySelectorAll('button, a, div, span, [role="button"]');
                for (var i = 0; i < allEls.length; i++) {
                    var el = allEls[i];
                    if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                    var t = el.textContent.trim();
                    if (t === text || t.toLowerCase() === text.toLowerCase()) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }""", text)
            if fallback:
                clicked += 1
                if i < times - 1:
                    await asyncio.sleep(0.3)
                continue
        except Exception:
            pass

        break  # nothing worked

    if clicked == 0:
        return {"ok": False, "detail": f"element not found: {text[:40]}"}
    return {"ok": True, "detail": f"clicked '{text[:40]}' {clicked}x"}


async def execute_type(page, params: dict) -> dict:
    """Type text into an input field. Finds by selector, placeholder, or first visible input."""
    text = params.get("text", params.get("value", ""))
    selector = params.get("selector", "")
    placeholder = params.get("placeholder", "")

    if not text:
        return {"ok": False, "detail": "no text to type"}

    try:
        result = await page.evaluate("""(opts) => {
            var text = opts.text;
            var selector = opts.selector;
            var placeholder = opts.placeholder;
            var input = null;

            // Strategy 1: CSS selector
            if (selector) {
                input = document.querySelector(selector);
            }
            // Strategy 2: placeholder match
            if (!input && placeholder) {
                var inputs = document.querySelectorAll('input, textarea');
                for (var i = 0; i < inputs.length; i++) {
                    var ph = (inputs[i].placeholder || '').toLowerCase();
                    if (ph.indexOf(placeholder.toLowerCase()) !== -1) { input = inputs[i]; break; }
                }
            }
            // Strategy 3: first visible text input
            if (!input) {
                var inputs = document.querySelectorAll('input[type="text"], input[type="search"], input:not([type]), textarea');
                for (var i = 0; i < inputs.length; i++) {
                    if (inputs[i].offsetWidth > 0 && inputs[i].offsetHeight > 0) {
                        input = inputs[i]; break;
                    }
                }
            }
            if (!input) return JSON.stringify({ok: false, reason: 'no input found'});

            input.scrollIntoView({block: 'center', behavior: 'instant'});
            input.focus();
            // Use framework-aware fill if available (handles React/Vue controlled inputs)
            if (window.__FW && __FW.fillInput) {
                __FW.fillInput(input, text);
            } else {
                // Fallback: native setter + events for non-React pages
                try {
                    var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                    setter.call(input, text);
                } catch(e) {}
                input.value = text;
                input.setAttribute('value', text);
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.dispatchEvent(new Event('change', {bubbles: true}));
                var rk = Object.keys(input).find(function(k) { return k.indexOf('__reactProps') === 0; });
                if (rk && input[rk] && input[rk].onChange) {
                    try { input[rk].onChange({target: input}); } catch(e) {}
                }
            }
            return JSON.stringify({ok: true, placeholder: (input.placeholder || '').substring(0, 30)});
        }""", {"text": text, "selector": selector, "placeholder": placeholder})

        if isinstance(result, str):
            result = json.loads(result)
        if result and result.get("ok"):
            return {"ok": True, "detail": f"typed '{text[:30]}' into input ({result.get('placeholder', '')})"}
        return {"ok": False, "detail": f"type failed: {result.get('reason', 'unknown') if isinstance(result, dict) else result}"}
    except Exception as e:
        return {"ok": False, "detail": f"type error: {e}"}


async def execute_fill_form(page, params: dict) -> dict:
    """Fill a form field and optionally click a submit/solve button.

    params: {value, expression, placeholder, submit_button}
    """
    value = str(params.get("value", params.get("answer", "")))
    expression = params.get("expression", "")
    placeholder = params.get("placeholder", params.get("input_placeholder", ""))
    submit_btn = params.get("submit_button", "")

    if not value and expression:
        try:
            expr = expression.replace("x", "*").replace("\u00d7", "*").replace("\u00f7", "/")
            expr = re.sub(r'=\s*\?', '', expr).strip()
            value = str(int(eval(expr)))  # noqa: S307
        except Exception:
            return {"ok": False, "detail": f"cannot compute: {expression}"}

    if not value:
        return {"ok": False, "detail": "no value to fill"}

    # Fill the input using framework-aware method
    result = await page.evaluate("""(opts) => {
        var value = opts.value;
        var hint = opts.placeholder;
        var input = null;

        if (hint) {
            var inputs = document.querySelectorAll('input, textarea');
            for (var i = 0; i < inputs.length; i++) {
                var ph = (inputs[i].placeholder || '').toLowerCase();
                if (ph.indexOf(hint.toLowerCase()) !== -1) { input = inputs[i]; break; }
            }
        }
        if (!input) {
            var inputs = document.querySelectorAll('input[type="text"], input[type="number"], input[type="tel"], input:not([type]), textarea');
            for (var i = 0; i < inputs.length; i++) {
                var inp = inputs[i];
                if (inp.offsetWidth === 0 || inp.offsetHeight === 0) continue;
                input = inp; break;
            }
        }
        if (!input) return JSON.stringify({ok: false, reason: 'no input found'});

        input.scrollIntoView({block: 'center', behavior: 'instant'});
        input.focus();
        // Use framework-aware fill if available (handles React/Vue controlled inputs)
        if (window.__FW && __FW.fillInput) {
            __FW.fillInput(input, String(value));
        } else {
            var tracker = input._valueTracker;
            if (tracker) tracker.setValue('');
            try {
                var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                setter.call(input, String(value));
            } catch(e) { input.value = String(value); }
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));
            var rk = Object.keys(input).find(function(k) { return k.indexOf('__reactProps') === 0; });
            if (rk && input[rk] && input[rk].onChange) {
                try { input[rk].onChange({target: input}); } catch(e) {}
            }
        }
        return JSON.stringify({ok: true});
    }""", {"value": value, "placeholder": placeholder})

    info = json.loads(result) if isinstance(result, str) else result
    if not info or not info.get("ok"):
        return {"ok": False, "detail": f"fill_form: {info.get('reason', 'failed')}"}

    # Click submit button if specified
    detail = f"filled '{value}'"
    if submit_btn:
        await asyncio.sleep(0.2)
        btn_result = await execute_click(page, {"text": submit_btn})
        if btn_result.get("ok"):
            detail += f", clicked '{submit_btn}'"

    return {"ok": True, "detail": detail}


async def execute_hover(page, params: dict) -> dict:
    """Hover over an element to trigger hover effects."""
    text = params.get("text", params.get("hover_text", ""))
    seconds = max(params.get("seconds", 2.0), 1.0)

    mouse = await _get_mouse(page)

    try:
        # Find hover target — prioritize React onMouseEnter, then text match
        target = await page.evaluate(r"""(hoverText) => {
            var allEls = document.querySelectorAll('div, span, section, article, [role="button"]');
            var best = null;
            var bestScore = -1;

            for (var i = 0; i < allEls.length; i++) {
                var el = allEls[i];
                if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                var r = el.getBoundingClientRect();
                if (r.width < 20 || r.height < 20 || r.width > 900 || r.height > 700) continue;
                if (r.y + r.height < 0 || r.y > window.innerHeight) continue;

                var score = 0;
                var rk = Object.keys(el).find(function(k) { return k.indexOf('__reactProps') === 0; });
                if (rk && el[rk] && el[rk].onMouseEnter) score += 50;

                var s = getComputedStyle(el);
                if (s.cursor === 'pointer') score += 10;
                if (s.transition && s.transition !== 'none' && s.transition !== 'all 0s ease 0s') score += 5;

                var bg = s.backgroundColor;
                var WHITES = ['rgba(0, 0, 0, 0)', 'transparent', 'rgb(255, 255, 255)'];
                if (bg && WHITES.indexOf(bg) === -1) score += 10;

                if (hoverText) {
                    var t = el.textContent.trim().toLowerCase();
                    if (t.indexOf(hoverText.toLowerCase()) !== -1 && t.length < 100) score += 30;
                }

                if (score > bestScore) {
                    bestScore = score;
                    el.scrollIntoView({block: 'center', behavior: 'instant'});
                    r = el.getBoundingClientRect();
                    best = {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), score: score};
                }
            }
            return best ? JSON.stringify(best) : null;
        }""", text)

        if not target:
            return {"ok": False, "detail": "no hover target found"}

        info = json.loads(target) if isinstance(target, str) else target

        # Move mouse and dispatch hover events
        x, y = info["x"], info["y"]
        await mouse.move(x, y)
        await page.evaluate("""(pos) => {
            var el = document.elementFromPoint(pos.x, pos.y);
            if (!el) return;
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
            el.dispatchEvent(new MouseEvent('mouseenter', {bubbles: false, clientX: pos.x, clientY: pos.y}));
            el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true, clientX: pos.x, clientY: pos.y}));
        }""", {"x": x, "y": y})
        await asyncio.sleep(seconds)

        return {"ok": True, "detail": f"hovered at ({x},{y}) for {seconds}s"}
    except Exception as e:
        return {"ok": False, "detail": f"hover error: {e}"}


async def execute_scroll(page, params: dict) -> dict:
    """Scroll the page. direction: 'down' (default), 'up'. pixels: amount."""
    direction = params.get("direction", "down")
    pixels = params.get("pixels", 600)
    if direction == "up":
        pixels = -abs(pixels)

    try:
        result = await page.evaluate(f"""() => {{
            var before = window.scrollY;
            window.scrollBy(0, {pixels});
            var after = window.scrollY;
            if (after !== before) return JSON.stringify({{ok: true, scrolled: after - before, total: after}});
            var containers = document.querySelectorAll('div, main, section');
            for (var i = 0; i < containers.length; i++) {{
                var el = containers[i];
                if (el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 200) {{
                    var b = el.scrollTop;
                    el.scrollBy(0, {pixels});
                    if (el.scrollTop !== b) return JSON.stringify({{ok: true, scrolled: el.scrollTop - b, total: el.scrollTop, container: true}});
                }}
            }}
            return JSON.stringify({{ok: true, scrolled: 0, total: after}});
        }}""")
        info = json.loads(result) if isinstance(result, str) else result
        return {"ok": True, "detail": f"scrolled {info.get('scrolled', pixels)}px {direction}"}
    except Exception as e:
        return {"ok": False, "detail": f"scroll error: {e}"}


async def execute_drag(page, params: dict) -> dict:
    """Drag element(s) to drop zones. Finds draggables and targets automatically."""
    try:
        # Find drag sources and drop targets
        raw = await page.evaluate(r"""() => {
            var sources = [];
            var targets = [];
            document.querySelectorAll('[draggable="true"]').forEach(function(el) {
                el.scrollIntoView({block: 'center', behavior: 'instant'});
                var r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0)
                    sources.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), text: el.textContent.trim().substring(0, 15)});
            });
            if (sources.length === 0) {
                document.querySelectorAll('div, span, li, img').forEach(function(el) {
                    var r = el.getBoundingClientRect();
                    if (r.width < 10 || r.height < 10 || r.width > 300 || r.height > 300) return;
                    var cs = getComputedStyle(el);
                    if (cs.cursor === 'grab' || cs.cursor === 'move') {
                        el.scrollIntoView({block: 'center', behavior: 'instant'});
                        r = el.getBoundingClientRect();
                        sources.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), text: el.textContent.trim().substring(0, 15)});
                    }
                });
            }
            // Drop targets: React onDrop, data attrs, dashed borders
            document.querySelectorAll('div, section, span').forEach(function(el) {
                var rk = Object.keys(el).find(function(k) { return k.indexOf('__reactProps') === 0; });
                if (rk && el[rk] && (el[rk].onDrop || el[rk].onDragOver)) {
                    var r = el.getBoundingClientRect();
                    if (r.width > 5 && r.height > 5)
                        targets.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
                }
            });
            if (targets.length === 0) {
                document.querySelectorAll('[data-slot], [data-droppable], [data-drop], [data-target]').forEach(function(el) {
                    var r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0)
                        targets.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
                });
            }
            if (targets.length === 0) {
                document.querySelectorAll('div').forEach(function(el) {
                    var cs = getComputedStyle(el);
                    if (cs.borderStyle === 'dashed') {
                        var r = el.getBoundingClientRect();
                        if (r.width >= 30 && r.height >= 30)
                            targets.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
                    }
                });
            }
            return JSON.stringify({sources: sources, targets: targets});
        }""")

        pairs = json.loads(raw) if isinstance(raw, str) else raw
        sources = pairs.get("sources", [])
        targets = pairs.get("targets", [])

        if not sources:
            return {"ok": False, "detail": "no draggable elements found"}
        if not targets:
            return {"ok": False, "detail": f"found {len(sources)} sources but 0 drop targets"}

        # Try React prop dispatch first
        native_count = await page.evaluate("""() => {
            var sources = [];
            document.querySelectorAll('[draggable="true"]').forEach(function(el) {
                var rk = Object.keys(el).find(function(k) { return k.indexOf('__reactProps') === 0; });
                if (rk && el[rk] && el[rk].onDragStart) sources.push({el: el, props: el[rk]});
            });
            var targets = [];
            document.querySelectorAll('div, section').forEach(function(el) {
                var rk = Object.keys(el).find(function(k) { return k.indexOf('__reactProps') === 0; });
                if (rk && el[rk] && el[rk].onDrop) targets.push({el: el, props: el[rk]});
            });
            if (sources.length === 0 || targets.length === 0) return 0;
            var count = 0;
            for (var i = 0; i < Math.min(sources.length, targets.length); i++) {
                try {
                    var _data = {};
                    var fakeDT = {setData: function(t,v){_data[t]=v;}, getData: function(t){return _data[t]||'';}, effectAllowed:'move', dropEffect:'move'};
                    sources[i].props.onDragStart({dataTransfer:fakeDT, target:sources[i].el, preventDefault:function(){}, stopPropagation:function(){}});
                    if (targets[i].props.onDragOver) targets[i].props.onDragOver({dataTransfer:fakeDT, preventDefault:function(){}, stopPropagation:function(){}});
                    targets[i].props.onDrop({dataTransfer:fakeDT, target:targets[i].el, preventDefault:function(){}, stopPropagation:function(){}});
                    count++;
                } catch(e) {}
            }
            return count;
        }""")

        nc = int(native_count) if native_count else 0
        if nc > 0:
            await asyncio.sleep(1.0)
            return {"ok": True, "detail": f"React drag dispatched {nc} pairs"}

        # Fallback: Playwright mouse drags
        mouse = await _get_mouse(page)
        dragged = 0
        for i, src in enumerate(sources[:6]):
            tgt = targets[i] if i < len(targets) else targets[-1]
            sx, sy = int(src["x"]), int(src["y"])
            tx, ty = int(tgt["x"]), int(tgt["y"])
            await mouse.move(sx, sy)
            await asyncio.sleep(0.1)
            await mouse.down()
            for step in range(1, 16):
                await mouse.move(sx + (tx - sx) * step / 15, sy + (ty - sy) * step / 15)
                await asyncio.sleep(0.015)
            await mouse.up()
            await asyncio.sleep(0.3)
            dragged += 1

        return {"ok": dragged > 0, "detail": f"mouse-dragged {dragged} elements"}
    except Exception as e:
        return {"ok": False, "detail": f"drag error: {e}"}


async def execute_draw(page, params: dict) -> dict:
    """Draw strokes on a canvas element."""
    selector = params.get("selector", "canvas")
    num_strokes = params.get("strokes", 4)

    try:
        canvas_box = await page.evaluate(f"""() => {{
            var c = document.querySelector('{selector}');
            if (!c) return null;
            c.scrollIntoView({{block: 'center', behavior: 'instant'}});
            var r = c.getBoundingClientRect();
            return JSON.stringify({{x: r.x, y: r.y, w: r.width, h: r.height}});
        }}""")

        if not canvas_box:
            return {"ok": False, "detail": f"no element found: {selector}"}
        if isinstance(canvas_box, str):
            canvas_box = json.loads(canvas_box)

        ox, oy, w, h = canvas_box["x"], canvas_box["y"], canvas_box["w"], canvas_box["h"]
        if w < 10 or h < 10:
            return {"ok": False, "detail": f"canvas too small: {w}x{h}"}
        margin = min(20, w * 0.1)

        # JS native mouse event dispatch
        drawn = await page.evaluate("""(args) => {
            var ox = args.ox, oy = args.oy, w = args.w, h = args.h, margin = args.margin, strokes = args.strokes;
            var c = document.querySelector(args.selector);
            if (!c) return 0;
            var drawn = 0;
            for (var i = 0; i < strokes; i++) {
                var yFrac = (i + 1) / (strokes + 1);
                var sx = ox + margin, sy = oy + h * yFrac;
                var ex = ox + w - margin, ey = sy + h * 0.05;
                c.dispatchEvent(new MouseEvent('mousedown', {clientX: sx, clientY: sy, bubbles: true}));
                for (var step = 1; step <= 10; step++) {
                    var frac = step / 10;
                    c.dispatchEvent(new MouseEvent('mousemove', {clientX: sx + (ex-sx)*frac, clientY: sy + (ey-sy)*frac, bubbles: true}));
                }
                c.dispatchEvent(new MouseEvent('mouseup', {clientX: ex, clientY: ey, bubbles: true}));
                drawn++;
            }
            return drawn;
        }""", {"ox": ox, "oy": oy, "w": w, "h": h, "margin": margin, "strokes": num_strokes, "selector": selector})

        # Also Playwright trusted mouse strokes
        mouse = await _get_mouse(page)
        for i in range(min(2, num_strokes)):
            cx = ox + margin + (w - 2 * margin) * (i + 1) / 3
            cy = oy + h * 0.5
            await mouse.move(cx, cy)
            await mouse.down()
            await mouse.move(cx + w * 0.3, cy + h * 0.05)
            await mouse.up()
            await asyncio.sleep(0.1)

        # React props drawing
        react_strokes = await page.evaluate(f"""() => {{
            var c = document.querySelector('{selector}');
            if (!c) return 0;
            var rk = Object.keys(c).find(function(k) {{ return k.indexOf('__reactProps') === 0; }});
            if (!rk || !c[rk] || !c[rk].onMouseDown) return 0;
            var r = c.getBoundingClientRect();
            var drawn = 0;
            var margin = Math.min(20, r.width * 0.1);
            for (var s = 0; s < {num_strokes}; s++) {{
                var yFrac = (s + 1) / ({num_strokes} + 1);
                var sx = r.left + margin, sy = r.top + r.height * yFrac;
                var ex = r.left + r.width - margin, ey = sy + r.height * 0.03;
                c[rk].onMouseDown({{clientX: sx, clientY: sy, preventDefault: function(){{}}, target: c, currentTarget: c, nativeEvent: new MouseEvent('mousedown')}});
                for (var step = 1; step <= 8; step++) {{
                    var frac = step / 8;
                    if (c[rk].onMouseMove) c[rk].onMouseMove({{clientX: sx+(ex-sx)*frac, clientY: sy+(ey-sy)*frac, preventDefault: function(){{}}, target: c, currentTarget: c, nativeEvent: new MouseEvent('mousemove')}});
                }}
                if (c[rk].onMouseUp) c[rk].onMouseUp({{clientX: ex, clientY: ey, preventDefault: function(){{}}, target: c, currentTarget: c, nativeEvent: new MouseEvent('mouseup')}});
                drawn++;
            }}
            return drawn;
        }}""")

        return {"ok": True, "detail": f"drew {num_strokes} strokes (js={drawn}, react={react_strokes})"}
    except Exception as e:
        return {"ok": False, "detail": f"draw error: {e}"}


async def execute_press_keys(page, params: dict) -> dict:
    """Press a sequence of keyboard keys."""
    keys = params.get("keys", [])
    if isinstance(keys, str):
        keys = keys.split()
    if not keys:
        return {"ok": False, "detail": "no keys provided"}

    key_map = {
        "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
        "space": " ", "return": "Enter", "esc": "Escape", "del": "Delete",
        "arrowup": "ArrowUp", "arrowdown": "ArrowDown", "arrowleft": "ArrowLeft", "arrowright": "ArrowRight",
    }

    keyboard = None
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
            if keyboard:
                if "+" in normalized and len(normalized) > 1:
                    parts = normalized.split("+")
                    mod_map = {"ctrl": "Control", "control": "Control", "alt": "Alt", "shift": "Shift", "meta": "Meta"}
                    modifiers = [mod_map.get(p.strip().lower(), p.strip()) for p in parts[:-1]]
                    for mod in modifiers:
                        await keyboard.down(mod)
                    await keyboard.press(parts[-1].strip())
                    for mod in reversed(modifiers):
                        await keyboard.up(mod)
                else:
                    await keyboard.press(normalized)
            else:
                await page.evaluate("""(keySpec) => {
                    var parts = keySpec.split('+');
                    var mainKey = parts[parts.length - 1];
                    var opts = {key: mainKey, code: 'Key' + mainKey.toUpperCase(), bubbles: true, cancelable: true,
                                ctrlKey: keySpec.toLowerCase().indexOf('control') >= 0,
                                altKey: keySpec.toLowerCase().indexOf('alt') >= 0,
                                shiftKey: keySpec.toLowerCase().indexOf('shift') >= 0};
                    document.activeElement.dispatchEvent(new KeyboardEvent('keydown', opts));
                    document.activeElement.dispatchEvent(new KeyboardEvent('keyup', opts));
                }""", normalized)
            pressed.append(key)
            await asyncio.sleep(0.15)
        except Exception as e:
            return {"ok": len(pressed) > 0, "detail": f"pressed {pressed}, error on '{key}': {e}"}

    return {"ok": True, "detail": f"pressed {len(pressed)} keys: {pressed}"}


async def execute_wait(page, params: dict) -> dict:
    """Wait for a specified duration."""
    seconds = params.get("seconds", 2)
    await asyncio.sleep(seconds)
    return {"ok": True, "detail": f"waited {seconds}s"}


async def execute_evaluate_js(page, params: dict) -> dict:
    """Evaluate arbitrary JavaScript on the page. Escape hatch for novel interactions."""
    code = params.get("code", "")
    if not code:
        return {"ok": False, "detail": "no JS code provided"}
    try:
        result = await page.evaluate(f"() => {{ {code} }}")
        result_str = json.dumps(result, default=str)[:500] if result is not None else "undefined"
        return {"ok": True, "detail": f"JS result: {result_str}"}
    except Exception as e:
        return {"ok": False, "detail": f"JS error: {e}"}


async def execute_done(page, params: dict) -> dict:
    """Signal task completion."""
    summary = params.get("summary", "Task complete")
    success = params.get("success", True)
    return {"ok": True, "done": True, "success": success, "detail": summary}


# ══════════════════════════════════════════════════════════════════════════════
# Dispatch table
# ══════════════════════════════════════════════════════════════════════════════

ACTION_HANDLERS = {
    "click": execute_click,
    "type": execute_type,
    "fill_form": execute_fill_form,
    "hover": execute_hover,
    "scroll": execute_scroll,
    "drag": execute_drag,
    "draw": execute_draw,
    "press_keys": execute_press_keys,
    "wait": execute_wait,
    "evaluate_js": execute_evaluate_js,
    "done": execute_done,
}


async def execute(page, action_type: str, params: dict) -> dict:
    """Execute an action by type. Returns {ok, detail, ...}."""
    handler = ACTION_HANDLERS.get(action_type)
    if not handler:
        return {"ok": False, "detail": f"unknown action type: {action_type}"}
    try:
        return await handler(page, params)
    except Exception as e:
        return {"ok": False, "detail": f"{action_type} error: {e}"}
