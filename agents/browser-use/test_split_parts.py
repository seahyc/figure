"""
Test: Inspect split_parts DOM structure on live site.
Auto-solves steps until split_parts appears, then inspects DOM elements.
"""
import asyncio
from playwright.async_api import async_playwright

URL = "https://serene-frangipane-7fd25b.netlify.app/"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        with open("skills/pre_solve/script.js") as f:
            script = f.read()

        for attempt in range(1, 10):
            await page.goto(URL, wait_until="networkidle")
            await asyncio.sleep(2)
            await page.evaluate("""() => {
                const btns = document.querySelectorAll('button');
                for (const b of btns) { if (b.textContent.trim().toUpperCase() === 'START') { b.click(); return; } }
            }""")
            await asyncio.sleep(2)

            last_step = None; stuck = 0; found_sp = False
            for i in range(40):
                body_text = await page.evaluate("() => document.body.innerText")
                if 'Split Parts' in body_text or 'split_parts' in body_text or 'scattered' in body_text:
                    title = await page.evaluate("""() => {
                        var els = document.querySelectorAll('p, strong, h1, h2, h3, div');
                        for (var e of els) {
                            if (e.textContent.includes('Split Parts') || e.textContent.includes('scattered')) {
                                return e.textContent.trim().substring(0, 100);
                            }
                        }
                        return '';
                    }""")
                    if 'Split' in title or 'scatter' in title:
                        found_sp = True
                        step = await page.evaluate("() => { var m = document.body.innerText.match(/step\\s+(\\d+)/i); return m ? m[1] : null; }")
                        print(f"  Step {step}: SPLIT_PARTS confirmed: {title}")
                        break

                step = await page.evaluate("() => { var m = document.body.innerText.match(/step\\s+(\\d+)/i); return m ? m[1] : null; }")
                if step == last_step: stuck += 1
                else: stuck = 0
                if stuck > 5: break
                last_step = step
                result = await page.evaluate(f"({script})()")
                auto = 'AUTO-SUBMITTED' in str(result)
                print(f"  Step {step}: {'AUTO' if auto else 'manual'} - {str(result)[:100]}")
                await asyncio.sleep(4)

            if not found_sp:
                print(f"Attempt {attempt}: split_parts not found, stuck at step {last_step}")
                continue

            print(f"\n*** Attempt {attempt}: SPLIT_PARTS challenge active ***")

            # INSPECT DOM
            print("\n=== DOM INSPECTION ===")

            # 1. Try data-part attribute
            data_parts = await page.evaluate("""() => {
                var els = document.querySelectorAll('[data-part]');
                return Array.from(els).map(e => ({
                    tag: e.tagName,
                    classes: e.className,
                    dataPart: e.getAttribute('data-part'),
                    text: e.textContent.trim().substring(0, 50),
                    style: e.getAttribute('style') || '',
                    rect: {x: e.getBoundingClientRect().x, y: e.getBoundingClientRect().y,
                           w: e.getBoundingClientRect().width, h: e.getBoundingClientRect().height}
                }));
            }""")
            print(f"[data-part] elements: {len(data_parts)}")
            for dp in data_parts:
                print(f"  {dp}")

            # 2. Try text-based search for "Part N:"
            part_text_els = await page.evaluate("""() => {
                var results = [];
                document.querySelectorAll('*').forEach(el => {
                    var text = el.textContent.trim();
                    if (/^Part \\d+:/.test(text) && text.length < 30) {
                        var s = window.getComputedStyle(el);
                        results.push({
                            tag: el.tagName,
                            classes: el.className,
                            text: text,
                            style: el.getAttribute('style') || '',
                            position: s.position,
                            bg: s.backgroundColor,
                            cursor: s.cursor,
                            rect: {x: el.getBoundingClientRect().x, y: el.getBoundingClientRect().y,
                                   w: el.getBoundingClientRect().width, h: el.getBoundingClientRect().height}
                        });
                    }
                });
                return results;
            }""")
            print(f"\nElements with 'Part N:' text: {len(part_text_els)}")
            for pt in part_text_els:
                print(f"  {pt}")

            # 3. Find all absolutely positioned elements outside normal flow
            abs_els = await page.evaluate("""() => {
                var results = [];
                document.querySelectorAll('*').forEach(el => {
                    var s = window.getComputedStyle(el);
                    if (s.position === 'absolute' && s.cursor === 'pointer') {
                        var text = el.textContent.trim();
                        if (text.length < 50) {
                            results.push({
                                tag: el.tagName,
                                classes: el.className.substring(0, 80),
                                text: text,
                                bg: s.backgroundColor,
                                zIndex: s.zIndex,
                                top: s.top,
                                left: s.left,
                                rect: {x: el.getBoundingClientRect().x, y: el.getBoundingClientRect().y,
                                       w: el.getBoundingClientRect().width, h: el.getBoundingClientRect().height}
                            });
                        }
                    }
                });
                return results;
            }""")
            print(f"\nAbsolutely positioned cursor-pointer elements: {len(abs_els)}")
            for ae in abs_els:
                print(f"  {ae}")

            # 4. Check yellow background elements
            yellow_els = await page.evaluate("""() => {
                var results = [];
                document.querySelectorAll('*').forEach(el => {
                    var s = window.getComputedStyle(el);
                    var bg = s.backgroundColor;
                    // yellow-400 is rgb(250, 204, 21) or similar
                    if (bg && (bg.includes('250') || bg.includes('234') || bg.includes('253')) &&
                        bg.includes('204') || bg.includes('179') || bg.includes('224')) {
                        var text = el.textContent.trim();
                        if (text.length < 50) {
                            results.push({
                                tag: el.tagName,
                                classes: el.className.substring(0, 80),
                                text: text,
                                bg: bg,
                                position: s.position,
                                cursor: s.cursor
                            });
                        }
                    }
                });
                return results;
            }""")
            print(f"\nYellow background elements: {len(yellow_els)}")
            for ye in yellow_els[:10]:
                print(f"  {ye}")

            # 5. Try bg-yellow classes
            bg_yellow = await page.evaluate("""() => {
                var results = [];
                document.querySelectorAll('[class*="yellow"], [class*="bg-yellow"]').forEach(el => {
                    results.push({
                        tag: el.tagName,
                        classes: el.className.substring(0, 100),
                        text: el.textContent.trim().substring(0, 50)
                    });
                });
                return results;
            }""")
            print(f"\nElements with 'yellow' in class: {len(bg_yellow)}")
            for by in bg_yellow:
                print(f"  {by}")

            # 6. Check React fiber for current challenge config
            fiber_info = await page.evaluate("""() => {
                var challengeArea = document.querySelector('.max-w-6xl') || document.querySelector('main');
                if (!challengeArea) return 'no challenge area';
                var startEl = challengeArea.querySelector('[class*="bg-"]') ||
                              challengeArea.querySelector('strong');
                if (!startEl) return 'no start element';
                var fiberKey = Object.keys(startEl).find(k => k.indexOf('__reactFiber') === 0);
                if (!fiberKey) return 'no fiber key';
                var fiber = startEl[fiberKey];
                for (var i = 0; i < 30 && fiber; i++) {
                    var props = fiber.memoizedProps;
                    if (props && props.config && typeof props.stepNum === 'number') {
                        // Also extract memoizedState chain
                        var states = [];
                        var state = fiber.memoizedState;
                        var depth = 0;
                        while (state && depth < 10) {
                            if (typeof state.memoizedState === 'string') {
                                states.push(state.memoizedState);
                            }
                            state = state.next;
                            depth++;
                        }
                        return {
                            stepNum: props.stepNum,
                            config: props.config,
                            states: states,
                            fiberType: fiber.type ? (fiber.type.name || String(fiber.type).substring(0, 30)) : null
                        };
                    }
                    fiber = fiber.return;
                }
                return 'no Gv component found';
            }""")
            print(f"\nReact fiber info: {fiber_info}")

            # 7. Test clicking data-part elements if found
            if data_parts:
                print("\n=== CLICKING data-part ELEMENTS ===")
                click_result = await page.evaluate("""() => {
                    var parts = document.querySelectorAll('[data-part]');
                    var clicked = 0;
                    parts.forEach(p => { p.click(); clicked++; });
                    return clicked;
                }""")
                print(f"Clicked {click_result} data-part elements")
                await asyncio.sleep(1)
                progress = await page.evaluate("""() => {
                    var el = document.getElementById('parts-progress');
                    return el ? el.textContent : 'no progress element';
                }""")
                print(f"Progress: {progress}")

            # 8. Try clicking elements with "Part N:" text
            if part_text_els:
                print("\n=== CLICKING 'Part N:' TEXT ELEMENTS ===")
                click_result = await page.evaluate("""() => {
                    var clicked = 0;
                    document.querySelectorAll('*').forEach(el => {
                        var text = el.textContent.trim();
                        if (/^Part \\d+:/.test(text) && text.length < 30 && el.children.length === 0) {
                            el.click();
                            clicked++;
                        }
                    });
                    return clicked;
                }""")
                print(f"Clicked {click_result} 'Part N:' elements")
                await asyncio.sleep(1)
                body = await page.evaluate("() => document.body.innerText.substring(0, 300)")
                found_text = 'found' in body.lower()
                print(f"Progress changed: {body[:100]}")

            break

        await browser.close()

asyncio.run(main())
