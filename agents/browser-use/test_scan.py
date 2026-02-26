"""Quick test: Navigate to challenge, click START, dump DOM for code scanning debug."""
import asyncio
import json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await page.goto("https://serene-frangipane-7fd25b.netlify.app/")
        await page.wait_for_timeout(3000)

        # Click START
        start = page.get_by_role("button", name="START")
        if await start.count() > 0:
            await start.click()
            print("Clicked START")
            await page.wait_for_timeout(3000)
        else:
            print("No START button found")

        # Dump all data-* attributes
        data_attrs = await page.evaluate("""() => {
            var results = [];
            var allEls = document.querySelectorAll('*');
            for (var i = 0; i < allEls.length; i++) {
                var el = allEls[i];
                var attrs = el.attributes;
                for (var j = 0; j < attrs.length; j++) {
                    if (attrs[j].name.indexOf('data-') === 0 && attrs[j].value.length >= 4) {
                        results.push({tag: el.tagName, attr: attrs[j].name, value: attrs[j].value.substring(0, 50)});
                    }
                }
            }
            return results;
        }""")
        print(f"\nData attributes ({len(data_attrs)}):")
        for a in data_attrs[:30]:
            print(f"  {a['tag']}.{a['attr']} = {a['value']}")

        # Dump aria labels
        aria = await page.evaluate("""() => {
            var results = [];
            var els = document.querySelectorAll('[aria-label],[aria-description]');
            for (var i = 0; i < els.length; i++) {
                results.push({
                    tag: els[i].tagName,
                    label: els[i].getAttribute('aria-label'),
                    desc: els[i].getAttribute('aria-description')
                });
            }
            return results;
        }""")
        print(f"\nAria labels ({len(aria)}):")
        for a in aria[:20]:
            print(f"  {a['tag']}: label={a['label']}, desc={a['desc']}")

        # Dump meta tags
        metas = await page.evaluate("""() => {
            var results = [];
            var els = document.querySelectorAll('meta[content]');
            for (var i = 0; i < els.length; i++) {
                results.push({name: els[i].name || els[i].getAttribute('property'), content: els[i].content.substring(0, 50)});
            }
            return results;
        }""")
        print(f"\nMeta tags ({len(metas)}):")
        for m in metas:
            print(f"  {m['name']}: {m['content']}")

        # Check challenge visible text (first 1000 chars around challenge area)
        text = await page.evaluate("() => document.body ? document.body.innerText.substring(0, 2000) : ''")
        print(f"\nVisible text (first 2000 chars):")
        print(text[:2000])

        # Check for HTML comments
        comments = await page.evaluate("""() => {
            var results = [];
            var walker = document.createTreeWalker(document.body || document, NodeFilter.SHOW_COMMENT);
            var node;
            while (node = walker.nextNode()) {
                if (node.textContent.trim().length > 3) results.push(node.textContent.trim().substring(0, 100));
            }
            return results;
        }""")
        print(f"\nHTML comments ({len(comments)}):")
        for c in comments[:10]:
            print(f"  {c}")

        # Check shadow DOM
        shadow = await page.evaluate("""() => {
            var results = [];
            var allEls = document.querySelectorAll('*');
            for (var i = 0; i < allEls.length; i++) {
                if (allEls[i].shadowRoot) {
                    results.push({
                        tag: allEls[i].tagName,
                        id: allEls[i].id,
                        text: allEls[i].shadowRoot.textContent.substring(0, 200)
                    });
                }
            }
            return results;
        }""")
        print(f"\nShadow DOM ({len(shadow)}):")
        for s in shadow:
            print(f"  {s['tag']}#{s['id']}: {s['text'][:100]}")

        await browser.close()

asyncio.run(main())
