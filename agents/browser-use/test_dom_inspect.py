"""Quick diagnostic: navigate to challenge, advance to drag/hover step, inspect DOM.
Uses JS clicks to bypass overlays (same as agent)."""
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
        await click_btn(page, "START")
        await asyncio.sleep(2)

        submitted = set()
        for attempt in range(80):
            text = await page.evaluate("() => document.body.innerText.substring(0, 3000)")
            m = re.search(r'Step\s+(\d+)', text)
            step = int(m.group(1)) if m else 0
            lower = text.lower()

            # Check for drag-drop challenge
            if "drag" in lower and "drop" in lower:
                print(f"\n=== DRAG-DROP at step {step} ===")
                info = await page.evaluate("""() => {
                    var result = {};
                    var drags = document.querySelectorAll('[draggable="true"]');
                    result.draggableCount = drags.length;
                    result.draggables = Array.from(drags).slice(0, 3).map(el => ({
                        tag: el.tagName, cls: el.className.substring(0, 60), text: el.textContent.trim().substring(0, 20),
                        w: Math.round(el.getBoundingClientRect().width), h: Math.round(el.getBoundingClientRect().height)
                    }));
                    // Find relevant classes
                    var allClasses = new Set();
                    document.querySelectorAll('*').forEach(el => {
                        if (el.className && typeof el.className === 'string') {
                            el.className.split(/\\s+/).forEach(c => {
                                if (/drop|target|slot|zone|dest|grid|board|cell|place|area|piece|tile|drag|item|source|receive/i.test(c))
                                    allClasses.add(c);
                            });
                        }
                    });
                    result.relevantClasses = Array.from(allClasses);
                    // Draggable parent structure
                    if (drags.length > 0) {
                        var p = drags[0].parentElement;
                        result.dragParent = {tag: p.tagName, cls: p.className.substring(0, 60), childCount: p.children.length};
                        if (p.parentElement) {
                            result.grandparent = {tag: p.parentElement.tagName, cls: p.parentElement.className.substring(0, 60), childCount: p.parentElement.children.length};
                            result.siblings = Array.from(p.parentElement.children).map(c => ({
                                tag: c.tagName, cls: (c.className||'').substring(0, 60),
                                childCount: c.children.length,
                                w: Math.round(c.getBoundingClientRect().width),
                                h: Math.round(c.getBoundingClientRect().height),
                                text: c.textContent.trim().substring(0, 30)
                            }));
                        }
                    }
                    return result;
                }""")
                print(json.dumps(info, indent=2))
                break

            # Check for hover challenge
            if "hover" in lower and ("box" in lower or "second" in lower):
                print(f"\n=== HOVER at step {step} ===")
                info = await page.evaluate("""() => {
                    var colored = [];
                    document.querySelectorAll('div, span, section').forEach(el => {
                        var s = getComputedStyle(el);
                        var bg = s.backgroundColor;
                        if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') {
                            var r = el.getBoundingClientRect();
                            if (r.width >= 20 && r.width <= 500 && r.height >= 20 && r.height <= 500 && r.y > 0 && r.y < 800) {
                                colored.push({tag: el.tagName, cls: (el.className||'').substring(0, 50), bg: bg,
                                    x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                                    w: Math.round(r.width), h: Math.round(r.height),
                                    text: el.textContent.trim().substring(0, 40)});
                            }
                        }
                    });
                    return colored;
                }""")
                print(json.dumps(info, indent=2))
                break

            # Try to advance
            codes = await find_code(page)
            fresh = [c for c in codes if c not in submitted]
            if fresh:
                await submit_code(page, fresh[0])
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
                print(f"Step {step}, attempt {attempt}: {text[:60].strip()}")

        await browser.close()

asyncio.run(main())
