"""Inspect the live site's input/submit mechanism."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    skills_js = Path("skills/pre_solve/script.js").read_text()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        await page.goto("https://serene-frangipane-7fd25b.netlify.app/", timeout=15000)
        await page.wait_for_timeout(2000)

        start_btn = await page.query_selector("text=START")
        if start_btn:
            await start_btn.click()
            await page.wait_for_timeout(3000)

        # Find all inputs and buttons
        info = await page.evaluate("""() => {
            const inputs = [];
            document.querySelectorAll('input, textarea').forEach(el => {
                inputs.push({
                    tag: el.tagName, type: el.type, id: el.id, name: el.name,
                    placeholder: el.placeholder, className: el.className.substring(0, 80)
                });
            });
            const buttons = [];
            document.querySelectorAll('button, [role="button"], a.btn, [type="submit"]').forEach(el => {
                buttons.push({
                    tag: el.tagName, text: el.textContent.trim().substring(0, 40),
                    id: el.id, className: el.className.substring(0, 80),
                    type: el.type || ''
                });
            });
            return { inputs, buttons, url: window.location.href };
        }""")

        print("Inputs:", info['inputs'])
        print("\nButtons:", info['buttons'])
        print("\nURL:", info['url'])

        # Dump full HTML of the submit area
        html = await page.evaluate("""() => {
            // Look for anything that looks like a code entry area
            const candidates = [
                ...document.querySelectorAll('[class*="code"]'),
                ...document.querySelectorAll('[class*="submit"]'),
                ...document.querySelectorAll('[class*="input"]'),
                ...document.querySelectorAll('form'),
            ];
            return candidates.map(el => ({
                tag: el.tagName,
                id: el.id,
                className: el.className.substring(0, 80),
                innerHTML: el.innerHTML.substring(0, 200)
            }));
        }""")
        print("\nCode/submit area candidates:")
        for h in html:
            print(f"  {h['tag']}#{h['id']} .{h['className'][:40]}: {h['innerHTML'][:120]}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
