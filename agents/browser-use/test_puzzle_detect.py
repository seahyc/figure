"""Debug: check puzzle format and B2 detection on live site."""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        with open("skills/pre_solve/script.js") as f:
            script = f.read()

        await page.goto("https://serene-frangipane-7fd25b.netlify.app/", wait_until="networkidle")
        await asyncio.sleep(2)

        # Click START
        await page.evaluate("""() => {
            const btns = document.querySelectorAll('button');
            for (const b of btns) { if (b.textContent.trim().toUpperCase() === 'START') b.click(); }
        }""")
        await asyncio.sleep(3)

        for step in range(40):
            cs = await page.evaluate(r"() => { var m = document.body.innerText.match(/step\s+(\d+)/i); return m ? m[1] : null; }")

            # Check for puzzle-like content
            puzzle_info = await page.evaluate("""() => {
                var bodyText = document.body.innerText;
                // Check various puzzle formats
                var format1 = bodyText.match(/(\\d+)\\s*\\+\\s*(\\d+)\\s*=\\s*\\?/);
                var format2 = bodyText.match(/(\\d+)\\s*\\+\\s*(\\d+)\\s*=/);
                var format3 = bodyText.match(/what\\s+is\\s+(\\d+)\\s*\\+\\s*(\\d+)/i);
                var format4 = bodyText.match(/puzzle.*?(\\d+\\s*[+\\-*x×]\\s*\\d+)/i);
                var format5 = bodyText.match(/(\\d+)\\s*[+\\-*x×]\\s*(\\d+)\\s*=\\s*\\??/);

                // Check for puzzle inputs and solve button
                var inputs = [];
                document.querySelectorAll('input[type="text"], input[type="number"]').forEach(function(inp) {
                    inputs.push({
                        placeholder: inp.placeholder,
                        value: inp.value,
                        type: inp.type
                    });
                });
                var solveBtn = null;
                document.querySelectorAll('button').forEach(function(btn) {
                    var t = btn.textContent.trim();
                    if (/solve/i.test(t)) solveBtn = t;
                });

                return JSON.stringify({
                    has_puzzle_format1: !!format1,
                    has_puzzle_format2: !!format2,
                    has_puzzle_format3: !!format3,
                    has_puzzle_format4: !!format4,
                    has_puzzle_format5: !!format5,
                    format2_match: format2 ? format2[0] : null,
                    inputs: inputs,
                    solve_button: solveBtn,
                    body_snippet: bodyText.substring(0, 500)
                }, null, 2);
            }""")

            import json
            info = json.loads(puzzle_info) if puzzle_info else {}
            has_puzzle = info.get('has_puzzle_format1') or info.get('has_puzzle_format2')

            if has_puzzle:
                print(f"\n=== PUZZLE at Challenge Step {cs} ===")
                print(f"  Format1 (N+M=?): {info.get('has_puzzle_format1')}")
                print(f"  Format2 (N+M=):  {info.get('has_puzzle_format2')}")
                print(f"  Format3 (what is): {info.get('has_puzzle_format3')}")
                print(f"  Format5 (any op): {info.get('has_puzzle_format5')}")
                print(f"  Format2 match: {info.get('format2_match')}")
                print(f"  Inputs: {info.get('inputs')}")
                print(f"  Solve button: {info.get('solve_button')}")
                # Show puzzle portion of body
                body = info.get('body_snippet', '')
                for line in body.split('\n'):
                    if any(c.isdigit() for c in line) and ('+' in line or '=' in line or 'puzzle' in line.lower()):
                        print(f"  Body line: {line.strip()}")

                # Run pre_solve and check if B2 fires
                result = await page.evaluate(f"({script})()")
                result_str = str(result) if result else ''
                b2_fired = "Puzzle:" in result_str
                print(f"  B2 fired: {b2_fired}")
                print(f"  Pre-solve: {result_str[:200]}")

                if not b2_fired:
                    print(f"\n  >>> B2 DID NOT FIRE! Investigating...")
                    # Check exact body text around the math
                    exact = await page.evaluate("""() => {
                        var text = document.body.innerText;
                        var lines = text.split('\\n');
                        var relevantLines = lines.filter(l =>
                            /\\d+.*[+\\-*].*\\d+/.test(l) || /puzzle|solve|answer/i.test(l)
                        );
                        return relevantLines.join('\\n');
                    }""")
                    print(f"  Relevant lines: {exact}")
                    break
                else:
                    # B2 worked, wait for code to appear
                    await asyncio.sleep(1)
                    result2 = await page.evaluate(f"({script})()")
                    auto = "AUTO-SUBMITTED" in str(result2) if result2 else False
                    print(f"  Second run: {'AUTO-SUBMITTED' if auto else str(result2)[:100]}")
                    if auto:
                        await asyncio.sleep(1.5)
            else:
                # Not a puzzle step, run pre_solve normally
                result = await page.evaluate(f"({script})()")
                auto = "AUTO-SUBMITTED" in str(result) if result else False
                if auto:
                    await asyncio.sleep(1.5)
                else:
                    await asyncio.sleep(0.5)

        await browser.close()

asyncio.run(main())
