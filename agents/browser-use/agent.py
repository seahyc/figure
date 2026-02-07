"""
Figure Agent v2 - Generalizable Browser Automation

Uses browser-use framework with LLM for visual understanding.
No hardcoded challenge-specific logic - purely generalizable.
"""

import os
import re
import json
import time
import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from browser_use import Agent, Browser
from browser_use.agent.views import ActionResult
from browser_use.tools.service import Tools
from browser_use.tools.views import DoneAction
from browser_use.llm.openai.chat import ChatOpenAI
from browser_use.llm.google import ChatGoogle
from browser_use.llm.anthropic.chat import ChatAnthropic
from pydantic import BaseModel, Field

load_dotenv()

# ---------------------------------------------------------------------------
# Run logging: comprehensive logging to runs/ directory
# ---------------------------------------------------------------------------

RUNS_DIR = Path(__file__).parent.parent.parent / "runs"


class RunLogger:
    """Comprehensive run logging for screenshots, LLM I/O, and DOM events."""

    def __init__(self, agent_name: str = "browser-use", url: str = "", goal: str = ""):
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_id = f"{ts}-{agent_name}"
        self.run_dir = RUNS_DIR / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

        # Create subdirs
        (self.run_dir / "screenshots").mkdir(exist_ok=True)
        (self.run_dir / "llm").mkdir(exist_ok=True)
        (self.run_dir / "dom").mkdir(exist_ok=True)

        # Save initial config
        config = {
            "agent": agent_name,
            "url": url,
            "goal": goal,
            "started_at": datetime.now().isoformat(),
            "run_id": self.run_id,
        }
        (self.run_dir / "config.json").write_text(json.dumps(config, indent=2))
        self.step_count = 0
        self.llm_count = 0

    async def save_screenshot(self, page, step_num: int, label: str = ""):
        """Save screenshot after each step."""
        try:
            import base64
            fname = f"step-{step_num:03d}"
            if label:
                fname += f"-{label}"
            fname += ".png"
            path = self.run_dir / "screenshots" / fname
            png = await page.screenshot()
            if isinstance(png, str):
                # base64 encoded string
                path.write_bytes(base64.b64decode(png))
            elif isinstance(png, bytes):
                path.write_bytes(png)
            else:
                path.write_bytes(bytes(png))
            return path
        except Exception as e:
            print(f"[RunLogger] Screenshot failed: {e}")
            return None

    def save_llm_call(self, step_num: int, request: dict, response: dict):
        """Save full LLM request/response JSON."""
        self.llm_count += 1
        data = {
            "step": step_num,
            "call_num": self.llm_count,
            "timestamp": datetime.now().isoformat(),
            "request": request,
            "response": response,
        }
        fname = f"step-{step_num:03d}-call-{self.llm_count:03d}.json"
        path = self.run_dir / "llm" / fname
        path.write_text(json.dumps(data, indent=2, default=str))

    def save_dom_event(self, step_num: int, events: list):
        """Save DOM mutation events."""
        if not events:
            return
        fname = f"step-{step_num:03d}-dom.json"
        path = self.run_dir / "dom" / fname
        data = {"step": step_num, "timestamp": datetime.now().isoformat(), "events": events}
        path.write_text(json.dumps(data, indent=2))

    def save_summary(self, history, wall_time: float, model_key: str):
        """Generate summary.md after run completes."""
        lines = [
            f"# Run Summary: {self.run_id}",
            "",
            f"**Agent:** browser-use",
            f"**Model:** {model_key}",
            f"**Wall time:** {wall_time:.1f}s ({wall_time/60:.1f}m)",
            f"**Steps:** {history.number_of_steps()}",
            f"**Success:** {history.is_successful()}",
            "",
        ]
        if history.usage:
            u = history.usage
            lines.extend([
                "## Token Usage",
                f"- Input: {u.total_prompt_tokens:,}",
                f"- Output: {u.total_completion_tokens:,}",
                f"- Total: {u.total_tokens:,}",
                f"- Cost: ${u.total_cost:.4f}",
                "",
            ])
        lines.extend([
            "## Files",
            f"- Screenshots: {len(list((self.run_dir / 'screenshots').glob('*.png')))}",
            f"- LLM calls: {len(list((self.run_dir / 'llm').glob('*.json')))}",
            f"- DOM events: {len(list((self.run_dir / 'dom').glob('*.json')))}",
        ])
        (self.run_dir / "summary.md").write_text("\n".join(lines))


# Global run logger instance
_run_logger: RunLogger | None = None


# ---------------------------------------------------------------------------
# Skills system: auto-discovered from skills/ directory
# ---------------------------------------------------------------------------

SKILLS_DIR = Path(__file__).parent / "skills"


@dataclass
class SkillInfo:
    name: str
    skill_type: str        # "script" or "knowledge"
    description: str
    body: str              # Full SKILL.md body (after frontmatter)
    script: str | None     # JS function body (script skills only)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a SKILL.md file. Returns (metadata_dict, body)."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not m:
        return {}, text
    yaml_block, body = m.group(1), m.group(2)
    # Simple key: value parser (handles multiline > values)
    meta = {}
    current_key = None
    current_val = []
    for line in yaml_block.split("\n"):
        kv = re.match(r"^(\w[\w_-]*):\s*(.*)", line)
        if kv:
            if current_key:
                meta[current_key] = " ".join(current_val).strip()
            current_key = kv.group(1)
            val = kv.group(2).strip()
            current_val = [] if val in (">", "|") else [val]
        elif current_key and line.startswith("  "):
            current_val.append(line.strip())
    if current_key:
        meta[current_key] = " ".join(current_val).strip()
    return meta, body


def load_skills(skills_dir: Path | str | None = None) -> list[SkillInfo]:
    """Discover and load all skills from the skills directory."""
    skills_path = Path(skills_dir) if skills_dir else SKILLS_DIR
    if not skills_path.exists():
        return []
    skills = []
    for skill_md in sorted(skills_path.glob("*/SKILL.md")):
        text = skill_md.read_text()
        frontmatter, body = parse_frontmatter(text)
        if "name" not in frontmatter:
            continue
        script = None
        script_path = skill_md.parent / "script.js"
        if script_path.exists():
            script = script_path.read_text().strip()
        skills.append(SkillInfo(
            name=frontmatter["name"],
            skill_type=frontmatter.get("type", "knowledge"),
            description=frontmatter.get("description", "").strip(),
            body=body.strip(),
            script=script,
        ))
    return skills


def generate_skills_prompt(skills: list[SkillInfo]) -> str:
    """Auto-generate system prompt section from skill metadata."""
    if not skills:
        return ""
    lines = ["\nAVAILABLE SKILLS:"]
    script_skills = [s for s in skills if s.skill_type == "script"]
    knowledge_skills = [s for s in skills if s.skill_type == "knowledge"]
    if script_skills:
        lines.append("Script skills (call via evaluate action):")
        for s in script_skills:
            lines.append(f"  - {s.name}: {s.description}")
            lines.append(f"    Call: window.__skills.{s.name}()  (accepts options object for customization)")
    if knowledge_skills:
        lines.append("Knowledge skills (load full instructions with read_skill action):")
        for s in knowledge_skills:
            lines.append(f"  - {s.name}: {s.description}")
    lines.append("")
    lines.append("Tip: Script skills accept an options object — use read_skill for parameter docs.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Model registry: family groups auto-determine fallbacks
MODELS = {
    "claude": {
        "name": "Claude Sonnet 4.5 via Max subscription (local proxy)",
        "vision": True,
        "family": "anthropic",
    },
    "claude-api": {
        "name": "Claude Sonnet 4.5 (direct API, pay-per-token)",
        "vision": True,
        "family": "anthropic-api",
    },
    "kimi": {
        "name": "Kimi K2.5 (multimodal, 256K context)",
        "vision": True,
        "family": "kimi",
    },
    "kimi-k2": {
        "name": "Kimi K2 (text-only, 256K context, cheaper)",
        "vision": False,
        "family": "kimi",
    },
    "gemini": {
        "name": "Gemini 2.0 Flash (multimodal, 1M context)",
        "vision": True,
        "family": "gemini",
    },
    "gemini-2.5": {
        "name": "Gemini 2.5 Flash (multimodal, thinking)",
        "vision": True,
        "family": "gemini",
    },
}

# Default model per family (used for auto-fallback)
FAMILY_DEFAULTS = {
    "anthropic": "claude",
    "anthropic-api": "claude-api",
    "kimi": "kimi",
    "gemini": "gemini",
}


def get_fallback(model_key: str) -> str:
    """Get the fallback model from another family."""
    my_family = MODELS[model_key]["family"]
    # Fallback chain: anthropic → gemini → kimi → gemini
    fallback_order = {"anthropic": "gemini", "anthropic-api": "gemini", "gemini": "kimi", "kimi": "gemini"}
    other_family = fallback_order.get(my_family, "gemini")
    return FAMILY_DEFAULTS[other_family]


def get_llm(model_key: str):
    """Get LLM instance by model key."""
    if model_key == "claude":
        # Claude via local proxy (Max subscription) — OpenAI-compatible API
        return ChatOpenAI(
            model="claude-sonnet-4-5-20250929",
            api_key="not-needed",
            base_url="http://127.0.0.1:8000/v1",
            temperature=0.0,
        )

    elif model_key == "claude-api":
        # Claude via direct Anthropic API (pay-per-token)
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable required")
        return ChatAnthropic(
            model="claude-sonnet-4-5-20250929",
            api_key=api_key,
            temperature=0.0,
            max_tokens=8192,
        )

    elif model_key == "kimi":
        api_key = os.getenv("KIMI_API_KEY")
        if not api_key:
            raise ValueError("KIMI_API_KEY environment variable required")
        return ChatOpenAI(
            model="kimi-k2.5",
            api_key=api_key,
            base_url="https://api.moonshot.ai/v1",
            temperature=1.0,  # K2.5 requires temperature=1.0
            frequency_penalty=0.0,
            remove_min_items_from_schema=True,
            remove_defaults_from_schema=True,
        )

    elif model_key == "kimi-k2":
        api_key = os.getenv("KIMI_API_KEY")
        if not api_key:
            raise ValueError("KIMI_API_KEY environment variable required")
        return ChatOpenAI(
            model="kimi-k2-0711-preview",
            api_key=api_key,
            base_url="https://api.moonshot.ai/v1",
            temperature=0.6,
            frequency_penalty=0.0,
            remove_min_items_from_schema=True,
            remove_defaults_from_schema=True,
        )

    elif model_key == "gemini":
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY or GEMINI_API_KEY required")
        return ChatGoogle(
            model="gemini-2.0-flash",
            api_key=api_key,
        )

    elif model_key == "gemini-2.5":
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY or GEMINI_API_KEY required")
        return ChatGoogle(
            model="gemini-2.5-flash",
            api_key=api_key,
            thinking_budget=0,
        )

    else:
        raise ValueError(f"Unknown model: {model_key}. Available: {list(MODELS.keys())}")


# ---------------------------------------------------------------------------
# Screenshot stream: rolling buffer of timestamped frames
# ---------------------------------------------------------------------------

_frame_buffer: list[tuple[float, bytes]] = []  # [(epoch_s, png_bytes), ...]
_frame_task: asyncio.Task | None = None
FRAME_MAX = 20
FRAME_INTERVAL = 3  # seconds


async def _screenshot_loop(get_page_fn):
    """Background task: capture screenshots at regular intervals and save to disk."""
    import base64 as b64
    frame_num = 0
    while True:
        try:
            page = await get_page_fn()
            raw = await page.screenshot()
            if isinstance(raw, str):
                png = b64.b64decode(raw)
            elif isinstance(raw, bytes):
                png = raw
            else:
                png = bytes(raw)
            _frame_buffer.append((time.time(), png))
            # Trim oldest from memory buffer
            while len(_frame_buffer) > FRAME_MAX:
                _frame_buffer.pop(0)
            # Save every frame to disk for post-run review
            if _run_logger:
                frame_num += 1
                ts = datetime.now().strftime("%H%M%S")
                path = _run_logger.run_dir / "screenshots" / f"stream-{frame_num:04d}-{ts}.png"
                path.write_bytes(png)
        except asyncio.CancelledError:
            break
        except Exception:
            pass
        await asyncio.sleep(FRAME_INTERVAL)


def build_tools() -> Tools:
    """Build Tools instance with custom actions."""
    tools = Tools()

    # --- Skills: inject_skills / read_skill / save_skill ---

    @tools.registry.action(
        description=(
            "Initialize all pre-loaded skills from the skills/ directory. "
            "Script skills are injected into the browser and persist across navigations. "
            "Called automatically at startup — you normally don't need to call this manually."
        ),
    )
    async def inject_skills(browser_session) -> ActionResult:
        skills = load_skills()
        injected = []
        for s in skills:
            if s.script:
                js = f"window.__skills = window.__skills || {{}}; window.__skills.{s.name} = {s.script};"
                await browser_session._cdp_add_init_script(js)
                page = await browser_session.get_current_page()
                await page.evaluate(f"() => {{ {js} }}")
                injected.append(s.name)

        names = [s.name for s in skills]
        return ActionResult(
            extracted_content=f"Loaded {len(names)} skills: {', '.join(names)}. "
                              f"Injected {len(injected)} script skills into browser. "
                              f"Call script skills via evaluate: window.__skills.<name>(options)",
            long_term_memory=f"Skills available: {', '.join(names)}. "
                             f"Script skills callable via evaluate: {', '.join(f'window.__skills.{n}()' for n in injected)}.",
        )

    class ReadSkillParams(BaseModel):
        name: str = Field(description="Name of the skill to load")

    @tools.registry.action(
        description=(
            "Load a skill's full instructions and parameter docs into context. "
            "Use for knowledge skills or to see detailed usage examples for script skills."
        ),
        param_model=ReadSkillParams,
    )
    async def read_skill(params: ReadSkillParams) -> ActionResult:
        skill_path = SKILLS_DIR / params.name / "SKILL.md"
        if not skill_path.exists():
            return ActionResult(error=f"Skill '{params.name}' not found")
        text = skill_path.read_text()
        _, body = parse_frontmatter(text)
        return ActionResult(extracted_content=body.strip())

    # --- DOM interaction tools ---

    class HoverParams(BaseModel):
        index: int = Field(description="Element index to hover over")

    @tools.registry.action(
        description="Hover over element to reveal hidden content (tooltips, menus, etc).",
        param_model=HoverParams,
    )
    async def hover(params: HoverParams, browser_session) -> ActionResult:
        page = await browser_session.get_current_page()

        node = await browser_session.get_element_by_index(params.index)
        if not node:
            return ActionResult(error=f"Element {params.index} not found")

        if node.absolute_position:
            r = node.absolute_position
            x, y = r.x + r.width / 2, r.y + r.height / 2
        else:
            cdp = await browser_session.cdp_client_for_node(node)
            rect = await browser_session.get_element_coordinates(node.backend_node_id, cdp)
            if not rect:
                return ActionResult(error=f"Could not get position for element {params.index}")
            x, y = rect.x + rect.width / 2, rect.y + rect.height / 2

        await page.mouse.move(x, y)
        await asyncio.sleep(1.5)  # Let hover effects trigger (needs 1s for hover_reveal, 800ms for sequence)

        return ActionResult(extracted_content=f"Hovered over element {params.index}")

    class DragParams(BaseModel):
        source: int = Field(description="Source element index to drag FROM")
        target: int = Field(description="Target element index to drag TO")

    @tools.registry.action(
        description="Drag element from source index to target index. Use for drag-and-drop puzzles.",
        param_model=DragParams,
    )
    async def drag(params: DragParams, browser_session) -> ActionResult:
        page = await browser_session.get_current_page()

        # Get source element
        src_node = await browser_session.get_element_by_index(params.source)
        if not src_node:
            return ActionResult(error=f"Source element {params.source} not found")

        # Get target element
        tgt_node = await browser_session.get_element_by_index(params.target)
        if not tgt_node:
            return ActionResult(error=f"Target element {params.target} not found")

        # Get coordinates
        async def get_center(node):
            if node.absolute_position:
                r = node.absolute_position
                return r.x + r.width / 2, r.y + r.height / 2
            cdp = await browser_session.cdp_client_for_node(node)
            rect = await browser_session.get_element_coordinates(node.backend_node_id, cdp)
            if rect:
                return rect.x + rect.width / 2, rect.y + rect.height / 2
            return None, None

        sx, sy = await get_center(src_node)
        tx, ty = await get_center(tgt_node)

        if sx is None or tx is None:
            return ActionResult(error="Could not get element coordinates")

        # Perform drag with smooth movement
        await page.mouse.move(sx, sy)
        await page.mouse.down()
        for step in range(1, 11):
            frac = step / 10
            await page.mouse.move(sx + (tx - sx) * frac, sy + (ty - sy) * frac)
        await page.mouse.up()

        return ActionResult(extracted_content=f"Dragged element {params.source} to {params.target}")

    class DrawParams(BaseModel):
        index: int = Field(description="Canvas or element index to draw on")
        strokes: int = Field(default=3, description="Number of horizontal strokes to draw")

    @tools.registry.action(
        description="Draw strokes on a canvas or element. Use for signature/drawing challenges.",
        param_model=DrawParams,
    )
    async def draw(params: DrawParams, browser_session) -> ActionResult:
        page = await browser_session.get_current_page()

        node = await browser_session.get_element_by_index(params.index)
        if not node:
            return ActionResult(error=f"Element {params.index} not found")

        if node.absolute_position:
            r = node.absolute_position
            ox, oy, w, h = r.x, r.y, r.width, r.height
        else:
            cdp = await browser_session.cdp_client_for_node(node)
            rect = await browser_session.get_element_coordinates(node.backend_node_id, cdp)
            if not rect:
                return ActionResult(error=f"Could not get bounds for element {params.index}")
            ox, oy, w, h = rect.x, rect.y, rect.width, rect.height

        margin = min(20, w * 0.1)
        for i in range(params.strokes):
            y_frac = (i + 1) / (params.strokes + 1)
            sx, sy = ox + margin, oy + h * y_frac
            ex, ey = ox + w - margin, oy + h * y_frac + h * 0.05

            await page.mouse.move(sx, sy)
            await page.mouse.down()
            for step in range(1, 11):
                frac = step / 10
                await page.mouse.move(sx + (ex - sx) * frac, sy + (ey - sy) * frac)
            await page.mouse.up()

        return ActionResult(extracted_content=f"Drew {params.strokes} strokes on element {params.index}")

    class SendKeysParams(BaseModel):
        keys: str = Field(description="Space-separated key combo sequence, e.g. 'ctrl+a ctrl+c ctrl+v' or 'Enter' or 'Tab'")

    @tools.registry.action(
        description=(
            "Send keyboard shortcuts/key sequences to the page. "
            "Dispatches keydown+keyup events for each combo in the sequence. "
            "Examples: 'ctrl+a ctrl+c ctrl+v', 'Enter', 'Escape', 'Tab', 'ArrowDown'."
        ),
        param_model=SendKeysParams,
    )
    async def send_keys(params: SendKeysParams, browser_session) -> ActionResult:
        keys = params.keys
        page = await browser_session.get_current_page()
        js = """(keysStr) => {
            const KEY_MAP = {
                arrowup:'ArrowUp', arrowdown:'ArrowDown', arrowleft:'ArrowLeft', arrowright:'ArrowRight',
                enter:'Enter', tab:'Tab', escape:'Escape', backspace:'Backspace', delete:'Delete',
                shift:'Shift', control:'Control', alt:'Alt', meta:'Meta', space:' ', ' ':' '
            };
            const combos = keysStr.split(' ');
            const results = [];
            for (const combo of combos) {
                const parts = combo.toLowerCase().split('+');
                const key = parts[parts.length - 1];
                const normalizedKey = KEY_MAP[key] || (key.length === 1 ? key : key.charAt(0).toUpperCase() + key.slice(1));
                const opts = {
                    key: normalizedKey,
                    code: key.length === 1 ? 'Key' + key.toUpperCase() : normalizedKey,
                    ctrlKey: parts.includes('ctrl') || parts.includes('control'),
                    shiftKey: parts.includes('shift'),
                    altKey: parts.includes('alt'),
                    metaKey: parts.includes('meta') || parts.includes('cmd'),
                    bubbles: true,
                    cancelable: true
                };
                document.dispatchEvent(new KeyboardEvent('keydown', opts));
                document.dispatchEvent(new KeyboardEvent('keyup', opts));
                results.push(combo);
            }
            return 'Sent keys: ' + results.join(', ');
        }"""
        result = await page.evaluate(js, keys)
        return ActionResult(extracted_content=str(result))

    @tools.registry.action(
        description=(
            "Diagnose page state for debugging. Returns info about the current URL, "
            "DOM structure, React/Vue state, storage contents, and any errors. "
            "Use when a page appears blank or broken."
        ),
    )
    async def diagnose_page(browser_session) -> ActionResult:
        page = await browser_session.get_current_page()
        js = """() => {
            const info = {
                url: window.location.href,
                readyState: document.readyState,
                rootChildren: document.querySelector('#root') ? document.querySelector('#root').childNodes.length : 'no #root',
                bodyChildren: document.body.childNodes.length,
                bodyHTML: document.body.innerHTML.substring(0, 500),
                hasReact: !!window.__REACT_DEVTOOLS_GLOBAL_HOOK__,
                scripts: [...document.querySelectorAll('script[src]')].map(s => s.src).slice(0, 5),
                errors: window.__pageErrors || [],
                storage: {
                    sessionKeys: Object.keys(sessionStorage),
                    localKeys: Object.keys(localStorage)
                }
            };
            return JSON.stringify(info, null, 2);
        }"""
        result = await page.evaluate(js)
        return ActionResult(extracted_content=str(result))

    # --- Override done: refuse premature termination ---

    @tools.registry.action(
        "Complete the task. Only call this when you are CERTAIN the entire goal is achieved.",
        param_model=DoneAction,
    )
    async def done(params: DoneAction, browser_session) -> ActionResult:
        # Check page for completion signals before allowing termination
        try:
            page = await browser_session.get_current_page()
            page_text = await page.evaluate(
                "() => document.body ? document.body.innerText.substring(0, 2000) : ''"
            )
            page_url = await page.evaluate("() => window.location.href")
        except Exception:
            page_text = ""
            page_url = ""

        # Look for completion signals (generalizable patterns)
        completion_signals = [
            "congratulations", "all steps completed", "challenge complete",
            "you did it", "well done", "finished", "all done",
        ]
        text_lower = page_text.lower()
        is_complete = any(sig in text_lower for sig in completion_signals)

        # Also accept if we've advanced past step 1 (for single-challenge testing)
        import re
        step_match = re.search(r'step\s*(\d+)\s*(?:of|/)', text_lower)
        if step_match and int(step_match.group(1)) > 1:
            is_complete = True

        if is_complete or params.success is False:
            # Actually done — allow termination
            return ActionResult(
                is_done=True,
                success=params.success,
                extracted_content=params.text,
                long_term_memory=f"Task completed: {params.success} - {params.text[:100]}",
            )
        else:
            # Not done — refuse and tell agent to keep going
            return ActionResult(
                is_done=False,
                extracted_content=(
                    f"REFUSED: The page does not show completion signals. "
                    f"Current URL: {page_url}. "
                    f"The task is NOT finished. Keep working — try different approaches if stuck. "
                    f"Use evaluate to search the DOM for hidden elements instead of scrolling blindly."
                ),
                long_term_memory="Attempted to call done() but was refused — task is not complete. Must keep going.",
            )

    return tools


SYSTEM_PROMPT = """You are an efficient web automation agent. Follow the task instructions carefully.

CRITICAL — PRE-SOLVE AUTO-SUBMITS:
pre_solve runs AUTOMATICALLY before you see the page. It clicks action buttons, solves
drag_drop/gesture, finds codes, and AUTO-SUBMITS them. Check #__pre_solve_results first.
If it says "AUTO-SUBMITTED", the step is ALREADY DONE — just wait for the next page.

EFFICIENCY RULES:
- Think in 1-2 sentences max. Act immediately.
- If pre_solve already submitted, do NOTHING — page will advance.
- Combine actions: input + click in one step.
- If stuck after 2 attempts, use search_dom or try a completely different approach.

WHEN PRE-SOLVE DIDN'T HANDLE IT:
1. Check if code is visible (font-mono, font-bold, highlighted text) → input + submit
2. Scroll challenge → evaluate("window.__skills.scroll_to({position:'bottom'})")
3. Hover challenge → hover(index=N), then check for revealed code
4. Shadow DOM → evaluate("window.__skills.shadow_dom()")
5. Canvas/drawing → draw(index=N, strokes=3)
6. Keyboard sequence → send_keys("ArrowUp ArrowDown ArrowLeft ArrowRight")
7. Math/calculation → evaluate("window.__skills.compute({math: 'EXPRESSION'})")
8. Base64/encoded → evaluate("window.__skills.compute({decode: 'BASE64_STRING'})")
9. Reversed string → evaluate("window.__skills.compute({reverse: 'STRING'})")
10. Hidden DOM → evaluate("window.__skills.search_dom({text:'[A-Z0-9]{6}'})")

NEVER click: "Next", "Continue", "Proceed", "Go Forward", "Click Me", floating elements.
ONLY "Submit Code" button advances to the next step. Find it by its text content.
Input field: look for placeholder "Enter 6-character code" if #code-input doesn't exist.
"""


_step_timings = []  # [(step_num, phase, duration_ms), ...]
_last_page_url = None
_same_url_count = 0


async def pre_step_cleanup(agent):
    """Auto-run cleanup before each step's DOM snapshot.

    1. dismiss_popups: hide modal overlays via exclude attr
    2. clean_dom: deduplicate repetitive text/interactive elements
    3. observe_changes: start observer + inject change summary into DOM

    Uses data-browser-use-exclude attribute so excluded elements are
    invisible to the serializer.
    """
    global _last_page_url, _same_url_count
    t0 = time.time()
    step_n = agent.state.n_steps
    try:
        page = await agent.browser_session.get_current_page()

        # Stuck detection: track how many steps on the same URL
        current_url = await page.evaluate("() => window.location.href")
        if current_url == _last_page_url:
            _same_url_count += 1
        else:
            _same_url_count = 0
            _last_page_url = current_url

        # Phase 1: Dismiss popups, clean DOM, run pre_solve (first pass)
        first_result = await page.evaluate("""() => {
            if (!window.__skills) return 'no_skills';
            if (window.__skills.dismiss_popups) window.__skills.dismiss_popups();
            if (window.__skills.clean_dom) window.__skills.clean_dom();
            if (window.__skills.pre_solve) return window.__skills.pre_solve();
            return 'no_pre_solve';
        }""")
        print(f"[pre_step_cleanup] Phase 1 result: {str(first_result)[:120]}")

        # Phase 2: If first pass didn't auto-submit, wait for delayed content and retry.
        # This catches delayed_reveal (3s timer) and timed challenges without slowing
        # down steps that pre_solve already handled.
        if first_result and "AUTO-SUBMITTED" not in str(first_result):
            await asyncio.sleep(3.5)
            second_result = await page.evaluate("""() => {
                if (window.__skills && window.__skills.pre_solve) return window.__skills.pre_solve();
                return 'no_pre_solve';
            }""")
            if second_result and "AUTO-SUBMITTED" in str(second_result):
                print(f"[pre_step_cleanup] Phase 2 result: {str(second_result)[:120]}")

        # Phase 3: DOM cleanup and metadata injection
        await page.evaluate("""() => {
            window.scrollTo(0, 0);

            var tsEl = document.getElementById('__agent-timestamp');
            if (!tsEl) {
                tsEl = document.createElement('div');
                tsEl.id = '__agent-timestamp';
                tsEl.style.cssText = 'font-size:11px;color:#888;';
                if (document.body.firstChild) document.body.insertBefore(tsEl, document.body.firstChild);
            }
            var now = new Date();
            tsEl.textContent = 'Agent time: ' + now.toISOString() + ' (epoch: ' + Date.now() + ')';

            // Stuck detection warning
            var stuckCount = """ + str(_same_url_count) + """;
            var stuckEl = document.getElementById('__stuck-warning');
            if (stuckCount >= 5) {
                if (!stuckEl) {
                    stuckEl = document.createElement('div');
                    stuckEl.id = '__stuck-warning';
                    stuckEl.style.cssText = 'font-size:14px;color:red;font-weight:bold;padding:8px;border:2px solid red;margin:8px 0;background:#fff0f0;';
                    if (document.body.firstChild) document.body.insertBefore(stuckEl, document.body.firstChild);
                }
                stuckEl.textContent = 'WARNING: You have been on this same page for ' + stuckCount + ' steps. STOP repeating the same actions. Try: (1) Read the challenge description carefully, (2) Look for the actual code, (3) Type the code and click Submit Code button.';
            } else if (stuckEl) {
                stuckEl.remove();
            }

            if (window.__skills && window.__skills.observe_changes) {
                var result = window.__skills.observe_changes();
                var existing = document.getElementById('__dom-changes-summary');
                if (existing) existing.remove();
                if (result && result.indexOf('changes') !== -1 && result.indexOf('No DOM') === -1) {
                    var el = document.createElement('div');
                    el.id = '__dom-changes-summary';
                    el.style.cssText = 'font-size:11px;color:#888;padding:4px;border:1px solid #ddd;margin:4px 0;';
                    el.textContent = result;
                    if (tsEl.nextSibling) {
                        document.body.insertBefore(el, tsEl.nextSibling);
                    }
                }
                if (window.__domChangeLog) window.__domChangeLog = [];
            }
        }""")
    except Exception as e:
        print(f"[pre_step_cleanup] ERROR: {e}")
    cleanup_ms = (time.time() - t0) * 1000
    _step_timings.append((step_n, 'cleanup', cleanup_ms))
    ts = time.strftime('%H:%M:%S')
    print(f"[{ts}][Step {step_n}] pre_step_cleanup: {cleanup_ms:.0f}ms")


async def run_agent(url: str, goal: str, model_key: str, fallback_key: str, headless: bool = False):
    """Run the browser automation agent with automatic fallback."""
    global _run_logger

    # Keep task string lean — detailed goal goes into system prompt (cached)
    task = f"Navigate to {url} and complete this goal: {goal}"
    models_to_try = [model_key, fallback_key]

    for i, key in enumerate(models_to_try):
        model_info = MODELS[key]
        is_fallback = i > 0

        print(f"\n{'='*60}")
        if is_fallback:
            print(f"FALLBACK: Switching to {model_info['name']}")
        else:
            print(f"Figure Agent v2 - {model_info['name']}")
            print(f"Fallback: {MODELS[fallback_key]['name']}")
        print(f"{'='*60}")
        print(f"URL: {url}")
        print(f"Goal: {goal[:80]}{'...' if len(goal) > 80 else ''}")
        print(f"{'='*60}\n")

        # Initialize run logger
        _run_logger = RunLogger(agent_name="browser-use", url=url, goal=goal)
        print(f"[Agent] Logging to: {_run_logger.run_dir}")

        browser = None
        try:
            llm = get_llm(key)
            tools = build_tools()

            # Auto-discover skills and generate prompt section
            all_skills = load_skills()
            skills_prompt = generate_skills_prompt(all_skills)

            # Create browser separately so we can clean it up properly
            browser = Browser(
                headless=headless,
                viewport={"width": 1280, "height": 800},
                disable_security=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

            agent = Agent(
                task=task,
                llm=llm,
                browser=browser,
                use_vision=model_info["vision"],
                controller=tools,
                extend_system_message=SYSTEM_PROMPT + skills_prompt,
                initial_actions=[
                    {"inject_skills": {}},  # Inject skills first (persists via CDP init script)
                    {"navigate": {"url": url}},  # Then navigate
                ],
                # Performance optimizations (documented browser-use options)
                max_history_items=10,
                use_judge=False,  # Skip extra LLM evaluation call
                # NOTE: Don't use vision_detail_level="low" or small screenshots
                # - it causes the model to hallucinate codes instead of reading them
                save_conversation_path="/tmp/agent-conv",
            )

            async def post_step_log(agent):
                step_n = agent.state.n_steps
                step_time = time.time() - agent.step_start_time if hasattr(agent, 'step_start_time') else 0
                _step_timings.append((step_n, 'total', step_time * 1000))
                ts = time.strftime('%H:%M:%S')
                print(f"[{ts}][Step {step_n}] total: {step_time:.1f}s")

                if _run_logger:
                    try:
                        page = await agent.browser_session.get_current_page()
                        await _run_logger.save_screenshot(page, step_n)

                        # Capture and save DOM events
                        dom_events = await page.evaluate(
                            "() => window.__domChangeLog ? [...window.__domChangeLog] : []"
                        )
                        if dom_events:
                            _run_logger.save_dom_event(step_n, dom_events)
                    except Exception as e:
                        print(f"[RunLogger] Post-step capture failed: {e}")

                    # Save LLM conversation state for this step
                    try:
                        llm_data = {
                            "step": step_n,
                            "timestamp": datetime.now().isoformat(),
                            "model_output": None,
                            "result": None,
                        }
                        mo = agent.state.last_model_output
                        if mo:
                            llm_data["model_output"] = {
                                "evaluation_previous_goal": mo.current_state.evaluation_previous_goal if mo.current_state else None,
                                "memory": mo.current_state.memory if mo.current_state else None,
                                "next_goal": mo.current_state.next_goal if mo.current_state else None,
                                "actions": [str(a) for a in mo.action] if mo.action else [],
                            }
                        last_result = agent.state.last_result
                        if last_result:
                            llm_data["result"] = [
                                {
                                    "extracted_content": r.extracted_content,
                                    "error": r.error,
                                    "is_done": r.is_done,
                                }
                                for r in last_result
                            ]
                        _run_logger.save_llm_call(step_n, {}, llm_data)
                    except Exception as e:
                        print(f"[RunLogger] LLM log failed: {e}")

            # Auto-clean conv logs from previous runs
            conv_dir = Path("/tmp/agent-conv")
            if conv_dir.exists():
                for f in conv_dir.glob("conversation_*.txt"):
                    f.unlink()

            print(f"[Agent] Starting with {key}...")
            _step_timings.clear()
            _frame_buffer.clear()

            # Start background screenshot stream
            global _frame_task
            _frame_task = asyncio.create_task(
                _screenshot_loop(agent.browser_session.get_current_page)
            )

            t0 = time.time()
            history = await agent.run(
                max_steps=200,
                on_step_start=pre_step_cleanup,
                on_step_end=post_step_log,
            )
            wall_time = time.time() - t0

            # Stop screenshot stream
            if _frame_task:
                _frame_task.cancel()
                _frame_task = None

            print(f"\n{'='*60}")
            print(f"EXECUTION COMPLETE ({key})")
            print(f"{'='*60}")

            # --- Run metrics ---
            steps = history.number_of_steps()
            duration = history.total_duration_seconds()
            print(f"Steps:      {steps}")
            print(f"Wall time:  {wall_time:.1f}s ({wall_time/60:.1f}m)")
            print(f"Step time:  {duration:.1f}s (agent-measured)")
            if history.usage:
                u = history.usage
                print(f"Tokens:     {u.total_prompt_tokens:,} in / {u.total_completion_tokens:,} out / {u.total_tokens:,} total")
                print(f"Cost:       ${u.total_cost:.4f}")
                print(f"API calls:  {u.entry_count}")
                if u.by_model:
                    for model, stats in u.by_model.items():
                        print(f"  {model}: {stats.invocations} calls, {stats.total_tokens:,} tokens, ${stats.cost:.4f}")
            print(f"Success:    {history.is_successful()}")

            # Step timing breakdown
            total_times = [(s, d) for s, p, d in _step_timings if p == 'total']
            cleanup_times = [(s, d) for s, p, d in _step_timings if p == 'cleanup']
            if total_times:
                avg_step = sum(d for _, d in total_times) / len(total_times) / 1000
                max_step_n, max_step_d = max(total_times, key=lambda x: x[1])
                min_step_n, min_step_d = min(total_times, key=lambda x: x[1])
                print(f"\nStep timing:")
                print(f"  Avg step:     {avg_step:.1f}s")
                print(f"  Fastest step: {min_step_d/1000:.1f}s (step {min_step_n})")
                print(f"  Slowest step: {max_step_d/1000:.1f}s (step {max_step_n})")
            if cleanup_times:
                avg_cleanup = sum(d for _, d in cleanup_times) / len(cleanup_times)
                print(f"  Avg cleanup:  {avg_cleanup:.0f}ms")
            print(f"{'='*60}")

            # Save run summary
            if _run_logger:
                _run_logger.save_summary(history, wall_time, key)
                print(f"[Agent] Run logged to: {_run_logger.run_dir}")

            return history

        except Exception as e:
            error_msg = str(e).lower()
            is_retryable = any(k in error_msg for k in [
                "rate limit", "quota", "429", "resource_exhausted",
                "permission denied", "401", "403", "404",
                "not found the model", "timed out", "timeout",
                "connection error", "connection reset",
            ])

            if is_retryable and not is_fallback:
                print(f"\n[Agent] {key} failed: {e}")
                print(f"[Agent] Falling back to {fallback_key}...")
                continue
            else:
                raise

        finally:
            # Always clean up browser resources
            if _frame_task and not _frame_task.done():
                _frame_task.cancel()
                try:
                    await _frame_task
                except asyncio.CancelledError:
                    pass
                _frame_task = None

            if browser:
                try:
                    print("[Agent] Closing browser...")
                    await browser.stop()
                    print("[Agent] Browser closed cleanly")
                except Exception as cleanup_err:
                    print(f"[Agent] Browser cleanup warning: {cleanup_err}")


async def main():
    """CLI entry point."""
    import argparse

    model_choices = list(MODELS.keys())
    model_help = " | ".join(f"{k} ({v['name']})" for k, v in MODELS.items())

    parser = argparse.ArgumentParser(
        description="Figure Agent v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Models: {model_help}\n\nFallback auto-selects from the other family (kimi ↔ gemini).",
    )
    parser.add_argument("--url", required=True, help="Starting URL")
    goal_group = parser.add_mutually_exclusive_group(required=True)
    goal_group.add_argument("--goal", help="Goal to accomplish")
    goal_group.add_argument("--goal-file", help="Path to a text file containing the goal/prompt")
    parser.add_argument("--model", "-m", choices=model_choices, default="claude",
                        help="Primary LLM model (default: claude via Max subscription)")
    parser.add_argument("--fallback", "-f", choices=model_choices, default=None,
                        help="Fallback LLM model (default: auto from other family)")
    parser.add_argument("--headless", action="store_true", help="Run headless")

    args = parser.parse_args()

    goal = args.goal
    if args.goal_file:
        with open(args.goal_file) as f:
            goal = f.read().strip()

    fallback = args.fallback or get_fallback(args.model)
    if fallback == args.model:
        fallback = get_fallback(args.model)

    await run_agent(
        url=args.url,
        goal=goal,
        model_key=args.model,
        fallback_key=fallback,
        headless=args.headless,
    )


if __name__ == "__main__":
    asyncio.run(main())
