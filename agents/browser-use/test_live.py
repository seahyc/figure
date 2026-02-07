"""Quick test of pre_solve against the live Netlify challenge site."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright


LIVE_URL = "https://serene-frangipane-7fd25b.netlify.app/"


async def main():
    skills_js = Path("skills/pre_solve/script.js").read_text()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        print(f"Navigating to {LIVE_URL}")
        await page.goto(LIVE_URL, timeout=15000)
        await page.wait_for_timeout(2000)

        # Click START if present
        start_btn = await page.query_selector("text=START")
        if start_btn:
            await start_btn.click()
            print("Clicked START")
            await page.wait_for_timeout(3000)

        # Check page structure
        has_input = await page.evaluate("!!document.getElementById('code-input')")
        has_submit = await page.evaluate("!!document.getElementById('submit-code')")
        print(f"Has #code-input: {has_input}, #submit-code: {has_submit}")

        title = await page.evaluate("document.body ? document.body.innerText.substring(0, 200) : ''")
        print(f"Page text: {title[:200]}")

        # Inject pre_solve via file
        inject_path = Path("/tmp/pre_solve_inject.js")
        inject_path.write_text(
            "window.__skills = window.__skills || {};\n"
            "window.__skills.pre_solve = " + skills_js + ";\n"
        )
        await page.add_script_tag(path=str(inject_path))

        check = await page.evaluate("typeof window.__skills.pre_solve")
        print(f"pre_solve type: {check}")

        # Pass 1
        r1 = await page.evaluate("window.__skills.pre_solve()")
        print(f"Pass 1: {r1}")

        # Wait and pass 2
        await asyncio.sleep(3.5)
        r2 = await page.evaluate("window.__skills.pre_solve()")
        print(f"Pass 2: {r2}")

        # Check current state
        body = await page.evaluate("document.body ? document.body.innerText.substring(0, 300) : ''")
        print(f"\nAfter pre_solve:\n{body[:300]}")

        await page.screenshot(path="/tmp/live-test.png")
        print("\nScreenshot saved to /tmp/live-test.png")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
