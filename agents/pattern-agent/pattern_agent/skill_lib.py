from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pattern_agent.skills import SkillResult


MatchFn = Callable[[Any], Awaitable[float]]
RunFn = Callable[[Any], Awaitable[SkillResult]]


@dataclass(frozen=True)
class SkillDef:
    name: str
    match: MatchFn
    run: RunFn


def build_skill_library() -> list[SkillDef]:
    """
    Skills are intentionally parameter-light and signature-based.
    They are written to be reusable on "similar but different" pages by:
    - matching on text + structure (presence of canvas/draggables/inputs),
    - executing a minimal deterministic interaction,
    - relying on outer-loop verification (code extracted + step advanced).
    """

    async def _has(page, selector: str) -> bool:
        try:
            return (await page.locator(selector).count()) > 0
        except Exception:
            return False

    async def _challenge_text(page) -> str:
        try:
            return (await page.locator(".challenge-container").inner_text()).strip()
        except Exception:
            return ""

    async def _click_all(page, selector: str, *, max_clicks: int = 12) -> int:
        loc = page.locator(selector)
        n = await loc.count()
        clicked = 0
        for i in range(min(n, max_clicks)):
            try:
                await loc.nth(i).click(timeout=1200)
                clicked += 1
                await asyncio.sleep(0.15)
            except Exception:
                pass
        return clicked

    async def _click_n(page, selector: str, n: int) -> int:
        loc = page.locator(selector)
        if await loc.count() <= 0:
            return 0
        clicked = 0
        for _ in range(n):
            try:
                await loc.first.click(timeout=1200)
                clicked += 1
                await asyncio.sleep(0.15)
            except Exception:
                break
        return clicked

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    async def match_click_reveal(page) -> float:
        return 0.95 if await _has(page, ".challenge-container button:has-text('Reveal Code')") else 0.0

    async def run_click_reveal(page) -> SkillResult:
        n = await _click_all(page, ".challenge-container button:has-text('Reveal Code')", max_clicks=3)
        return SkillResult(ok=n > 0, notes=f"clicked={n}", params={})

    async def match_scroll_reveal(page) -> float:
        txt = await _challenge_text(page)
        if re.search(r"\bscroll\b", txt, flags=re.I):
            return 0.8
        return 0.0

    async def run_scroll_reveal(page) -> SkillResult:
        try:
            await page.mouse.wheel(0, 900)
        except Exception:
            await page.evaluate("() => window.scrollBy(0, 900)")
        await asyncio.sleep(0.3)
        return SkillResult(ok=True, notes="scrolled", params={})

    async def match_delayed_reveal(page) -> float:
        txt = await _challenge_text(page)
        return 0.7 if re.search(r"\bwait\b.*\bseconds\b", txt, flags=re.I) else 0.0

    async def run_delayed_reveal(page) -> SkillResult:
        txt = await _challenge_text(page)
        # Local arena uses 2-6 seconds. Parse if present; otherwise cap.
        sec = 6.0
        m = re.search(r"wait(?:ing)?\s+([\d.]+)\s+seconds", txt, flags=re.I)
        if m:
            try:
                sec = float(m.group(1))
            except ValueError:
                sec = 6.0
        await asyncio.sleep(min(max(sec + 0.2, 1.5), 7.0))
        return SkillResult(ok=True, notes=f"waited={sec}", params={})

    async def match_drag_drop(page) -> float:
        if await _has(page, '.challenge-container [draggable="true"][data-piece]') and await _has(page, ".challenge-container [data-slot]"):
            return 0.9
        return 0.0

    async def run_drag_drop(page) -> SkillResult:
        js = r"""() => {
          const pieces = Array.from(document.querySelectorAll('.challenge-container [draggable="true"][data-piece]'));
          const slots = Array.from(document.querySelectorAll('.challenge-container [data-slot]'));
          if (pieces.length === 0 || slots.length === 0) return {ok:false, why:"missing"};
          const dt = new DataTransfer();
          const dispatch = (el, type) => el.dispatchEvent(new DragEvent(type, {dataTransfer: dt, bubbles:true, cancelable:true}));
          const moves = Math.min(6, pieces.length, slots.length);
          for (let i = 0; i < moves; i++) {
            dt.clearData();
            dt.setData("text/plain", pieces[i].getAttribute("data-piece") || "");
            dispatch(pieces[i], "dragstart");
            dispatch(slots[i], "dragover");
            dispatch(slots[i], "drop");
            dispatch(pieces[i], "dragend");
          }
          return {ok:true, moved:moves};
        }"""
        try:
            res = await page.evaluate(js)
        except Exception as e:
            return SkillResult(ok=False, notes=str(e), params={})
        await asyncio.sleep(0.3)
        return SkillResult(ok=bool(res and res.get("ok")), notes=str(res), params={})

    async def match_keyboard_sequence(page) -> float:
        txt = await _challenge_text(page)
        if "\u2192" in txt and re.search(r"(control\+|arrowup|arrowdown|shift\+tab|\btab\b|\benter\b)", txt, flags=re.I):
            return 0.9
        return 0.0

    async def run_keyboard_sequence(page) -> SkillResult:
        txt = await _challenge_text(page)
        # Find the first line containing arrows and plausible keys.
        line = ""
        for candidate in txt.splitlines():
            if "\u2192" not in candidate:
                continue
            if not re.search(r"(control\+|arrowup|arrowdown|shift\+tab|\btab\b|\benter\b)", candidate, flags=re.I):
                continue
            line = candidate
            break
        if not line:
            return SkillResult(ok=False, notes="no sequence line", params={})

        line = re.sub(r"^.*?(required sequence:|press the following key sequence:)\s*", "", line, flags=re.I).strip()
        parts = [p.strip() for p in line.split("\u2192")]
        sent = []
        for p in parts:
            if not p:
                continue
            try:
                await page.keyboard.press(p)
                sent.append(p)
            except Exception:
                # Try common normalizations.
                pp = p.replace("CTRL", "Control").replace("Cmd", "Meta")
                try:
                    await page.keyboard.press(pp)
                    sent.append(pp)
                except Exception:
                    pass
        await asyncio.sleep(0.3)
        return SkillResult(ok=len(sent) > 0, notes=f"sent={sent}", params={})

    async def match_memory(page) -> float:
        if await _has(page, ".challenge-container #remember-btn") or await _has(page, ".challenge-container button:has-text('I Remember')"):
            return 0.85
        return 0.0

    async def run_memory(page) -> SkillResult:
        btn = page.locator(".challenge-container #remember-btn, .challenge-container button:has-text('I Remember')")
        try:
            await btn.first.wait_for(state="visible", timeout=4000)
        except Exception:
            # Button might already be visible or might be hidden but still clickable.
            pass
        try:
            await btn.first.click(timeout=1500)
            await asyncio.sleep(0.3)
            return SkillResult(ok=True, notes="clicked remember", params={})
        except Exception as e:
            return SkillResult(ok=False, notes=str(e), params={})

    async def match_hover(page) -> float:
        if await _has(page, ".challenge-container [data-hover-target]"):
            return 0.9
        txt = await _challenge_text(page)
        return 0.6 if re.search(r"\bhover\b", txt, flags=re.I) else 0.0

    async def run_hover(page) -> SkillResult:
        target = page.locator(".challenge-container [data-hover-target], .challenge-container :has-text('Hover here')")
        if await target.count() <= 0:
            return SkillResult(ok=False, notes="no hover target", params={})
        try:
            await target.first.hover(timeout=1500)
            await asyncio.sleep(1.2)
            return SkillResult(ok=True, notes="hovered", params={})
        except Exception as e:
            return SkillResult(ok=False, notes=str(e), params={})

    async def match_capture(page) -> float:
        txt = await _challenge_text(page)
        if await _has(page, ".challenge-container button:has-text('Capture')") and re.search(r"Captures:\s*\d+/3", txt, flags=re.I):
            return 0.85
        return 0.0

    async def run_capture(page) -> SkillResult:
        clicked = await _click_n(page, ".challenge-container button:has-text('Capture')", 3)
        await asyncio.sleep(0.2)
        return SkillResult(ok=clicked >= 3, notes=f"clicked={clicked}", params={})

    async def match_canvas(page) -> float:
        if await _has(page, ".challenge-container canvas") and await _has(page, ".challenge-container button:has-text('Draw something first')"):
            return 0.9
        return 0.0

    async def run_canvas(page) -> SkillResult:
        canvas = page.locator(".challenge-container canvas").first
        box = await canvas.bounding_box()
        if not box:
            return SkillResult(ok=False, notes="no canvas bbox", params={})
        x0 = box["x"] + box["width"] * 0.2
        x1 = box["x"] + box["width"] * 0.8
        for j in range(3):
            y = box["y"] + box["height"] * (0.25 + 0.2 * j)
            await page.mouse.move(x0, y)
            await page.mouse.down()
            await page.mouse.move(x1, y + box["height"] * 0.03)
            await page.mouse.up()
            await asyncio.sleep(0.05)
        # Click reveal if present.
        await _click_all(page, ".challenge-container button:has-text('Reveal Code')", max_clicks=1)
        await asyncio.sleep(0.2)
        return SkillResult(ok=True, notes="drew strokes + clicked reveal", params={})

    async def match_audio(page) -> float:
        return 0.9 if await _has(page, ".challenge-container button:has-text('Play Audio')") else 0.0

    async def run_audio(page) -> SkillResult:
        n = await _click_all(page, ".challenge-container button:has-text('Play Audio')", max_clicks=1)
        return SkillResult(ok=n > 0, notes=f"clicked={n}", params={})

    async def match_video(page) -> float:
        return 0.9 if await _has(page, ".challenge-container button:has-text('Frame 43')") else 0.0

    async def run_video(page) -> SkillResult:
        clicked = await _click_n(page, ".challenge-container button:has-text('Frame 43')", 3)
        await asyncio.sleep(0.2)
        return SkillResult(ok=clicked >= 3, notes=f"clicked={clicked}", params={})

    async def match_split_parts(page) -> float:
        return 0.9 if await _has(page, ".challenge-container [data-part]") else 0.0

    async def run_split_parts(page) -> SkillResult:
        clicked = await _click_all(page, ".challenge-container [data-part]", max_clicks=8)
        await asyncio.sleep(0.2)
        return SkillResult(ok=clicked >= 4, notes=f"clicked={clicked}", params={})

    async def match_encoded_base64(page) -> float:
        return 0.9 if await _has(page, ".challenge-container #base64-input") else 0.0

    async def run_encoded_base64(page) -> SkillResult:
        try:
            await page.locator(".challenge-container #base64-input").first.fill("AAAAAA")
        except Exception:
            pass
        clicked = await _click_all(page, ".challenge-container button:has-text('Reveal')", max_clicks=1)
        await asyncio.sleep(0.2)
        return SkillResult(ok=clicked > 0, notes="filled+reveal", params={})

    async def match_obfuscated(page) -> float:
        return 0.9 if await _has(page, ".challenge-container #obfuscated-input") else 0.0

    async def run_obfuscated(page) -> SkillResult:
        js = r"""() => {
          const re = /\b[A-HJ-NP-Z2-9]{6}\b/;
          const p = Array.from(document.querySelectorAll('.challenge-container p'))
            .map(el => (el.innerText || '').trim().toUpperCase())
            .find(t => re.test(t));
          if (!p) return {ok:false, why:"no reversed code"};
          const reversed = p.match(re)[0];
          const decoded = reversed.split('').reverse().join('');
          const input = document.querySelector('.challenge-container #obfuscated-input');
          const btn = document.querySelector('.challenge-container #obfuscated-submit, .challenge-container button');
          if (!input) return {ok:false, why:"no input"};
          input.value = decoded;
          input.dispatchEvent(new Event('input', {bubbles:true}));
          return {ok:true, decoded};
        }"""
        res = await page.evaluate(js)
        await _click_all(page, ".challenge-container #obfuscated-submit, .challenge-container button:has-text('Submit')", max_clicks=1)
        await asyncio.sleep(0.2)
        return SkillResult(ok=bool(res and res.get("ok")), notes=str(res), params={})

    async def match_multi_tab(page) -> float:
        if await _has(page, ".challenge-container [data-tab]"):
            return 0.85
        txt = await _challenge_text(page)
        return 0.6 if re.search(r"\btab\s+\d+\b", txt, flags=re.I) else 0.0

    async def run_multi_tab(page) -> SkillResult:
        btns = page.locator(".challenge-container [data-tab]")
        n = await btns.count()
        clicked = 0
        for i in range(n):
            try:
                await btns.nth(i).click(timeout=1200)
                clicked += 1
                await asyncio.sleep(0.1)
            except Exception:
                pass
        await asyncio.sleep(0.2)
        return SkillResult(ok=clicked >= 5, notes=f"clicked={clicked}", params={})

    async def match_gesture(page) -> float:
        if await _has(page, ".challenge-container canvas") and await _has(page, ".challenge-container button:has-text('Complete')"):
            return 0.75
        return 0.0

    async def run_gesture(page) -> SkillResult:
        canvas = page.locator(".challenge-container canvas").first
        box = await canvas.bounding_box()
        if box:
            x0 = box["x"] + box["width"] * 0.25
            x1 = box["x"] + box["width"] * 0.75
            y = box["y"] + box["height"] * 0.5
            await page.mouse.move(x0, y)
            await page.mouse.down()
            await page.mouse.move(x1, y)
            await page.mouse.up()
        await asyncio.sleep(0.15)
        await _click_all(page, ".challenge-container button:has-text('Complete')", max_clicks=1)
        await asyncio.sleep(0.2)
        return SkillResult(ok=True, notes="stroke+complete", params={})

    async def match_sequence(page) -> float:
        txt = await _challenge_text(page)
        if re.search(r"Progress:\s*\d+/4", txt):
            return 0.9
        if await _has(page, ".challenge-container #seq-complete-btn"):
            return 0.9
        return 0.0

    async def run_sequence(page) -> SkillResult:
        # Click
        await _click_all(page, ".challenge-container button:has-text('Click Me')", max_clicks=1)
        # Hover (800ms+)
        try:
            ha = page.locator(".challenge-container [data-hover-area], .challenge-container #seq-hover-area")
            if await ha.count() > 0:
                await ha.first.hover(timeout=1500)
                await asyncio.sleep(1.0)
        except Exception:
            pass
        # Type
        try:
            inp = page.locator(".challenge-container input")
            if await inp.count() > 0:
                await inp.first.fill("x")
        except Exception:
            pass
        # Scroll inside box
        try:
            await page.evaluate("() => { const b = document.querySelector('.challenge-container [data-scroll-box], .challenge-container #seq-scroll-box'); if (b) b.scrollTop = b.scrollHeight; }")
        except Exception:
            pass
        await asyncio.sleep(0.2)
        # Complete
        await _click_all(page, ".challenge-container #seq-complete-btn, .challenge-container button:has-text('Complete')", max_clicks=1)
        await asyncio.sleep(0.2)
        return SkillResult(ok=True, notes="sequence actions executed", params={})

    async def match_math(page) -> float:
        txt = await _challenge_text(page)
        if re.search(r"\d+\s*\+\s*\d+\s*=\s*\?", txt):
            return 0.85
        if re.search(r"\bcalculate:", txt, flags=re.I) and re.search(r"\d+\s*[x×]\s*\d+\s*\+\s*\d+", txt, flags=re.I):
            return 0.85
        return 0.0

    async def run_math(page) -> SkillResult:
        txt = await _challenge_text(page)
        ans: str | None = None
        m = re.search(r"(\d+)\s*\+\s*(\d+)\s*=\s*\?", txt)
        if m:
            ans = str(int(m.group(1)) + int(m.group(2)))
        m = re.search(r"(\d+)\s*[x×]\s*(\d+)\s*\+\s*(\d+)\s*=\s*\?", txt, flags=re.I)
        if m:
            ans = str(int(m.group(1)) * int(m.group(2)) + int(m.group(3)))
        if ans is None:
            return SkillResult(ok=False, notes="no expression", params={})
        try:
            inp = page.locator(".challenge-container input")
            if await inp.count() > 0:
                await inp.first.fill(ans)
        except Exception:
            pass
        await _click_all(page, ".challenge-container button:has-text('Solve')", max_clicks=1)
        await asyncio.sleep(0.2)
        return SkillResult(ok=True, notes=f"ans={ans}", params={})

    async def match_shadow_dom(page) -> float:
        return 0.95 if await _has(page, ".challenge-container .shadow-level-host") else 0.0

    async def run_shadow_dom(page) -> SkillResult:
        js = r"""() => {
          let host = document.querySelector('.challenge-container .shadow-level-host');
          let clicks = 0;
          for (let i = 0; i < 10 && host; i++) {
            const inner = host.shadowRoot && host.shadowRoot.querySelector('div');
            if (inner) { inner.click(); clicks++; }
            host = host.shadowRoot && host.shadowRoot.querySelector('.shadow-level-host');
          }
          return {ok: clicks > 0, clicks};
        }"""
        res = await page.evaluate(js)
        await asyncio.sleep(0.3)
        return SkillResult(ok=bool(res and res.get("ok")), notes=str(res), params={})

    async def match_websocket(page) -> float:
        return 0.85 if await _has(page, ".challenge-container button:has-text('Connect')") and await _has(page, ".challenge-container pre") else 0.0

    async def run_websocket(page) -> SkillResult:
        await _click_all(page, ".challenge-container button:has-text('Connect')", max_clicks=1)
        await asyncio.sleep(4.0)
        return SkillResult(ok=True, notes="connected+waited", params={})

    async def match_service_worker(page) -> float:
        return 0.9 if await _has(page, ".challenge-container button:has-text('Register Service Worker')") else 0.0

    async def run_service_worker(page) -> SkillResult:
        await _click_all(page, ".challenge-container button:has-text('Register Service Worker')", max_clicks=1)
        await asyncio.sleep(2.0)
        await _click_all(page, ".challenge-container button:has-text('Retrieve from Cache')", max_clicks=1)
        await asyncio.sleep(0.2)
        return SkillResult(ok=True, notes="register+retrieve", params={})

    async def match_mutation(page) -> float:
        return 0.9 if await _has(page, ".challenge-container button:has-text('Trigger Mutation')") else 0.0

    async def run_mutation(page) -> SkillResult:
        await _click_n(page, ".challenge-container button:has-text('Trigger Mutation')", 5)
        await asyncio.sleep(0.2)
        await _click_all(page, ".challenge-container button:has-text('Reveal Code')", max_clicks=1)
        await asyncio.sleep(0.2)
        return SkillResult(ok=True, notes="mutations+reveal", params={})

    async def match_recursive_iframe(page) -> float:
        return 0.85 if await _has(page, ".challenge-container button:has-text('Extract Code')") else 0.0

    async def run_recursive_iframe(page) -> SkillResult:
        await _click_all(page, ".challenge-container button:has-text('Extract Code')", max_clicks=1)
        await asyncio.sleep(0.2)
        return SkillResult(ok=True, notes="clicked extract", params={})

    # Fallback skill: click all non-nav buttons inside challenge container once.
    async def match_click_misc(page) -> float:
        return 0.1 if await _has(page, ".challenge-container button") else 0.0

    async def run_click_misc(page) -> SkillResult:
        block = re.compile(r"^(next|continue|proceed|go forward|advance|move on|click here)$", re.I)
        btns = page.locator(".challenge-container button")
        n = await btns.count()
        clicked = 0
        for i in range(min(n, 12)):
            b = btns.nth(i)
            try:
                txt = (await b.inner_text()).strip()
            except Exception:
                continue
            if not txt or block.match(txt) or txt.lower().startswith("submit code"):
                continue
            try:
                await b.click(timeout=1000)
                clicked += 1
                await asyncio.sleep(0.15)
            except Exception:
                pass
        return SkillResult(ok=clicked > 0, notes=f"clicked={clicked}", params={})

    return [
        SkillDef("shadow_dom", match_shadow_dom, run_shadow_dom),
        SkillDef("drag_drop", match_drag_drop, run_drag_drop),
        SkillDef("keyboard_sequence", match_keyboard_sequence, run_keyboard_sequence),
        SkillDef("sequence", match_sequence, run_sequence),
        SkillDef("canvas", match_canvas, run_canvas),
        SkillDef("gesture", match_gesture, run_gesture),
        SkillDef("split_parts", match_split_parts, run_split_parts),
        SkillDef("video", match_video, run_video),
        SkillDef("service_worker", match_service_worker, run_service_worker),
        SkillDef("mutation", match_mutation, run_mutation),
        SkillDef("websocket", match_websocket, run_websocket),
        SkillDef("encoded_base64", match_encoded_base64, run_encoded_base64),
        SkillDef("obfuscated", match_obfuscated, run_obfuscated),
        SkillDef("multi_tab", match_multi_tab, run_multi_tab),
        SkillDef("timing_or_rotating", match_capture, run_capture),
        SkillDef("hover_reveal", match_hover, run_hover),
        SkillDef("memory", match_memory, run_memory),
        SkillDef("click_reveal", match_click_reveal, run_click_reveal),
        SkillDef("scroll_reveal", match_scroll_reveal, run_scroll_reveal),
        SkillDef("delayed_or_conditional", match_delayed_reveal, run_delayed_reveal),
        SkillDef("audio", match_audio, run_audio),
        SkillDef("math", match_math, run_math),
        SkillDef("click_misc", match_click_misc, run_click_misc),
    ]
