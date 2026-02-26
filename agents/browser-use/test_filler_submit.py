"""Debug: why does auto-submit fail on filler challenge steps?
Runs through steps until finding a filler challenge, then investigates."""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        with open("skills/pre_solve/script.js") as f:
            script = f.read()
        with open("skills/dismiss_popups/script.js") as f:
            dismiss_script = f.read()

        await page.goto("https://serene-frangipane-7fd25b.netlify.app/", wait_until="networkidle")
        await asyncio.sleep(2)

        # Click START
        await page.evaluate("""() => {
            const btns = document.querySelectorAll('button');
            for (const b of btns) { if (b.textContent.trim().toUpperCase() === 'START') b.click(); }
        }""")
        await asyncio.sleep(3)

        # Register skills
        await page.evaluate(f"window.__skills = {{ dismiss_popups: {dismiss_script}, pre_solve: {script} }}")

        for step in range(40):
            cs = await page.evaluate(r"() => { var m = document.body.innerText.match(/step\s+(\d+)/i); return m ? m[1] : null; }")

            # Run dismiss_popups + pre_solve
            await page.evaluate("() => { if (window.__skills.dismiss_popups) window.__skills.dismiss_popups(); }")
            result = await page.evaluate(f"({script})()")
            result_str = str(result) if result else ''

            is_filler = "Filler:" in result_str
            auto = "AUTO-SUBMITTED" in result_str

            if is_filler and auto:
                print(f"\n=== FILLER + AUTO-SUBMIT at Challenge Step {cs} ===")
                print(f"  Pre-solve result: {result_str[:200]}")

                # Wait for submit retries to complete
                await asyncio.sleep(1)

                # Check submit log
                submit_log = await page.evaluate("() => { var el = document.getElementById('__submit-log'); return el ? el.textContent : 'no-log'; }")
                print(f"  Submit log: {submit_log}")

                # Check page state
                await asyncio.sleep(1)
                new_cs = await page.evaluate(r"() => { var m = document.body.innerText.match(/step\s+(\d+)/i); return m ? m[1] : null; }")

                if new_cs != cs:
                    print(f"  >>> ADVANCED to step {new_cs}")
                    continue
                else:
                    print(f"  >>> STUCK on step {cs}")

                    # Detailed diagnostics
                    debug = await page.evaluate("""() => {
                        // Check for blocking overlays
                        var overlays = [];
                        document.querySelectorAll('div').forEach(function(el) {
                            var style = getComputedStyle(el);
                            var z = parseFloat(style.zIndex) || 0;
                            if (style.position === 'fixed' && z > 500 && el.offsetWidth > 0) {
                                overlays.push({
                                    z: z,
                                    w: el.offsetWidth,
                                    h: el.offsetHeight,
                                    display: style.display,
                                    text: el.textContent.trim().substring(0, 100),
                                    buttons: Array.from(el.querySelectorAll('button')).map(b => b.textContent.trim())
                                });
                            }
                        });

                        // Check Submit Code button
                        var btn = null;
                        document.querySelectorAll('button').forEach(function(b) {
                            if (!btn && b.textContent.trim() === 'Submit Code') btn = b;
                        });
                        var btnReactKey = btn ? Object.keys(btn).find(k => k.startsWith('__reactProps')) : null;
                        var btnOnClick = btnReactKey && btn[btnReactKey] && btn[btnReactKey].onClick;

                        // Check input
                        var inp = document.querySelector('input[placeholder*="code" i], input[placeholder*="character" i]');

                        // Check form
                        var form = btn ? btn.closest('form') : null;
                        var formReactKey = form ? Object.keys(form).find(k => k.startsWith('__reactProps')) : null;
                        var formOnSubmit = formReactKey && form[formReactKey] && form[formReactKey].onSubmit;

                        // Count submit code buttons
                        var submitBtnCount = 0;
                        document.querySelectorAll('button').forEach(function(b) {
                            if (b.textContent.trim() === 'Submit Code') submitBtnCount++;
                        });

                        return JSON.stringify({
                            overlays: overlays,
                            btn_found: !!btn,
                            btn_disabled: btn ? btn.disabled : null,
                            btn_visible: btn ? (btn.offsetWidth > 0 && btn.offsetHeight > 0) : false,
                            btn_has_react_onClick: !!btnOnClick,
                            btn_type: btn ? btn.type : null,
                            btn_in_form: !!form,
                            form_has_onSubmit: !!formOnSubmit,
                            submit_btn_count: submitBtnCount,
                            inp_found: !!inp,
                            inp_value: inp ? inp.value : null,
                            inp_placeholder: inp ? inp.placeholder : null,
                            body_has_accepted: document.body.innerText.includes('Code accepted'),
                            body_has_wrong: document.body.innerText.includes('Wrong code'),
                            body_has_filler: document.body.innerText.includes('filler content')
                        }, null, 2);
                    }""")
                    print(f"  Debug: {debug}")

                    # Try manual Playwright submit
                    code = await page.evaluate(r"() => { var m = document.body.innerText.match(/[A-HJ-NP-Z2-9]{6}/g); return m ? m[0] : null; }")
                    if code:
                        print(f"\n  Trying Playwright submit with code '{code}'...")
                        # Scroll to top first
                        await page.evaluate("() => window.scrollTo(0, 0)")
                        await asyncio.sleep(0.5)

                        inp = page.locator('input[placeholder*="code" i]').first
                        inp_count = await page.locator('input[placeholder*="code" i]').count()
                        print(f"  Found {inp_count} code inputs")

                        # Try filling and clicking via Playwright
                        try:
                            await inp.fill(code)
                            await asyncio.sleep(0.5)
                            btn = page.locator('button:text-is("Submit Code")').first
                            btn_count = await page.locator('button:text-is("Submit Code")').count()
                            print(f"  Found {btn_count} Submit Code buttons")
                            if btn_count > 0:
                                try:
                                    await btn.click(timeout=3000)
                                    print("  Playwright click succeeded")
                                except Exception as e:
                                    print(f"  Playwright click FAILED: {e}")
                                    # Try force click
                                    try:
                                        await btn.click(force=True, timeout=3000)
                                        print("  Force click succeeded")
                                    except Exception as e2:
                                        print(f"  Force click FAILED: {e2}")

                                await asyncio.sleep(2)
                                final_cs = await page.evaluate(r"() => { var m = document.body.innerText.match(/step\s+(\d+)/i); return m ? m[1] : null; }")
                                if final_cs != cs:
                                    print(f"  >>> Playwright submit WORKED: advanced to step {final_cs}")
                                else:
                                    print(f"  >>> Playwright submit FAILED, still on step {cs}")
                                    await page.screenshot(path="/tmp/stuck-filler.png")
                                    print("  Screenshot: /tmp/stuck-filler.png")
                        except Exception as e:
                            print(f"  Fill/click error: {e}")

                    break  # Stop at first stuck filler step

            elif auto:
                await asyncio.sleep(1.5)
                new_cs = await page.evaluate(r"() => { var m = document.body.innerText.match(/step\s+(\d+)/i); return m ? m[1] : null; }")
                status = "OK" if new_cs != cs else "STUCK"
                print(f"  Step {cs} -> {new_cs}: {status}")
                if status == "STUCK":
                    # Quick debug for non-filler stuck
                    submit_log = await page.evaluate("() => { var el = document.getElementById('__submit-log'); return el ? el.textContent : 'no-log'; }")
                    overlays = await page.evaluate("""() => {
                        var ols = [];
                        document.querySelectorAll('div').forEach(function(el) {
                            var s = getComputedStyle(el);
                            var z = parseFloat(s.zIndex) || 0;
                            if (s.position === 'fixed' && z > 500 && el.offsetWidth > 0) {
                                ols.push(z + ': ' + el.textContent.trim().substring(0, 60));
                            }
                        });
                        return ols;
                    }""")
                    print(f"    Submit: {submit_log}")
                    print(f"    Overlays: {overlays}")
                    break
            else:
                # Not auto-submitted, wait and continue
                await asyncio.sleep(1)

        await browser.close()

asyncio.run(main())
