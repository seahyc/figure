"""Debug: what happens when auto-submit claims to click but page doesn't advance?"""
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

        for step in range(30):
            cs = await page.evaluate(r"() => { var m = document.body.innerText.match(/step\s+(\d+)/i); return m ? m[1] : null; }")

            # Run pre_solve
            result = await page.evaluate(f"({script})()")
            auto = "AUTO-SUBMITTED" in str(result)

            if auto:
                # Wait for submit retries
                await asyncio.sleep(1)

                # Check submit result
                submit_log = await page.evaluate("() => { var el = document.getElementById('__submit-log'); return el ? el.textContent : 'no-log'; }")

                # Check if page advanced
                await asyncio.sleep(1)
                new_cs = await page.evaluate(r"() => { var m = document.body.innerText.match(/step\s+(\d+)/i); return m ? m[1] : null; }")

                if new_cs != cs:
                    print(f"  Step {cs} -> {new_cs}: OK ({submit_log})")
                else:
                    print(f"  Step {cs}: STUCK ({submit_log})")

                    # Detailed debug: what's the page state?
                    debug = await page.evaluate("""() => {
                        var inp = document.querySelector('input[placeholder*="code" i]');
                        var btn = null;
                        document.querySelectorAll('button').forEach(b => {
                            if (b.textContent.trim() === 'Submit Code') btn = b;
                        });
                        var rk = btn ? Object.keys(btn).find(k => k.startsWith('__reactProps')) : null;
                        var hasOnClick = rk && btn[rk] && btn[rk].onClick;

                        // Get challenge description (first ~100 chars after "Challenge Step N")
                        var bodyText = document.body.innerText;
                        var csMatch = bodyText.match(/Challenge Step \\d+[\\s\\S]{0,200}/);
                        var challengeDesc = csMatch ? csMatch[0].substring(0, 200) : 'no-match';

                        return JSON.stringify({
                            input_value: inp ? inp.value : 'no-input',
                            input_placeholder: inp ? inp.placeholder : 'none',
                            btn_text: btn ? btn.textContent.trim() : 'no-btn',
                            btn_disabled: btn ? btn.disabled : 'no-btn',
                            btn_visible: btn ? (btn.offsetWidth > 0) : false,
                            btn_has_react_onClick: !!hasOnClick,
                            challenge_desc: challengeDesc,
                            body_has_accepted: bodyText.includes('Code accepted'),
                            body_has_wrong: bodyText.includes('Wrong code')
                        }, null, 2);
                    }""")
                    print(f"    Debug: {debug}")

                    # Try manual Playwright-level submit
                    print("    Trying Playwright-level submit...")
                    code = await page.evaluate(r"() => { var m = document.body.innerText.match(/[A-HJ-NP-Z2-9]{6}/g); return m ? m[0] : null; }")
                    if code:
                        inp = page.locator('input[placeholder*="code" i]').first
                        await inp.fill(code)
                        await asyncio.sleep(0.5)
                        # Click Submit Code button using Playwright
                        btn = page.locator('button:text-is("Submit Code")').first
                        btn_count = await page.locator('button:text-is("Submit Code")').count()
                        print(f"    Found {btn_count} Submit Code buttons, input filled with '{code}'")
                        if btn_count > 0:
                            await btn.click()
                            await asyncio.sleep(2)
                            final_cs = await page.evaluate(r"() => { var m = document.body.innerText.match(/step\s+(\d+)/i); return m ? m[1] : null; }")
                            if final_cs != cs:
                                print(f"    >>> Playwright click WORKED: advanced to step {final_cs}")
                            else:
                                print(f"    >>> Playwright click also FAILED, still on step {cs}")
                                # Take screenshot
                                await page.screenshot(path="/tmp/stuck-step.png")
                                print("    Screenshot saved to /tmp/stuck-step.png")
                    break  # Stop at first stuck step for debugging
            else:
                await asyncio.sleep(1)

        await browser.close()

asyncio.run(main())
