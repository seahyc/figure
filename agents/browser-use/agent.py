"""
Figure Agent v2 - Browser Automation Agent

Uses browser-use framework with LLM for visual understanding.
General-purpose architecture with pluggable skills for domain-specific tasks.
"""

import os
import re
import json
import time
import shutil
import asyncio
import importlib.util
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType
from dotenv import load_dotenv
from browser_use import Agent, Browser, ChatBrowserUse
from browser_use.agent.views import ActionResult, AgentOutput
from browser_use.tools.service import Tools
from browser_use.tools.views import DoneAction
from browser_use.tools.registry.views import ActionModel as _ActionModelBase
from browser_use.llm.openai.chat import ChatOpenAI
from browser_use.llm.google import ChatGoogle
from browser_use.llm.anthropic.chat import ChatAnthropic
from pydantic import BaseModel, Field

load_dotenv()

# ---------------------------------------------------------------------------
# Redirect all temp files (Chromium, Playwright, tempfile) into project dir
# ---------------------------------------------------------------------------
_PROJECT_TMP = Path(__file__).parent / "tmp"
_PROJECT_TMP.mkdir(exist_ok=True)
os.environ["TMPDIR"] = str(_PROJECT_TMP)

# ---------------------------------------------------------------------------
# Run logging: comprehensive logging to runs/ directory
# ---------------------------------------------------------------------------

RUNS_DIR = Path(__file__).parent.parent.parent / "runs"
MAX_RUNS_DEFAULT = 20
MAX_SCREENSHOT_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB


def cleanup_old_runs(max_runs: int):
    """FIFO cleanup: remove oldest run dirs beyond limit, then cap screenshot size."""
    if max_runs <= 0 or not RUNS_DIR.exists():
        return
    dirs = sorted([d for d in RUNS_DIR.iterdir() if d.is_dir()], key=lambda d: d.name)
    while len(dirs) > max_runs:
        oldest = dirs.pop(0)
        print(f"[cleanup] Removing old run: {oldest.name}")
        shutil.rmtree(oldest, ignore_errors=True)
    # Cap total screenshot size across remaining runs
    total = 0
    for d in dirs:
        ss_dir = d / "screenshots"
        if ss_dir.exists():
            total += sum(f.stat().st_size for f in ss_dir.iterdir() if f.is_file())
    if total > MAX_SCREENSHOT_BYTES:
        for d in dirs:
            if total <= MAX_SCREENSHOT_BYTES:
                break
            ss_dir = d / "screenshots"
            if ss_dir.exists():
                dir_size = sum(f.stat().st_size for f in ss_dir.iterdir() if f.is_file())
                if dir_size > 0:
                    print(f"[cleanup] Removing screenshots from {d.name} ({dir_size // 1024}KB)")
                    shutil.rmtree(ss_dir, ignore_errors=True)
                    total -= dir_size


class RunLogger:
    """Comprehensive run logging for screenshots, LLM I/O, and DOM events."""

    def __init__(self, agent_name: str = "browser-use", url: str = "", goal: str = "",
                 max_runs: int = MAX_RUNS_DEFAULT):
        cleanup_old_runs(max_runs)
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

    def save_summary(self, history, wall_time: float, model_key: str,
                     step_timings: list[tuple[int, str, float]] | None = None):
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
            if u.by_model:
                lines.append("### By Model")
                for model, stats in u.by_model.items():
                    lines.append(f"- **{model}**: {stats.invocations} calls, {stats.total_tokens:,} tokens, ${stats.cost:.4f}")
                lines.append("")
        # Per-step timing table
        if step_timings:
            lines.extend(["## Per-Step Timing", ""])
            lines.append("| Step | Cleanup | LLM | Execute | Total |")
            lines.append("|------|---------|-----|---------|-------|")
            steps_seen = sorted(set(s for s, _, _ in step_timings))
            for step in steps_seen:
                by_phase = {p: d for s, p, d in step_timings if s == step}
                cleanup = by_phase.get('cleanup', 0)
                llm = by_phase.get('llm_inference', 0)
                execute = by_phase.get('execute_actions', 0)
                total = by_phase.get('total', 0)
                lines.append(f"| {step} | {cleanup/1000:.1f}s | {llm/1000:.1f}s | {execute/1000:.1f}s | {total/1000:.1f}s |")
            lines.append("")
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
    hook_module: ModuleType | None = field(default=None, repr=False)  # Python hook (hook.py)
    prompt_text: str | None = None  # System prompt extension (prompt.txt)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a SKILL.md file. Returns (metadata_dict, body)."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not m:
        return {}, text
    yaml_block, body = m.group(1), m.group(2)
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
        # Load Python hook module if present
        hook_module = None
        hook_path = skill_md.parent / "hook.py"
        if hook_path.exists():
            spec = importlib.util.spec_from_file_location(
                f"skill_hook_{frontmatter['name']}", hook_path
            )
            if spec and spec.loader:
                hook_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(hook_module)
        # Load prompt extension if present
        prompt_text = None
        prompt_path = skill_md.parent / "prompt.txt"
        if prompt_path.exists():
            prompt_text = prompt_path.read_text().strip()
        skills.append(SkillInfo(
            name=frontmatter["name"],
            skill_type=frontmatter.get("type", "knowledge"),
            description=frontmatter.get("description", "").strip(),
            body=body.strip(),
            script=script,
            hook_module=hook_module,
            prompt_text=prompt_text,
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
    "opus": {
        "name": "Claude Opus (thinking, direct API)",
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
    "gemini-2.5-pro": {
        "name": "Gemini 2.5 Pro (thinking, full)",
        "vision": True,
        "family": "gemini",
    },
    "bu-1-0": {
        "name": "ChatBrowserUse bu-1-0 (optimized for browser-use)",
        "vision": True,
        "family": "browser-use",
    },
    "bu-2-0": {
        "name": "ChatBrowserUse bu-2-0 (premium browser-use model)",
        "vision": True,
        "family": "browser-use",
    },
}

# Default model per family (used for auto-fallback)
FAMILY_DEFAULTS = {
    "anthropic": "claude",
    "anthropic-api": "claude-api",
    "kimi": "kimi",
    "gemini": "gemini",
    "browser-use": "bu-1-0",
}


def get_fallback(model_key: str) -> str:
    """Get the fallback model from another family."""
    my_family = MODELS[model_key]["family"]
    fallback_order = {
        "anthropic": "gemini", "anthropic-api": "gemini",
        "gemini": "kimi", "kimi": "gemini",
        "browser-use": "gemini",
    }
    other_family = fallback_order.get(my_family, "gemini")
    return FAMILY_DEFAULTS[other_family]


def get_llm(model_key: str):
    """Get LLM instance by model key."""
    if model_key == "claude":
        return ChatOpenAI(
            model="claude-sonnet-4-5-20250929",
            api_key="not-needed",
            base_url="http://127.0.0.1:8001/v1",
            temperature=0.0,
        )

    elif model_key == "claude-api":
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
            temperature=1.0,
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

    elif model_key == "opus":
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            return ChatAnthropic(
                model="claude-opus-4-20250514",
                api_key=api_key,
                temperature=1.0,
                max_tokens=16384,
            )
        # Fall back to local proxy
        return ChatOpenAI(
            model="claude-opus-4-20250514",
            api_key="not-needed",
            base_url="http://127.0.0.1:8001/v1",
            temperature=1.0,
        )

    elif model_key == "gemini-2.5-pro":
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY or GEMINI_API_KEY required")
        return ChatGoogle(
            model="gemini-2.5-pro",
            api_key=api_key,
        )

    elif model_key in ("bu-1-0", "bu-2-0"):
        api_key = os.getenv("BROWSER_USE_API_KEY")
        if not api_key:
            raise ValueError("BROWSER_USE_API_KEY environment variable required")
        model_name = "bu-1-0" if model_key == "bu-1-0" else "bu-2-0"
        return ChatBrowserUse(model=model_name)

    else:
        raise ValueError(f"Unknown model: {model_key}. Available: {list(MODELS.keys())}")


# ---------------------------------------------------------------------------
# Screenshot stream: rolling buffer of timestamped frames
# ---------------------------------------------------------------------------

_frame_buffer: list[tuple[float, bytes]] = []
_frame_task: asyncio.Task | None = None
FRAME_MAX = 20
FRAME_INTERVAL = 3


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
            while len(_frame_buffer) > FRAME_MAX:
                _frame_buffer.pop(0)
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

    # --- Skills: inject_skills / read_skill ---

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

        # Inject framework interaction primitives (__FW namespace)
        try:
            fw_js_path = Path(__file__).parent / "skills" / "frameworks" / "react.js"
            if fw_js_path.exists():
                fw_js = fw_js_path.read_text()
                await browser_session._cdp_add_init_script(fw_js)
                page = await browser_session.get_current_page()
                await page.evaluate(f"() => {{ {fw_js} }}")
                injected.append("__FW")
        except Exception as e:
            print(f"[Agent] Warning: failed to inject framework helpers: {e}")

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

        mouse = await page.mouse
        await mouse.move(x, y)
        await asyncio.sleep(1.5)

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

        src_node = await browser_session.get_element_by_index(params.source)
        if not src_node:
            return ActionResult(error=f"Source element {params.source} not found")

        tgt_node = await browser_session.get_element_by_index(params.target)
        if not tgt_node:
            return ActionResult(error=f"Target element {params.target} not found")

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

        mouse = await page.mouse
        await mouse.move(sx, sy)
        await mouse.down()
        for step in range(1, 11):
            frac = step / 10
            await mouse.move(sx + (tx - sx) * frac, sy + (ty - sy) * frac)
        await mouse.up()

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

        mouse = await page.mouse
        margin = min(20, w * 0.1)
        for i in range(params.strokes):
            y_frac = (i + 1) / (params.strokes + 1)
            sx, sy = ox + margin, oy + h * y_frac
            ex, ey = ox + w - margin, oy + h * y_frac + h * 0.05

            await mouse.move(sx, sy)
            await mouse.down()
            for step in range(1, 11):
                frac = step / 10
                await mouse.move(sx + (ex - sx) * frac, sy + (ey - sy) * frac)
            await mouse.up()

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
        try:
            page = await browser_session.get_current_page()
            page_text = await page.evaluate(
                "() => document.body ? document.body.innerText.substring(0, 2000) : ''"
            )
            page_url = await page.evaluate("() => window.location.href")
        except Exception:
            page_text = ""
            page_url = ""

        completion_signals = [
            "congratulations", "all steps completed", "all 30 steps",
            "you did it", "all done", "challenge arena complete",
        ]
        text_lower = page_text.lower()
        is_complete = any(sig in text_lower for sig in completion_signals)

        if is_complete:
            return ActionResult(
                is_done=True,
                success=params.success,
                extracted_content=params.text,
                long_term_memory=f"Task completed: {params.success} - {params.text[:100]}",
            )
        else:
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


# ---------------------------------------------------------------------------
# Step timing tracking (general, not challenge-specific)
# ---------------------------------------------------------------------------

_step_timings: list[tuple[int, str, float]] = []


async def run_agent(url: str, goal: str, model_key: str, fallback_key: str,
                    headless: bool = False, max_runs: int = MAX_RUNS_DEFAULT,
                    classifier_enabled: bool = False, use_vision: bool = False):
    """Run the browser automation agent with automatic fallback."""
    global _run_logger

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

        # Initialize run logger (triggers FIFO cleanup)
        _run_logger = RunLogger(agent_name="browser-use", url=url, goal=goal,
                                max_runs=max_runs)
        print(f"[Agent] Logging to: {_run_logger.run_dir}")

        browser = None
        try:
            llm = get_llm(key)
            tools = build_tools()

            # Auto-discover skills and generate prompt section
            all_skills = load_skills()

            # When classifier is enabled, exclude page_assist (challenge-specific hook)
            # so only the generic interact hook runs
            if classifier_enabled:
                excluded = {"page_assist"}
                all_skills = [s for s in all_skills if s.name not in excluded]
                print(f"[Agent] Classifier mode: excluded challenge-specific skills: {excluded}")

            skills_prompt = generate_skills_prompt(all_skills)

            # Collect skill hooks and prompt extensions
            skill_hooks = []
            prompt_parts = []
            for s in all_skills:
                if s.hook_module and hasattr(s.hook_module, 'on_step_start'):
                    skill_hooks.append(s.hook_module.on_step_start)
                    print(f"[Agent] Loaded hook: {s.name}")
                if s.prompt_text:
                    prompt_parts.append(s.prompt_text)
                    print(f"[Agent] Loaded prompt extension: {s.name}")

            # Build combined system message from skill prompts (no hardcoded prompt)
            system_prompt = "\n\n".join(prompt_parts) if prompt_parts else ""
            if skills_prompt:
                system_prompt = system_prompt + "\n" + skills_prompt if system_prompt else skills_prompt

            # Enable classifier in interact hook if requested
            if classifier_enabled:
                for s in all_skills:
                    if s.hook_module and hasattr(s.hook_module, 'set_classifier_enabled'):
                        s.hook_module.set_classifier_enabled(True)
                        print(f"[Agent] Classifier enabled in {s.name} hook")

            # Combined on_step_start: run all skill hooks, first skip_llm wins
            async def combined_on_step_start(agent):
                for hook in skill_hooks:
                    result = await hook(agent)
                    if isinstance(result, dict) and result.get("skip_llm"):
                        agent._skip_llm = result
                        break

            browser = Browser(
                headless=headless,
                viewport={"width": 1280, "height": 800},
                disable_security=True,
                downloads_path=str(Path(__file__).parent / "downloads"),
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-background-timer-throttling",
                    "--disable-renderer-backgrounding",
                    "--disable-features=VizDisplayCompositor",
                    "--js-flags=--max-old-space-size=512",
                ],
            )

            agent = Agent(
                task=task,
                llm=llm,
                browser=browser,
                use_vision=use_vision,
                controller=tools,
                extend_system_message=system_prompt,
                initial_actions=[
                    {"inject_skills": {}},
                    {"navigate": {"url": url}},
                ],
                max_actions_per_step=5,
                max_history_items=6,
                use_judge=False,
                flash_mode=True,
                llm_timeout=120,
                save_conversation_path=str(Path(__file__).parent / "logs" / "agent-conv"),
            )

            # --- Instrument Agent internals for per-segment timing + LLM skip ---
            _orig_prepare = agent._prepare_context
            _orig_get_action = agent._get_next_action
            _orig_execute = agent._execute_actions
            _orig_post = agent._post_process

            async def timed_prepare(step_info=None):
                t = time.time()
                result = await _orig_prepare(step_info)
                ms = (time.time() - t) * 1000
                _step_timings.append((agent.state.n_steps, 'prepare_context', ms))
                print(f"  [{time.strftime('%H:%M:%S')}][Step {agent.state.n_steps}][prepare_context] {ms:.0f}ms")
                return result

            # Build synthetic action models for LLM-skip
            from pydantic import create_model as _cm
            _WaitModel = _cm('WaitModel', __base__=_ActionModelBase,
                             wait=(dict | None, None))
            _DoneModel = _cm('DoneModel', __base__=_ActionModelBase,
                             done=(dict | None, None))

            async def timed_get_action(browser_state_summary):
                # LLM-skip: if a hook signaled skip_llm, construct synthetic output
                if hasattr(agent, '_skip_llm') and agent._skip_llm:
                    skip_info = agent._skip_llm
                    agent._skip_llm = None
                    reason = skip_info.get('reason', 'hook')
                    print(f"  [{time.strftime('%H:%M:%S')}][Step {agent.state.n_steps}][llm_skip] {reason}")
                    _step_timings.append((agent.state.n_steps, 'llm_inference', 0))
                    # If hook signals challenge is done, tell agent to finish
                    if skip_info.get('done'):
                        print(f"    [agent] Challenge complete! Signaling done.")
                        agent.state.last_model_output = AgentOutput(
                            evaluation_previous_goal='Challenge completed successfully!',
                            memory='All 30 challenge steps completed. On /finish page.',
                            next_goal='Task is complete.',
                            action=[_DoneModel(done={'text': 'Challenge completed - all 30 steps done!', 'success': True})],
                        )
                        return
                    agent.state.last_model_output = AgentOutput(
                        evaluation_previous_goal='Auto-handled by skill hook',
                        memory=f"Auto-handled: {reason}. IMPORTANT: Stay on current page. NEVER navigate to other URLs. The challenge is a SPA at the root URL.",
                        next_goal='Wait for page transition. Do NOT navigate away.',
                        action=[_WaitModel(wait={'seconds': 2})],
                    )
                    return
                t = time.time()
                result = await _orig_get_action(browser_state_summary)
                ms = (time.time() - t) * 1000
                _step_timings.append((agent.state.n_steps, 'llm_inference', ms))
                print(f"  [{time.strftime('%H:%M:%S')}][Step {agent.state.n_steps}][llm_inference] {ms:.0f}ms")
                return result

            async def timed_execute():
                t = time.time()
                result = await _orig_execute()
                ms = (time.time() - t) * 1000
                _step_timings.append((agent.state.n_steps, 'execute_actions', ms))
                print(f"  [{time.strftime('%H:%M:%S')}][Step {agent.state.n_steps}][execute_actions] {ms:.0f}ms")
                return result

            async def timed_post():
                t = time.time()
                result = await _orig_post()
                ms = (time.time() - t) * 1000
                _step_timings.append((agent.state.n_steps, 'post_process', ms))
                print(f"  [{time.strftime('%H:%M:%S')}][Step {agent.state.n_steps}][post_process] {ms:.0f}ms")
                return result

            agent._prepare_context = timed_prepare
            agent._get_next_action = timed_get_action
            agent._execute_actions = timed_execute
            agent._post_process = timed_post

            async def post_step_log(agent):
                step_n = agent.state.n_steps
                step_time = time.time() - agent.step_start_time if hasattr(agent, 'step_start_time') else 0
                _step_timings.append((step_n, 'total', step_time * 1000))
                print(f"[{time.strftime('%H:%M:%S')}][Step {step_n}] total: {step_time:.1f}s")

                if _run_logger:
                    try:
                        page = await agent.browser_session.get_current_page()
                        await _run_logger.save_screenshot(page, step_n)

                        dom_events = await page.evaluate(
                            "() => window.__domChangeLog ? [...window.__domChangeLog] : []"
                        )
                        if dom_events:
                            _run_logger.save_dom_event(step_n, dom_events)
                    except Exception as e:
                        print(f"[RunLogger] Post-step capture failed: {e}")

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
            conv_dir = Path(__file__).parent / "logs" / "agent-conv"
            if conv_dir.exists():
                for f in conv_dir.glob("conversation_*.txt"):
                    f.unlink()

            print(f"[Agent] Starting with {key}...")
            _step_timings.clear()
            _frame_buffer.clear()

            global _frame_task
            _frame_task = None

            # Collect all step timings including from hooks
            all_step_timings = _step_timings

            combined_on_step_start_final = combined_on_step_start if skill_hooks else None

            t0 = time.time()

            history = await agent.run(
                max_steps=120,
                on_step_start=combined_on_step_start_final,
                on_step_end=post_step_log,
            )
            wall_time = time.time() - t0

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

            # Merge hook timings into our timing list
            for s in all_skills:
                if s.hook_module and hasattr(s.hook_module, 'get_step_timings'):
                    hook_timings = s.hook_module.get_step_timings()
                    all_step_timings = list(all_step_timings) + list(hook_timings)

            # Step timing breakdown — per-segment
            print(f"\nTiming breakdown by segment:")
            print(f"  {'Segment':<20s} {'Avg':>8s} {'Min':>8s} {'Max':>8s} {'Total':>8s} {'Count':>6s}")
            print(f"  {'-'*20} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*6}")
            for phase in ['cleanup', 'prepare_context', 'llm_inference', 'execute_actions', 'post_process', 'total']:
                times = [d for _, p, d in all_step_timings if p == phase]
                if times:
                    print(f"  {phase:<20s} {sum(times)/len(times)/1000:>7.1f}s {min(times)/1000:>7.1f}s {max(times)/1000:>7.1f}s {sum(times)/1000:>7.1f}s {len(times):>6d}")
            print(f"{'='*60}")

            # Save run summary
            if _run_logger:
                _run_logger.save_summary(history, wall_time, key, step_timings=all_step_timings)
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
        epilog=f"Models: {model_help}\n\nFallback auto-selects from the other family (kimi <-> gemini).",
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
    parser.add_argument("--max-runs", type=int, default=MAX_RUNS_DEFAULT,
                        help=f"Max runs to keep (default: {MAX_RUNS_DEFAULT}, 0 to disable)")
    parser.add_argument("--classifier", action="store_true",
                        help="Enable Tier 1 classifier (Gemini Flash) for page interaction classification")
    parser.add_argument("--vision", action="store_true",
                        help="Enable vision/screenshots (default: DOM-only for speed)")

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
        max_runs=args.max_runs,
        classifier_enabled=args.classifier,
        use_vision=args.vision,
    )


if __name__ == "__main__":
    asyncio.run(main())
