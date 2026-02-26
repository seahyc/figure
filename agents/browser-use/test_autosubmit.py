"""Quick test: does auto-submit actually work on the live site?"""
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
        await asyncio.sleep(2)

        for step in range(20):
            # Get current challenge step
            cs = await page.evaluate(r"() => { var m = document.body.innerText.match(/step\s+(\d+)/i); return m ? m[1] : null; }")

            # Run pre_solve
            result = await page.evaluate(f"({script})()")
            auto = "AUTO-SUBMITTED" in str(result)
            codes_match = await page.evaluate(r"() => { var m = document.body.innerText.match(/[A-HJ-NP-Z2-9]{6}/g); return m ? m.slice(0,3) : []; }")
            input_val = await page.evaluate("() => { var i = document.querySelector('input[placeholder*=\"code\" i]'); return i ? i.value : 'no-input'; }")
            btn_disabled = await page.evaluate("() => { var bs = document.querySelectorAll('button'); for (var b of bs) { if (b.textContent.trim() === 'Submit Code') return b.disabled; } return 'no-btn'; }")

            label = "AUTO" if auto else str(result)[:80]
            print(f"  Step {cs}: {label} | input='{input_val}' btnDisabled={btn_disabled} codes={codes_match}")

            if auto:
                # Wait for submit retries + page advance
                await asyncio.sleep(2)
                # Check submit log
                submit_log = await page.evaluate("() => { var el = document.getElementById('__submit-log'); return el ? el.textContent : 'no-log'; }")
                print(f"    {submit_log}")
                new_cs = await page.evaluate(r"() => { var m = document.body.innerText.match(/step\s+(\d+)/i); return m ? m[1] : null; }")
                if new_cs != cs:
                    print(f"    >>> ADVANCED to step {new_cs}")
                else:
                    print(f"    >>> STUCK on step {cs} — auto-submit may have failed")
                    body_snippet = await page.evaluate("() => document.body.innerText.substring(0, 300)")
                    print(f"    Body: {body_snippet[:200]}")
            else:
                await asyncio.sleep(1)

        await browser.close()

asyncio.run(main())
