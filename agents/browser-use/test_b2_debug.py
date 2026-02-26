"""Debug B2 puzzle solver: check if dismiss_popups hides the puzzle.
Aggressively tries to advance through steps to reach a puzzle challenge."""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        with open("skills/pre_solve/script.js") as f:
            pre_solve_js = f.read()
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

        import json
        last_cs = None
        stuck = 0
        puzzle_seen = False

        for step in range(500):
            cs = await page.evaluate(r"() => { var m = document.body.innerText.match(/step\s+(\d+)/i); return m ? m[1] : null; }")
            if cs == last_cs:
                stuck += 1
            else:
                stuck = 0
                last_cs = cs

            # Check for puzzle BEFORE dismiss_popups
            before = json.loads(await page.evaluate("""() => {
                var bt = document.body.innerText;
                var mathMatch = bt.match(/(\\d+)\\s*\\+\\s*(\\d+)\\s*=\\s*\\?/);
                var calcMatch = bt.match(/(\\d+)\\s*[x×]\\s*(\\d+)\\s*\\+\\s*(\\d+)\\s*=\\s*\\?/);
                var puzzleInput = document.getElementById('puzzle-input') || document.getElementById('calc-input');
                var solveBtn = document.getElementById('puzzle-solve') || document.getElementById('calc-solve');
                return JSON.stringify({
                    hasMath: !!mathMatch, hasCalc: !!calcMatch,
                    mathStr: mathMatch ? mathMatch[0] : null,
                    calcStr: calcMatch ? calcMatch[0] : null,
                    hasPuzzleInput: !!puzzleInput, hasSolveBtn: !!solveBtn,
                    puzzleInputVisible: puzzleInput ? puzzleInput.offsetWidth > 0 : false,
                    solveBtnVisible: solveBtn ? solveBtn.offsetWidth > 0 : false,
                });
            }"""))

            if before['hasMath'] or before['hasCalc']:
                puzzle_seen = True
                print(f"\n=== CS {cs} PUZZLE (before dismiss_popups) ===")
                print(f"  Expr: {before['mathStr'] or before['calcStr']}")
                print(f"  Input: id={before['hasPuzzleInput']} vis={before['puzzleInputVisible']}")
                print(f"  Solve: id={before['hasSolveBtn']} vis={before['solveBtnVisible']}")

                # Run dismiss_popups
                dr = await page.evaluate(f"({dismiss_js})()")

                after = json.loads(await page.evaluate("""() => {
                    var bt = document.body.innerText;
                    var m = bt.match(/(\\d+)\\s*\\+\\s*(\\d+)\\s*=\\s*\\?/);
                    var pi = document.getElementById('puzzle-input') || document.getElementById('calc-input');
                    var sb = document.getElementById('puzzle-solve') || document.getElementById('calc-solve');
                    return JSON.stringify({hasMath: !!m, hasPuzzleInput: !!pi, hasSolveBtn: !!sb,
                        puzzleInputVisible: pi ? pi.offsetWidth > 0 : false,
                        solveBtnVisible: sb ? sb.offsetWidth > 0 : false});
                }"""))
                print(f"  AFTER dismiss ({dr}):")
                print(f"    Math: {after['hasMath']} Input: {after['hasPuzzleInput']}({after['puzzleInputVisible']}) Solve: {after['hasSolveBtn']}({after['solveBtnVisible']})")

                killed = (before['hasMath'] and not after['hasMath'])
                if killed:
                    print(f"  >>> DISMISS_POPUPS KILLED THE PUZZLE EXPRESSION! <<<")

                # Run pre_solve
                result = await page.evaluate(f"({pre_solve_js})()")
                result_str = str(result) if result else ''
                b2 = "Puzzle:" in result_str
                print(f"  B2 fired: {b2} | {result_str[:150]}")

                if not b2:
                    diag = await page.evaluate("""() => {
                        var pi = document.getElementById('puzzle-input');
                        var hiddenParent = null;
                        if (pi) {
                            var el = pi.parentElement;
                            while (el && el !== document.body) {
                                var s = getComputedStyle(el);
                                if (s.display === 'none' || s.visibility === 'hidden') {
                                    hiddenParent = {tag: el.tagName, id: el.id,
                                        cls: el.className.toString().substring(0, 80),
                                        pos: s.position, z: s.zIndex, display: s.display};
                                    break;
                                }
                                el = el.parentElement;
                            }
                        }
                        // Also check what bodyText looks like for B2
                        var bt = document.body.innerText;
                        var hasMath = bt.match(/(\\d+)\\s*\\+\\s*(\\d+)\\s*=\\s*\\?/);
                        return JSON.stringify({hiddenParent, bodyHasMath: !!hasMath,
                            bodyLen: bt.length, bodyFirst200: bt.substring(0, 200)}, null, 2);
                    }""")
                    print(f"  ROOT CAUSE:\n{diag}")
                    # Don't break — try to solve manually and continue to see if puzzle works on later steps
                    # Manually solve the puzzle
                    if before['hasPuzzleInput']:
                        await page.evaluate("""() => {
                            var bt = document.body.innerText;
                            var m = bt.match(/(\\d+)\\s*\\+\\s*(\\d+)\\s*=\\s*\\?/);
                            if (!m) return;
                            var answer = parseInt(m[1]) + parseInt(m[2]);
                            var inp = document.getElementById('puzzle-input') || document.getElementById('calc-input');
                            var btn = document.getElementById('puzzle-solve') || document.getElementById('calc-solve');
                            if (inp && btn) {
                                var s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                                s.call(inp, String(answer));
                                inp.dispatchEvent(new Event('input', {bubbles: true}));
                                btn.click();
                            }
                        }""")
                        print(f"  Manually solved puzzle")
                        await asyncio.sleep(1)
                else:
                    await asyncio.sleep(1)

                # Run pre_solve again for auto-submit
                result2 = await page.evaluate(f"({pre_solve_js})()")
                auto = "AUTO-SUBMITTED" in str(result2) if result2 else False
                if auto:
                    print(f"  -> Auto-submitted after puzzle!")
                    await asyncio.sleep(1.5)
                continue

            # Not a puzzle — try pipeline
            await page.evaluate(f"({dismiss_js})()")
            await page.evaluate(f"({pre_solve_js})()")
            await asyncio.sleep(0.3)
            result = await page.evaluate(f"({pre_solve_js})()")
            result_str = str(result) if result else ''
            auto = "AUTO-SUBMITTED" in result_str

            if auto:
                print(f"  CS {cs}: auto-submitted")
                await asyncio.sleep(1.5)
                stuck = 0
            elif stuck > 8:
                # Force advance by finding any 6-char code and submitting it
                code = await page.evaluate(r"""() => {
                    var codes = [];
                    document.querySelectorAll('*').forEach(el => {
                        if (el.children.length > 0) return;
                        var t = el.textContent.trim();
                        var m = t.match(/\b[A-HJ-NP-Z2-9]{6}\b/);
                        if (m) codes.push(m[0]);
                    });
                    // Also check React fibers
                    var rootEl = document.getElementById('root');
                    if (rootEl) {
                        var key = Object.keys(rootEl).find(k => k.startsWith('__reactFiber') || k.startsWith('__reactContainer'));
                        if (key) {
                            var stack = [rootEl[key]];
                            var visited = 0;
                            while (stack.length > 0 && visited < 500) {
                                var f = stack.pop();
                                if (!f) continue;
                                visited++;
                                if (f.memoizedState) {
                                    var s = f.memoizedState;
                                    var depth = 0;
                                    while (s && depth < 10) {
                                        if (typeof s.memoizedState === 'string' && /^[A-HJ-NP-Z2-9]{6}$/.test(s.memoizedState)) {
                                            codes.push(s.memoizedState);
                                        }
                                        s = s.next;
                                        depth++;
                                    }
                                }
                                if (f.child) stack.push(f.child);
                                if (f.sibling) stack.push(f.sibling);
                            }
                        }
                    }
                    return codes.length > 0 ? codes[0] : null;
                }""")
                if code:
                    submitted = await page.evaluate(f"""() => {{
                        var inp = document.querySelector('input[placeholder*="code" i], input[placeholder*="character" i]');
                        var btn = null;
                        document.querySelectorAll('button').forEach(b => {{ if (b.textContent.trim() === 'Submit Code') btn = b; }});
                        if (!inp || !btn) return false;
                        var s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                        s.call(inp, '{code}');
                        inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                        inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                        setTimeout(() => btn.click(), 100);
                        return true;
                    }}""")
                    print(f"  CS {cs}: force-submitted {code} (result: {submitted})")
                    await asyncio.sleep(2)
                    # Clear submitted codes tracking
                    await page.evaluate("() => { window.__preSolveSubmittedCodes = []; }")
                    stuck = 0
                else:
                    print(f"  CS {cs}: no code found, truly stuck")
                    break
            elif stuck == 0:
                print(f"  CS {cs}: no auto-submit ({result_str[:60]})")

        print(f"\nDone. Puzzle seen: {puzzle_seen}")
        await browser.close()

asyncio.run(main())
