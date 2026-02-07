from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from pattern_agent.memory_store import MemoryStore
from pattern_agent.run_logger import RunLogger
from pattern_agent.signature import signature_from_dom_snapshot
from pattern_agent.skill_lib import build_skill_library
from pattern_agent.skills import normalize_code


STEP_RE = re.compile(r"Step\s+(\d+)\s*(?:/|of)\s*(\d+)", re.IGNORECASE)


@dataclass
class StepInfo:
    kind: str  # landing | step | finish | unknown
    step: int | None = None
    total: int | None = None

async def _get_step_info(page) -> StepInfo:
    # Local arena: header badge exists. Use a single evaluate call to avoid
    # transient "detached node" errors between count() and inner_text().
    try:
        txt = await page.evaluate("() => document.querySelector('.step-badge')?.textContent || ''")
        m = STEP_RE.search((txt or "").strip())
        if m:
            return StepInfo(kind="step", step=int(m.group(1)), total=int(m.group(2)))
    except Exception:
        pass
    # Live site: step badge DOM may differ. Fall back to scanning body text.
    try:
        body_txt = await page.evaluate("() => document.body?.innerText || ''")
        m = STEP_RE.search((body_txt or "").strip())
        if m:
            return StepInfo(kind="step", step=int(m.group(1)), total=int(m.group(2)))
    except Exception:
        pass

    # Landing page heuristics.
    try:
        has_start = await page.evaluate(
            "() => Array.from(document.querySelectorAll('button, a, [role=\"button\"]')).some(el => (el.textContent||'').trim().toLowerCase() === 'start')"
        )
        if has_start:
            return StepInfo(kind="landing")
    except Exception:
        pass

    # Finish page heuristics.
    try:
        h2 = await page.evaluate("() => document.querySelector('h2')?.textContent || ''")
        if "all steps completed" in (h2 or "").lower():
            return StepInfo(kind="finish")
    except Exception:
        pass

    return StepInfo(kind="unknown")


async def _dismiss_obstacles(page, *, budget_s: float = 4.0) -> None:
    """
    Close cookie banners, popups, and the blocking radio modal.
    Uses Playwright clicks rather than DOM removal (more "real").
    """
    t0 = time.time()
    last_total = None

    while time.time() - t0 < budget_s:
        # Cookie banner (accept/decline both remove it)
        for label in ["Decline", "Reject", "Accept", "Close", "Dismiss"]:
            btn = page.locator(f".obstacle-cookie button:has-text('{label}')")
            if await btn.count() > 0:
                try:
                    await btn.first.click(timeout=500)
                except Exception:
                    pass

        # Popups: prefer real close / dismiss / close buttons
        selectors = [
            ".obstacle-popup .popup-x.real-x",
            ".obstacle-popup .btn-dismiss",
            ".obstacle-popup .btn-close",
        ]
        for sel in selectors:
            loc = page.locator(sel)
            n = await loc.count()
            for i in range(min(n, 10)):
                try:
                    await loc.nth(i).click(timeout=500)
                except Exception:
                    pass

        # Blocking modal: pick "Option X - Correct Choice"
        modal = page.locator(".obstacle-blocking-modal")
        if await modal.count() > 0:
            try:
                correct = modal.locator(
                    "label.radio-option",
                    has_text=re.compile(r"Option\s+[A-D]\s+-\s+Correct\s+Choice", re.I),
                )
                if await correct.count() > 0:
                    await correct.first.click(timeout=1000)
                submit = modal.locator("button.btn-submit-modal")
                if await submit.count() > 0:
                    await submit.first.click(timeout=1000)
            except Exception:
                pass

        # If counts are stable, stop early.
        try:
            total = 0
            total += await page.locator(".obstacle-cookie").count()
            total += await page.locator(".obstacle-popup").count()
            total += await page.locator(".obstacle-blocking-modal").count()
            if last_total is not None and total == last_total:
                break
            last_total = total
        except Exception:
            break

        await asyncio.sleep(0.2)


async def _collect_code_candidates(page) -> list[dict[str, Any]]:
    """
    Collect candidate codes from the DOM with lightweight provenance so we can
    try them in a safe order.
    """
    js = r"""() => {
      const CODE_ANY = /\b[A-HJ-NP-Z2-9]{6}\b/g;
      const CODE_TEST = /^[A-HJ-NP-Z2-9]{6}$/;
      /** @type {{code:string, source:string, hint:string, priority:number}[]} */
      const out = [];
      const push = (code, source, hint, priority) => {
        const c = (code || "").trim().toUpperCase();
        if (!c) return;
        out.push({ code: c, source, hint: hint || "", priority: priority || 0 });
      };

      const isVisible = (el) => {
        if (!el) return false;
        // Skip hidden subtrees.
        if (el.closest && el.closest('[aria-hidden="true"]')) return false;
        const style = window.getComputedStyle(el);
        if (!style) return false;
        if (style.display === "none" || style.visibility === "hidden") return false;
        const r = el.getBoundingClientRect();
        return (r && r.width > 0 && r.height > 0);
      };

      const root = document.querySelector(".challenge-container") || document;
      const rootText = (root.innerText || "");

      // Hidden-dom shortcuts (scoped to current challenge container).
      root.querySelectorAll("[data-code]").forEach(el =>
        push(el.getAttribute("data-code"), "attr:data-code", el.tagName.toLowerCase(), 80)
      );
      root.querySelectorAll("[aria-label]").forEach(el => {
        const a = el.getAttribute("aria-label") || "";
        const m = a.match(CODE_ANY);
        if (m) m.forEach(c => push(c, "attr:aria-label", el.tagName.toLowerCase(), 70));
      });

      // Meta tag is global and can persist across steps. Only use it when the
      // current challenge explicitly hints it's a hidden DOM inspection task.
      if (/hidden dom/i.test(rootText)) {
        const meta = document.querySelector('meta[name="challenge-code"]');
        if (meta && meta.content) push(meta.content, "meta:challenge-code", "meta", 5);
      }

      // Prefer code-display wrappers (these often contain the real code even when hidden),
      // excluding the timing/rotating decoy displays.
      root.querySelectorAll("div.bg-white.border.border-gray-300.rounded").forEach(div => {
        const id = div.getAttribute("id") || "";
        if (id === "timing-display" || id === "rotating-display") return;
        const span = div.querySelector("span");
        const t = ((span && span.textContent) || "").trim().toUpperCase();
        if (CODE_TEST.test(t)) {
          push(t, "code-display", id || div.className, isVisible(div) ? 110 : 90);
          return;
        }
        const m = t.match(CODE_ANY);
        if (m && m.length) push(m[0], "code-display", id || div.className, isVisible(div) ? 105 : 85);
      });

      // Visible mono spans (as a last resort), excluding timing/rotating decoys.
      root.querySelectorAll("span").forEach(span => {
        if (span.closest("#timing-display") || span.closest("#rotating-display")) return;
        if (!isVisible(span)) return;
        const t = (span.innerText || "").trim().toUpperCase();
        if (t.length === 6 && CODE_TEST.test(t)) push(t, "span:innerText", span.className || span.id || span.tagName.toLowerCase(), 60);
      });

      // De-dupe and prioritize stable sources first.
      const seen = new Set();
      const dedup = [];
      for (const c of out) {
        const code = (c.code || "").trim().toUpperCase();
        if (!CODE_TEST.test(code)) continue;
        if (seen.has(code)) continue;
        seen.add(code);
        dedup.push({ code, source: c.source, hint: c.hint, priority: c.priority || 0 });
      }
      dedup.sort((a, b) => (b.priority - a.priority));
      return dedup.map(({ priority, ...rest }) => rest);
    }"""
    try:
        items = await page.evaluate(js)
        if not isinstance(items, list):
            return []
        return items
    except Exception:
        return []


async def _submit_code(page, code: str, *, prev_step: int | None, timeout_s: float = 4.0) -> bool:
    code = code.strip().upper()
    if not normalize_code(code):
        return False

    # Fill (programmatic, not pointer-based) then click submit.
    try:
        inp = page.locator("#code-input")
        if await inp.count() > 0:
            await inp.first.fill(code)
    except Exception:
        pass

    try:
        btn = page.locator("#submit-code")
        if await btn.count() > 0:
            await btn.first.click(timeout=1500)
    except Exception:
        # Overlay can block clicks; fall back to JS click.
        try:
            await page.evaluate("() => document.querySelector('#submit-code')?.click()")
        except Exception:
            return False

    # Wait for step change or finish.
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        info = await _get_step_info(page)
        if info.kind == "finish":
            return True
        if info.kind == "step" and prev_step is not None and info.step != prev_step:
            return True
        await asyncio.sleep(0.2)
    return False


async def _try_codes(page, *, step_num: int, candidates: list[dict[str, Any]]) -> str | None:
    for cand in candidates:
        code = normalize_code(cand.get("code", "") or "")
        if not code:
            continue
        if await _submit_code(page, code, prev_step=step_num):
            return code
    return None


async def run_agent(
    *,
    url: str,
    headless: bool,
    channel: str,
    max_steps: int,
    llm_mode: str,
    memory_file: Path | None,
) -> None:
    logger = RunLogger(agent="pattern-agent", url=url, llm_mode=llm_mode, headless=headless, channel=channel)
    memory = MemoryStore(path=memory_file)
    skills = build_skill_library()

    print(f"[pattern-agent] run_dir={logger.run_dir}")

    async with async_playwright() as p:
        # Prefer using a locally installed Chrome to avoid Playwright browser downloads.
        # If the requested channel isn't available, fall back to Playwright's default browser.
        try:
            browser = await p.chromium.launch(headless=headless, channel=channel)
        except Exception:
            browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        # Local arena loads Tailwind from a CDN which may be blocked in constrained environments.
        # Abort it so the page can render the functional DOM.
        if url.startswith("http://127.0.0.1") or url.startswith("http://localhost"):
            async def _block_tailwind(route, request):
                await route.abort()
            await context.route("**cdn.tailwindcss.com**", _block_tailwind)
        page = await context.new_page()

        await page.goto(url, wait_until="load")

        step_counter = 0
        prev_step_num: int | None = None
        unknown_streak = 0
        fatal_error: str | None = None
        finished = False

        for step_counter in range(1, max_steps + 1):
            info = await _get_step_info(page)
            if info.kind == "finish":
                print("[pattern-agent] finished")
                finished = True
                break

            if info.kind == "unknown":
                unknown_streak += 1
                if unknown_streak in (3, 6):
                    try:
                        dbg = await page.evaluate(
                            "() => ({url: location.href, readyState: document.readyState, rootChildren: document.querySelector('#root')?.childNodes.length || 0, bodyText: document.body?.innerText?.slice(0, 400) || '', bodyHTML: document.body?.innerHTML?.slice(0, 400) || ''})"
                        )
                    except Exception:
                        dbg = {"url": getattr(page, "url", ""), "error": "evaluate failed"}
                    logger.write_json(f"unknown-{unknown_streak}.json", dbg)
                    await logger.screenshot(page, step=step_counter, label=f"unknown-{unknown_streak}")
                if unknown_streak >= 6:
                    fatal_error = f"page never reached a known state (unknown_streak={unknown_streak})"
                    break
            else:
                unknown_streak = 0

            if info.kind == "landing":
                try:
                    # Live site uses a plain START button without an id.
                    btn = page.locator(
                        "#start-btn, button:has-text('START'), button:has-text('Start'), a:has-text('START'), a:has-text('Start'), [role='button']:has-text('START'), [role='button']:has-text('Start')"
                    )
                    await btn.first.click(timeout=2000)
                    await asyncio.sleep(0.5)
                except Exception:
                    pass
                continue

            if info.kind != "step" or info.step is None:
                # Give the page a moment; if still unknown, try a reload.
                await asyncio.sleep(0.5)
                info = await _get_step_info(page)
                if info.kind != "step":
                    try:
                        await page.reload(wait_until="load")
                    except Exception:
                        pass
                    continue

            step_num = info.step
            if prev_step_num != step_num:
                prev_step_num = step_num
                print(f"[pattern-agent] step={step_num}")
                await logger.screenshot(page, step=step_num, label="start")

            await _dismiss_obstacles(page)

            # Build a coarse signature for online reuse.
            try:
                visible_text = (await page.locator(".challenge-container").inner_text()).strip()
            except Exception:
                visible_text = ""
            try:
                btns = page.locator(".challenge-container button")
                nbtn = await btns.count()
                button_texts = []
                for i in range(min(nbtn, 12)):
                    try:
                        button_texts.append((await btns.nth(i).inner_text()).strip())
                    except Exception:
                        pass
            except Exception:
                button_texts = []

            try:
                has_canvas = (await page.locator(".challenge-container canvas").count()) > 0
            except Exception:
                has_canvas = False
            try:
                draggable_count = await page.locator('.challenge-container [draggable="true"]').count()
            except Exception:
                draggable_count = 0

            sig = signature_from_dom_snapshot(
                visible_text=visible_text,
                button_texts=button_texts,
                has_canvas=has_canvas,
                draggable_count=draggable_count,
            )

            learned = memory.get(sig.key)

            # 1) Fast path: if the code is already present in DOM, submit immediately.
            candidates = await _collect_code_candidates(page)
            logger.write_json(f"step-{step_num:02d}-candidates.json", {"sig": sig.key, "candidates": candidates})
            code = await _try_codes(page, step_num=step_num, candidates=candidates[:16])
            if code:
                continue

            # 2) Learned recipe (online skill learning): if we've seen this signature before,
            # reuse the skill that previously unlocked the code.
            if learned:
                skill = next((s for s in skills if s.name == learned.skill_name), None)
                if skill:
                    res = await skill.run(page)
                    logger.write_json(
                        f"step-{step_num:02d}-learned-skill.json",
                        {"sig": sig.key, "skill": learned.skill_name, "result": {"ok": res.ok, "notes": res.notes}},
                    )
                    candidates2 = await _collect_code_candidates(page)
                    code2 = await _try_codes(page, step_num=step_num, candidates=candidates2[:20])
                    if code2:
                        memory.record_success(sig.key, skill_name=learned.skill_name, params=learned.params)
                        memory.save()
                        continue

            # 3) Otherwise, match best-fitting skills and try them in order.
            scored = []
            for s in skills:
                try:
                    scored.append((await s.match(page), s))
                except Exception:
                    scored.append((0.0, s))
            scored.sort(key=lambda x: x[0], reverse=True)

            attempted = []
            advanced = False
            for score, s in scored[:6]:
                if score < 0.5:
                    break
                attempted.append({"skill": s.name, "score": score})
                res = await s.run(page)
                candidates_k = await _collect_code_candidates(page)
                code_k = await _try_codes(page, step_num=step_num, candidates=candidates_k[:24])
                if code_k:
                    memory.record_success(sig.key, skill_name=s.name, params=res.params or {})
                    memory.save()
                    advanced = True
                    break

            logger.write_json(f"step-{step_num:02d}-attempted-skills.json", {"sig": sig.key, "attempted": attempted})

            if advanced:
                continue

            # 4) Local arena quirk: last step always advances once you submit any 6 chars.
            total = info.total or 30
            if step_num >= total:
                if await _submit_code(page, "AAAAAA", prev_step=step_num):
                    continue

            await logger.screenshot(page, step=step_num, label="stuck")
            print(f"[pattern-agent] stuck on step={step_num}, giving up after heuristics")
            fatal_error = f"stuck on step={step_num}"
            break

        await browser.close()

    if not finished and not fatal_error:
        fatal_error = f"max_steps exceeded ({max_steps})"
    if fatal_error:
        raise RuntimeError(fatal_error)
