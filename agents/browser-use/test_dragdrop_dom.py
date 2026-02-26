"""
Test: Verify Source 10 with step-matching and alternate fiber works for drag-drop.
Simulates the exact scenario: auto-solve steps, then check drag-drop.
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

        for attempt in range(1, 30):
            await page.goto(URL, wait_until="networkidle")
            await asyncio.sleep(2)
            await page.evaluate("""() => {
                const btns = document.querySelectorAll('button');
                for (const b of btns) { if (b.textContent.trim().toUpperCase() === 'START') { b.click(); return; } }
            }""")
            await asyncio.sleep(2)

            last_step = None; stuck = 0; found_dd = False
            for i in range(40):
                body_text = await page.evaluate("() => document.body.innerText")
                if 'Drag-and-Drop' in body_text:
                    # Verify it's the CURRENT challenge, not leftover text
                    step_text = await page.evaluate("() => { var m = document.body.innerText.match(/step\\s+(\\d+)/i); return m ? m[1] : null; }")
                    title = await page.evaluate("""() => {
                        var h = document.querySelector('h1, h2, h3, [class*="text-2xl"], [class*="text-xl"]');
                        return h ? h.textContent.trim() : '';
                    }""")
                    if 'Drag' in title or 'drag' in title:
                        found_dd = True
                        print(f"  Step {step_text}: DRAG-DROP confirmed in title: {title}")
                        break
                step = await page.evaluate("() => { var m = document.body.innerText.match(/step\\s+(\\d+)/i); return m ? m[1] : null; }")
                if step == last_step: stuck += 1
                else: stuck = 0
                if stuck > 3: break
                last_step = step
                result = await page.evaluate(f"({script})()")
                auto = 'AUTO-SUBMITTED' in str(result)
                print(f"  Step {step}: {'AUTO' if auto else 'manual'} - {str(result)[:80]}")
                await asyncio.sleep(4)

            if not found_dd:
                print(f"Attempt {attempt}: drag-drop not found, stuck at step {last_step}")
                continue

            print(f"\n*** Attempt {attempt}: DRAG-DROP challenge active ***")

            # Check submitted codes so far
            submitted = await page.evaluate("() => window.__preSolveSubmittedCodes || []")
            print(f"Previously submitted codes: {submitted}")

            # Run pre_solve on drag-drop page
            dd_result = await page.evaluate(f"({script})()")
            print(f"Pre_solve result: {dd_result}")

            # Check what happened
            body = await page.evaluate("() => document.body.innerText.substring(0, 400)")
            auto_submitted = 'AUTO-SUBMITTED' in str(dd_result)
            code_accepted = 'Code accepted' in body
            print(f"Auto-submitted: {auto_submitted}, Code accepted: {code_accepted}")

            if not auto_submitted:
                # Try again after a delay (simulate Phase 2)
                print("Waiting 5s for React state to update...")
                await asyncio.sleep(5)
                dd_result2 = await page.evaluate(f"({script})()")
                print(f"Pre_solve result (retry): {dd_result2}")
                auto2 = 'AUTO-SUBMITTED' in str(dd_result2)
                print(f"Auto-submitted on retry: {auto2}")

            submitted_after = await page.evaluate("() => window.__preSolveSubmittedCodes || []")
            print(f"Submitted codes after: {submitted_after}")

            break

        await browser.close()

asyncio.run(main())
