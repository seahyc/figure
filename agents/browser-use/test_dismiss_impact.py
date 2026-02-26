"""Check what dismiss_popups hides on the live site - does it affect challenge content?"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        with open("skills/dismiss_popups/script.js") as f:
            dismiss_js = f.read()

        await page.goto("https://serene-frangipane-7fd25b.netlify.app/", wait_until="networkidle")
        await asyncio.sleep(2)

        # Click START
        await page.evaluate("""() => {
            const btns = document.querySelectorAll('button');
            for (const b of btns) { if (b.textContent.trim().toUpperCase() === 'START') b.click(); }
        }""")
        await asyncio.sleep(3)

        # Check what elements Phase 5 would hide (position:fixed/absolute, z>50)
        report = await page.evaluate("""() => {
            var results = [];
            document.querySelectorAll('div, section, aside').forEach(function(el) {
                var style = getComputedStyle(el);
                var z = parseFloat(style.zIndex) || 0;
                if ((style.position === 'fixed' || style.position === 'absolute') && z > 50
                    && el.offsetWidth > 0 && style.display !== 'none') {
                    // What does this element contain?
                    var text = el.textContent.trim().substring(0, 100);
                    var hasInput = el.querySelector('input') ? true : false;
                    var hasButton = el.querySelector('button') ? true : false;
                    var id = el.id || '';
                    var cls = (el.className || '').toString().substring(0, 80);

                    // Would it be excluded by dismiss_popups?
                    var challengeAttrs = ['data-part', 'data-piece', 'data-tab', 'data-slot',
                        'data-hover-target', 'data-scroll-box', 'data-challenge',
                        'data-step', 'data-code', 'data-type'];
                    var hasChallengeAttr = challengeAttrs.some(a => el.hasAttribute(a));
                    var excluded = false;
                    var idLow = id.toLowerCase();
                    var clsLow = cls.toLowerCase();
                    if (['root', 'app', '__next'].includes(idLow)) excluded = true;
                    if (clsLow.includes('app-container') || clsLow.includes('main-content')) excluded = true;
                    if (hasChallengeAttr) excluded = true;
                    if (el.closest('.challenge-container') || el.closest('.step-content') ||
                        el.closest('[data-challenge]')) excluded = true;

                    results.push({
                        tag: el.tagName,
                        id: id,
                        cls: cls,
                        position: style.position,
                        zIndex: z,
                        width: el.offsetWidth,
                        height: el.offsetHeight,
                        hasInput: hasInput,
                        hasButton: hasButton,
                        textPreview: text,
                        wouldBeHidden: !excluded,
                        hasChallengeAttr: hasChallengeAttr,
                    });
                }
            });
            return JSON.stringify(results, null, 2);
        }""")

        import json
        elements = json.loads(report)
        print(f"Found {len(elements)} fixed/absolute elements with z>50:")
        for i, el in enumerate(elements):
            marker = ">>> HIDDEN <<<" if el['wouldBeHidden'] else "(excluded)"
            print(f"\n  [{i}] {el['tag']}#{el['id']} z={el['zIndex']} pos={el['position']} {el['width']}x{el['height']} {marker}")
            print(f"      cls: {el['cls']}")
            print(f"      text: {el['textPreview'][:80]}")
            print(f"      input={el['hasInput']} btn={el['hasButton']} chalAttr={el['hasChallengeAttr']}")

        # Now run dismiss_popups and check what changed
        print("\n\n=== Running dismiss_popups ===")
        result = await page.evaluate(f"({dismiss_js})()")
        print(f"Result: {result}")

        # Check what got hidden
        hidden_check = await page.evaluate("""() => {
            var hidden = [];
            document.querySelectorAll('div, section, aside').forEach(function(el) {
                if (el.style.display === 'none') {
                    var z = getComputedStyle(el).zIndex;
                    var text = el.textContent.trim().substring(0, 100);
                    hidden.push({tag: el.tagName, id: el.id, cls: (el.className||'').toString().substring(0,80),
                        zIndex: z, text: text, hasInput: !!el.querySelector('input'), hasButton: !!el.querySelector('button')});
                }
            });
            return JSON.stringify(hidden, null, 2);
        }""")
        hidden = json.loads(hidden_check)
        print(f"\nHidden elements after dismiss ({len(hidden)}):")
        for h in hidden:
            print(f"  {h['tag']}#{h['id']} z={h['zIndex']} input={h['hasInput']} btn={h['hasButton']}")
            print(f"    cls: {h['cls']}")
            print(f"    text: {h['text'][:80]}")

        # Check if any hidden element contains puzzle-related content
        for h in hidden:
            if any(kw in h['text'].lower() for kw in ['puzzle', 'solve', 'enter answer', '= ?', 'challenge']):
                print(f"\n  >>> WARNING: Hidden element contains challenge content! <<<")
                print(f"      Text: {h['text']}")

        await browser.close()

asyncio.run(main())
