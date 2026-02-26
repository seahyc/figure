"""Diagnostic: navigate to drag-drop step and inspect actual DOM structure."""
import asyncio
import re
import json
from playwright.async_api import async_playwright

FILL_SUBMIT_JS = r"""(code) => {
    var input = null;
    var allInputs = document.querySelectorAll('input');
    for (var i = 0; i < allInputs.length; i++) {
        var ph = (allInputs[i].placeholder || '').toLowerCase();
        if (ph.indexOf('code') >= 0 || ph.indexOf('character') >= 0) {
            input = allInputs[i]; break;
        }
    }
    if (!input) return {ok: false};
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, code);
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));
    var btns = document.querySelectorAll('button');
    for (var i = 0; i < btns.length; i++) {
        if (/submit\s*code/i.test(btns[i].textContent.trim())) {
            btns[i].scrollIntoView({block: 'center'});
            var r = btns[i].getBoundingClientRect();
            return {ok: true, x: r.x + r.width/2, y: r.y + r.height/2};
        }
    }
    return {ok: false};
}"""

CLICK_BUTTON_JS = r"""(text) => {
    var buttons = document.querySelectorAll('button');
    for (var i = 0; i < buttons.length; i++) {
        var t = buttons[i].textContent.trim();
        if (t.toLowerCase().indexOf(text.toLowerCase()) >= 0) {
            buttons[i].scrollIntoView({block: 'center'});
            var r = buttons[i].getBoundingClientRect();
            return {ok: true, x: r.x + r.width/2, y: r.y + r.height/2, label: t};
        }
    }
    return {ok: false};
}"""

FALSE_POS = {'SUBMIT','BUTTON','HIDDEN','SCROLL','CANVAS','COOKIE','ACCEPT','REJECT',
             'OPTION','SELECT','SEARCH','FILTER','NUMBER','REVEAL','SHADOW','WORKER',
             'RENDER','CHANGE','PARENT','RETURN','ESCAPE','CURSOR','SCREEN','CHROME',
             'PLEASE','VERIFY','ANSWER','RESULT','ABCDEF','DECODE'}

async def find_code(page):
    text = await page.evaluate("() => document.body.innerText")
    codes = re.findall(r'[A-HJ-NP-Z2-9]{6}', text)
    return [c for c in codes if re.search(r'[A-Z]', c) and c not in FALSE_POS]

async def click_btn(page, text):
    info = await page.evaluate(CLICK_BUTTON_JS, text)
    if info and info.get("ok"):
        await page.mouse.click(info["x"], info["y"])
        return True
    return False

async def submit_code(page, code):
    info = await page.evaluate(FILL_SUBMIT_JS, code)
    if info and info.get("ok"):
        await asyncio.sleep(0.3)
        await page.mouse.click(info["x"], info["y"])
        return True
    return False

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await page.goto("https://serene-frangipane-7fd25b.netlify.app/")
        await asyncio.sleep(2)

        # Click START
        await click_btn(page, "START")
        await asyncio.sleep(2)

        submitted = set()
        for attempt in range(100):
            text = await page.evaluate("() => document.body.innerText.substring(0, 3000)")
            m = re.search(r'Step\s+(\d+)', text)
            step = int(m.group(1)) if m else 0
            lower = text.lower()

            # Check for drag-drop challenge
            if "drag" in lower and "drop" in lower:
                print(f"\n=== DRAG-DROP at step {step} ===")

                # COMPREHENSIVE DOM INSPECTION
                info = await page.evaluate("""() => {
                    var result = {};

                    // 1. All draggable elements
                    var drags = document.querySelectorAll('[draggable="true"]');
                    result.draggableCount = drags.length;
                    result.draggables = Array.from(drags).slice(0, 3).map(function(el) {
                        return {
                            tag: el.tagName,
                            cls: el.className ? el.className.substring(0, 100) : '',
                            text: el.textContent.trim().substring(0, 30),
                            attrs: Array.from(el.attributes).map(function(a) { return a.name + '=' + a.value.substring(0, 30); }),
                            w: Math.round(el.getBoundingClientRect().width),
                            h: Math.round(el.getBoundingClientRect().height)
                        };
                    });

                    // 2. Parent/grandparent structure of draggables
                    if (drags.length > 0) {
                        var p = drags[0].parentElement;
                        result.dragParent = {
                            tag: p.tagName,
                            cls: p.className ? p.className.substring(0, 100) : '',
                            childCount: p.children.length,
                            attrs: Array.from(p.attributes).map(function(a) { return a.name + '=' + a.value.substring(0, 30); })
                        };
                        if (p.parentElement) {
                            var gp = p.parentElement;
                            result.grandparent = {
                                tag: gp.tagName,
                                cls: gp.className ? gp.className.substring(0, 100) : '',
                                childCount: gp.children.length,
                                attrs: Array.from(gp.attributes).map(function(a) { return a.name + '=' + a.value.substring(0, 30); })
                            };
                            // List all children of grandparent to find drop zones
                            result.gpChildren = Array.from(gp.children).map(function(c) {
                                return {
                                    tag: c.tagName,
                                    cls: c.className ? c.className.substring(0, 100) : '',
                                    childCount: c.children.length,
                                    text: c.textContent.trim().substring(0, 40),
                                    attrs: Array.from(c.attributes).map(function(a) { return a.name + '=' + a.value.substring(0, 30); }),
                                    w: Math.round(c.getBoundingClientRect().width),
                                    h: Math.round(c.getBoundingClientRect().height)
                                };
                            });
                        }
                    }

                    // 3. All data-* attributes in the page
                    var dataAttrs = new Set();
                    document.querySelectorAll('*').forEach(function(el) {
                        Array.from(el.attributes).forEach(function(a) {
                            if (a.name.indexOf('data-') === 0) {
                                dataAttrs.add(a.name + '=' + a.value.substring(0, 30));
                            }
                        });
                    });
                    result.allDataAttrs = Array.from(dataAttrs);

                    // 4. All elements with drop/target/slot/zone in class
                    result.dropCandidates = [];
                    document.querySelectorAll('*').forEach(function(el) {
                        var c = el.className;
                        if (c && typeof c === 'string' && /drop|target|slot|zone|dest|receive|board/i.test(c)) {
                            result.dropCandidates.push({
                                tag: el.tagName,
                                cls: c.substring(0, 100),
                                childCount: el.children.length,
                                w: Math.round(el.getBoundingClientRect().width),
                                h: Math.round(el.getBoundingClientRect().height)
                            });
                        }
                    });

                    // 5. What [data-slot] and .drop-slot finds
                    result.dataSlotCount = document.querySelectorAll('[data-slot]').length;
                    result.dropSlotClassCount = document.querySelectorAll('.drop-slot').length;
                    result.dataDroppableCount = document.querySelectorAll('[data-droppable]').length;

                    // 6. Event listeners on draggable parent's siblings (looking for drop zones)
                    if (drags.length > 0) {
                        var parent = drags[0].parentElement;
                        if (parent && parent.parentElement) {
                            var siblings = parent.parentElement.children;
                            result.siblingDetails = Array.from(siblings).map(function(sib) {
                                var hasDropEvents = false;
                                // Check for ondrop/ondragover attributes
                                if (sib.ondrop || sib.ondragover) hasDropEvents = true;
                                // Check for React props with drag handlers
                                var rk = Object.keys(sib).find(function(k) { return k.indexOf('__reactProps') === 0; });
                                var reactDragProps = {};
                                if (rk && sib[rk]) {
                                    ['onDrop', 'onDragOver', 'onDragEnter', 'onDragLeave', 'onDragStart'].forEach(function(prop) {
                                        if (sib[rk][prop]) reactDragProps[prop] = true;
                                    });
                                }
                                return {
                                    tag: sib.tagName,
                                    cls: (sib.className || '').substring(0, 100),
                                    childCount: sib.children.length,
                                    hasDropEvents: hasDropEvents,
                                    reactDragProps: reactDragProps,
                                    w: Math.round(sib.getBoundingClientRect().width),
                                    h: Math.round(sib.getBoundingClientRect().height)
                                };
                            });
                        }
                    }

                    return result;
                }""")
                print(json.dumps(info, indent=2))
                break

            # Check for hover challenge
            if "hover" in lower and ("box" in lower or "second" in lower or "color" in lower):
                print(f"\n=== HOVER at step {step} ===")
                info = await page.evaluate("""() => {
                    var result = {};
                    // Check for hover-target data attributes
                    result.hoverTargetById = document.querySelector('#hover-target') ? true : false;
                    result.hoverTargetByData = document.querySelector('[data-hover-target]') ? true : false;
                    result.hoverTargetByDataHover = document.querySelector('[data-hover]') ? true : false;

                    // All elements with hover in their attrs
                    var hoverEls = [];
                    document.querySelectorAll('*').forEach(function(el) {
                        for (var i = 0; i < el.attributes.length; i++) {
                            if (el.attributes[i].name.indexOf('hover') >= 0 || el.attributes[i].value.indexOf('hover') >= 0) {
                                hoverEls.push({
                                    tag: el.tagName,
                                    cls: (el.className || '').substring(0, 80),
                                    attr: el.attributes[i].name + '=' + el.attributes[i].value.substring(0, 40)
                                });
                            }
                        }
                    });
                    result.hoverAttrs = hoverEls;

                    // Colored boxes
                    var colored = [];
                    document.querySelectorAll('div, span, section').forEach(function(el) {
                        var s = getComputedStyle(el);
                        var bg = s.backgroundColor;
                        if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent' && bg !== 'rgb(255, 255, 255)' && bg !== 'rgb(249, 250, 251)' && bg !== 'rgb(243, 244, 246)') {
                            var r = el.getBoundingClientRect();
                            if (r.width >= 30 && r.width <= 400 && r.height >= 30 && r.height <= 400 && r.y > 0 && r.y < 800) {
                                colored.push({
                                    tag: el.tagName,
                                    cls: (el.className || '').substring(0, 80),
                                    bg: bg,
                                    x: Math.round(r.x + r.width/2),
                                    y: Math.round(r.y + r.height/2),
                                    w: Math.round(r.width),
                                    h: Math.round(r.height),
                                    id: el.id || '',
                                    dataAttrs: Array.from(el.attributes).filter(function(a) { return a.name.indexOf('data-') === 0; }).map(function(a) { return a.name + '=' + a.value; })
                                });
                            }
                        }
                    });
                    result.coloredBoxes = colored;

                    return result;
                }""")
                print(json.dumps(info, indent=2))
                break

            # Try to advance
            codes = await find_code(page)
            fresh = [c for c in codes if c not in submitted]
            if fresh:
                ok = await submit_code(page, fresh[0])
                if ok:
                    submitted.add(fresh[0])
                    await asyncio.sleep(1.5)
                    continue

            # Try action buttons
            for btn_text in ['Reveal Code', 'I Remember', 'Start']:
                if await click_btn(page, btn_text):
                    await asyncio.sleep(2)
                    break

            await page.evaluate("window.scrollBy(0, 800)")
            await asyncio.sleep(4)

            if attempt % 5 == 0:
                print(f"Step {step}, attempt {attempt}: {text[:80].strip()}")

        await browser.close()

asyncio.run(main())
