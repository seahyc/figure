"""Test drag-drop with separate evaluate calls to debug why only 1/6 slots fill."""
import asyncio
import json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        with open("skills/pre_solve/script.js") as f:
            script = f.read()

        for session in range(10):
            await page.goto("https://serene-frangipane-7fd25b.netlify.app/", wait_until="networkidle")
            await asyncio.sleep(2)
            await page.evaluate("""() => {
                const btns = document.querySelectorAll('button');
                for (const b of btns) { if (b.textContent.trim().toUpperCase() === 'START') b.click(); }
            }""")
            await asyncio.sleep(2)

            last_step = None
            stuck = 0
            found_dd = False
            for i in range(25):
                step = await page.evaluate(r"() => { var m = document.body.innerText.match(/step\s+(\d+)/i); return m ? m[1] : null; }")
                body = await page.evaluate("() => document.body.innerText.substring(0, 500)")
                is_dd = "drag-and-drop" in body.lower() or "drag and drop" in body.lower()

                if is_dd:
                    piece_count = await page.evaluate('() => document.querySelectorAll("[draggable=true]").length')
                    if piece_count > 0:
                        print(f"Session {session+1}, step {step}: DRAG-DROP with {piece_count} pieces")
                        found_dd = True
                        break

                if step == last_step:
                    stuck += 1
                    if stuck > 5:
                        break
                else:
                    stuck = 0
                last_step = step
                result = await page.evaluate(f"({script})()")
                res_str = str(result)
                auto = "AUTO-SUBMITTED" in res_str
                label = "AUTO" if auto else res_str[:60]
                print(f"  S{session+1} Step {step}: {label}")
                await asyncio.sleep(4 if auto else 1)

            if not found_dd:
                print(f"Session {session+1}: no drag-drop found")
                continue

            print("\n=== TESTING ONE DROP AT A TIME ===")
            for drop_idx in range(6):
                state = await page.evaluate(r"""() => {
                    var pieces = document.querySelectorAll('[draggable="true"]');
                    var emptySlots = [];
                    document.querySelectorAll('div').forEach(function(el) {
                        var s = window.getComputedStyle(el);
                        if (s.borderStyle === 'dashed' && el.offsetWidth >= 40 && el.offsetHeight >= 40 &&
                            el.offsetWidth <= 120 && el.offsetHeight <= 120 && !el.querySelector('[draggable="true"]')) {
                            emptySlots.push(el.textContent.trim());
                        }
                    });
                    var filled = document.body.innerText.match(/(\d+)\/(\d+)\s*filled/i);
                    return {pieces: pieces.length, emptySlots: emptySlots.length, filledText: filled ? filled[0] : 'N/A'};
                }""")
                print(f"  [{drop_idx}] pieces={state['pieces']} empty={state['emptySlots']} {state['filledText']}")

                if state["emptySlots"] == 0:
                    print("  All slots filled!")
                    break

                # DragStart - pick first available piece
                ds = await page.evaluate(r"""() => {
                    var pieces = document.querySelectorAll('[draggable="true"]');
                    if (pieces.length === 0) return 'no pieces';
                    var piece = pieces[0];
                    var pk = Object.keys(piece).find(function(k) { return k.indexOf('__reactProps') === 0; });
                    if (!pk) return 'no reactProps on piece';
                    if (!piece[pk].onDragStart) return 'no onDragStart, props=' + Object.keys(piece[pk]).join(',');
                    piece[pk].onDragStart({
                        dataTransfer: { effectAllowed: 'none', setData: function(){}, setDragImage: function(){} },
                        preventDefault: function(){}
                    });
                    return 'dragStart OK on "' + piece.textContent.trim().substring(0, 10) + '"';
                }""")
                print(f"    dragStart: {ds}")

                await asyncio.sleep(0.15)

                # Drop - pick first empty slot
                dp = await page.evaluate(r"""() => {
                    var slots = [];
                    document.querySelectorAll('div').forEach(function(el) {
                        var s = window.getComputedStyle(el);
                        if (s.borderStyle === 'dashed' && el.offsetWidth >= 40 && el.offsetHeight >= 40 &&
                            el.offsetWidth <= 120 && el.offsetHeight <= 120 && !el.querySelector('[draggable="true"]')) {
                            slots.push(el);
                        }
                    });
                    if (slots.length === 0) return 'no empty slots left (drop success?)';
                    var slot = slots[0];
                    var sk = Object.keys(slot).find(function(k) { return k.indexOf('__reactProps') === 0; });
                    if (!sk) return 'no reactProps on slot';
                    var events = Object.keys(slot[sk]).filter(function(k){ return k.indexOf('on') === 0; });
                    if (!slot[sk].onDrop) return 'no onDrop, has: ' + events.join(',');
                    try {
                        slot[sk].onDrop({
                            preventDefault: function(){},
                            dataTransfer: { dropEffect: 'none', getData: function() { return ''; } }
                        });
                        return 'drop OK';
                    } catch(e) {
                        return 'drop error: ' + e.message;
                    }
                }""")
                print(f"    drop: {dp}")

                await asyncio.sleep(0.3)

            # Final state
            final = await page.evaluate(r"""() => {
                var b = document.body.innerText;
                var completed = /challenge completed/i.test(b);
                var filled = b.match(/(\d+)\/(\d+)\s*filled/i);
                var code_idx = b.indexOf('code is');
                return {
                    completed: completed,
                    filled: filled ? filled[0] : 'N/A',
                    codeSnippet: code_idx >= 0 ? b.substring(code_idx, code_idx + 40) : 'no code'
                };
            }""")
            print(f"\nFinal: {json.dumps(final)}")
            break

        await browser.close()

asyncio.run(main())
