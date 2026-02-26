"""
Figure Agent - General-Purpose Browser Automation Agent

Observe → Plan → Execute loop using:
- Observer: extracts structured page observations (ARIA, features, text)
- Planner: Gemini Flash LLM plans next action from observation + task
- Executor: Playwright-based action execution (click, type, hover, drag, etc.)
- Reward + Trajectory: structured logging for offline RL (DPO training)

No challenge-specific code. The challenge is just another task described in
natural language via --goal or --task-file.
"""

import os
import re
import json
import time
import shutil
import asyncio
import importlib.util
import yaml
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType
from dotenv import load_dotenv
from browser_use import Browser
from pydantic import BaseModel, Field

from observer import observe, Observation
from planner import plan
from executor import execute, ACTION_HANDLERS
from trajectory import Trajectory, TrajectoryStep
from reward import create_reward, RewardFunction, ProgressReward

load_dotenv()

# ---------------------------------------------------------------------------
# Redirect all temp files into project dir
# ---------------------------------------------------------------------------
_PROJECT_TMP = Path(__file__).parent / "tmp"
_PROJECT_TMP.mkdir(exist_ok=True)
os.environ["TMPDIR"] = str(_PROJECT_TMP)

# ---------------------------------------------------------------------------
# Run logging
# ---------------------------------------------------------------------------

RUNS_DIR = Path(__file__).parent.parent.parent / "runs"
MAX_RUNS_DEFAULT = 20
MAX_SCREENSHOT_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB


def cleanup_old_runs(max_runs: int):
    """FIFO cleanup: remove oldest run dirs beyond limit."""
    if max_runs <= 0 or not RUNS_DIR.exists():
        return
    dirs = sorted([d for d in RUNS_DIR.iterdir() if d.is_dir()], key=lambda d: d.name)
    while len(dirs) > max_runs:
        oldest = dirs.pop(0)
        print(f"[cleanup] Removing old run: {oldest.name}")
        shutil.rmtree(oldest, ignore_errors=True)
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

        (self.run_dir / "screenshots").mkdir(exist_ok=True)
        (self.run_dir / "llm").mkdir(exist_ok=True)
        (self.run_dir / "dom").mkdir(exist_ok=True)

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

    def save_summary(self, wall_time: float, num_steps: int, success: bool,
                     step_timings: list | None = None):
        lines = [
            f"# Run Summary: {self.run_id}",
            "",
            f"**Wall time:** {wall_time:.1f}s ({wall_time/60:.1f}m)",
            f"**Steps:** {num_steps}",
            f"**Success:** {success}",
            "",
        ]
        if step_timings:
            lines.extend(["## Per-Step Timing", ""])
            lines.append("| Step | Plan | Execute | Total |")
            lines.append("|------|------|---------|-------|")
            for st in step_timings:
                lines.append(f"| {st['step']} | {st['plan_ms']/1000:.1f}s | {st['exec_ms']/1000:.1f}s | {(st['plan_ms']+st['exec_ms'])/1000:.1f}s |")
            lines.append("")
        lines.extend([
            "## Files",
            f"- Screenshots: {len(list((self.run_dir / 'screenshots').glob('*.png')))}",
            f"- LLM calls: {len(list((self.run_dir / 'llm').glob('*.json')))}",
        ])
        (self.run_dir / "summary.md").write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Skills system: auto-discovered JS skills from skills/ directory
# ---------------------------------------------------------------------------

SKILLS_DIR = Path(__file__).parent / "skills"


@dataclass
class SkillInfo:
    name: str
    skill_type: str
    description: str
    body: str
    script: str | None
    hook_module: ModuleType | None = field(default=None, repr=False)
    prompt_text: str | None = None


def parse_frontmatter(text: str) -> tuple[dict, str]:
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
    """Discover and load JS skills from the skills directory."""
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


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

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


def get_llm_for_fallback(model_key: str):
    """Get a browser-use LLM instance for Tier 3 fallback."""
    from browser_use.llm.openai.chat import ChatOpenAI
    from browser_use.llm.google import ChatGoogle
    from browser_use.llm.anthropic.chat import ChatAnthropic

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
            raise ValueError("ANTHROPIC_API_KEY required")
        return ChatAnthropic(
            model="claude-sonnet-4-5-20250929",
            api_key=api_key,
            temperature=0.0,
            max_tokens=8192,
        )
    elif model_key in ("gemini", "gemini-2.5"):
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY required")
        model_name = "gemini-2.0-flash" if model_key == "gemini" else "gemini-2.5-flash"
        return ChatGoogle(model=model_name, api_key=api_key)
    else:
        raise ValueError(f"Unknown model: {model_key}")


# ---------------------------------------------------------------------------
# Browser setup
# ---------------------------------------------------------------------------

def create_browser(headless: bool = False) -> Browser:
    return Browser(
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


async def inject_js_skills(browser_session, skills: list[SkillInfo]):
    """Inject JS skills and framework helpers into the browser."""
    injected = []

    # Inject framework interaction primitives (__FW namespace)
    fw_js_path = Path(__file__).parent / "skills" / "frameworks" / "react.js"
    if fw_js_path.exists():
        try:
            fw_js = fw_js_path.read_text()
            await browser_session._cdp_add_init_script(fw_js)
            page = await browser_session.get_current_page()
            await page.evaluate(f"() => {{ {fw_js} }}")
            injected.append("__FW")
        except Exception as e:
            print(f"[Agent] Warning: failed to inject framework helpers: {e}")

    # Inject skill scripts
    for s in skills:
        if s.script:
            js = f"window.__skills = window.__skills || {{}}; window.__skills.{s.name} = {s.script};"
            try:
                await browser_session._cdp_add_init_script(js)
                page = await browser_session.get_current_page()
                await page.evaluate(f"() => {{ {js} }}")
                injected.append(s.name)
            except Exception as e:
                print(f"[Agent] Warning: failed to inject {s.name}: {e}")

    return injected


# ---------------------------------------------------------------------------
# Main observe-plan-execute loop
# ---------------------------------------------------------------------------

async def run_agent(
    url: str,
    goal: str,
    headless: bool = False,
    max_runs: int = MAX_RUNS_DEFAULT,
    max_steps: int = 120,
    reward_type: str = "binary",
    reward_pattern: str = r"Step\s+(\d+)",
    task_id: str = "",
    save_trajectory: bool = True,
):
    """Run the general-purpose browser agent.

    Args:
        url: Starting URL
        goal: Natural language task description
        headless: Run headless browser
        max_runs: Max run dirs to keep
        max_steps: Maximum steps before stopping
        reward_type: "binary", "progress", or "llm_judge"
        reward_pattern: Regex for progress reward
        task_id: Task identifier for trajectory logging
        save_trajectory: Whether to save trajectory JSONL
    """
    print(f"\n{'='*60}")
    print(f"Figure Agent — General-Purpose Browser Automation")
    print(f"{'='*60}")
    print(f"URL:  {url}")
    print(f"Goal: {goal[:100]}{'...' if len(goal) > 100 else ''}")
    print(f"{'='*60}\n")

    # Initialize logging
    run_logger = RunLogger(agent_name="browser-use", url=url, goal=goal, max_runs=max_runs)
    print(f"[Agent] Logging to: {run_logger.run_dir}")

    # Initialize reward function
    reward_fn = create_reward(
        reward_type,
        pattern=reward_pattern,
        task_description=goal,
    )

    # Initialize trajectory
    trajectory = Trajectory(
        task_id=task_id or url.split("/")[-1][:20],
        task_description=goal,
        url=url,
    )

    # Load JS skills
    skills = load_skills()
    print(f"[Agent] Loaded {len(skills)} skills: {[s.name for s in skills]}")

    browser = None
    try:
        browser = create_browser(headless=headless)

        # Start browser and get page
        await browser.start()
        page = await browser.get_current_page()

        # Inject JS skills
        injected = await inject_js_skills(browser, skills)
        print(f"[Agent] Injected JS: {injected}")

        # Navigate to URL
        await browser.navigate_to(url)
        await asyncio.sleep(1.0)
        page = await browser.get_current_page()

        # ── Observe → Plan → Execute Loop ──────────────────────────────
        history: list[dict] = []  # recent (action_desc, result) for planner context
        step_timings: list[dict] = []
        success = False

        t0 = time.time()

        for step_num in range(1, max_steps + 1):
            step_start = time.time()

            # 1. OBSERVE
            try:
                obs = await observe(page)
                obs_text = obs.to_prompt()
            except Exception as e:
                print(f"  [Step {step_num}] Observe error: {e}")
                obs_text = f"url: {url}\nerror: {e}"

            # 2. PLAN
            plan_start = time.time()
            try:
                action = await plan(obs_text, goal, history[-5:])
            except Exception as e:
                print(f"  [Step {step_num}] Plan error: {e}")
                action = {"thought": f"Plan failed: {e}", "action_type": "wait", "params": {"seconds": 2}}
            plan_ms = (time.time() - plan_start) * 1000

            action_type = action.get("action_type", "wait")
            action_params = action.get("params", {})
            thought = action.get("thought", "")

            print(f"  [{time.strftime('%H:%M:%S')}][Step {step_num}] {action_type} | {thought[:60]}")

            # 3. EXECUTE
            exec_start = time.time()
            try:
                result = await execute(page, action_type, action_params)
            except Exception as e:
                result = {"ok": False, "detail": f"execution error: {e}"}
            exec_ms = (time.time() - exec_start) * 1000

            print(f"    → {result.get('detail', '')[:80]} ({plan_ms:.0f}ms plan, {exec_ms:.0f}ms exec)")

            # 4. REWARD
            reward_val = 0.0
            try:
                reward_val = await reward_fn.compute(page, action_type, result)
            except Exception as e:
                print(f"    [reward] Error: {e}")

            if reward_val > 0:
                print(f"    ★ Reward: {reward_val}")

            # 5. LOG
            step_timing = {"step": step_num, "plan_ms": plan_ms, "exec_ms": exec_ms}
            step_timings.append(step_timing)

            # Update history for planner context
            action_desc = f"{action_type}({json.dumps(action_params, default=str)[:80]})"
            history.append({"action": action_desc, "result": result})

            # Log trajectory step
            traj_step = TrajectoryStep(
                step_num=step_num,
                timestamp=time.time(),
                observation=obs_text,
                action_type=action_type,
                action_params=action_params,
                thought=thought,
                result=result,
                reward=reward_val,
                plan_time_ms=plan_ms,
                exec_time_ms=exec_ms,
            )
            trajectory.add_step(traj_step)

            # Save screenshot
            try:
                await run_logger.save_screenshot(page, step_num)
            except Exception:
                pass

            # Log planner call
            run_logger.save_llm_call(step_num, {"observation": obs_text[:300], "task": goal[:200]},
                                     {"action": action, "result_detail": result.get("detail", "")[:200]})

            # 6. CHECK TERMINATION
            if result.get("done"):
                success = result.get("success", False)
                print(f"\n  [Agent] Task complete (success={success}): {result.get('detail', '')}")
                break

            # Check if progress reward has hit max (all steps done)
            if isinstance(reward_fn, ProgressReward) and reward_fn.current_progress >= reward_fn.max_value:
                print(f"\n  [Agent] Progress reward reached max ({reward_fn.current_progress}/{reward_fn.max_value})")
                success = True
                break

            # Refresh page reference (might have navigated)
            try:
                page = await browser.get_current_page()
            except Exception:
                pass

            # Brief pause between steps
            await asyncio.sleep(0.3)

        wall_time = time.time() - t0

        # ── Summary ────────────────────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"EXECUTION COMPLETE")
        print(f"{'='*60}")
        print(f"Steps:     {len(trajectory.steps)}")
        print(f"Wall time: {wall_time:.1f}s ({wall_time/60:.1f}m)")
        print(f"Reward:    {trajectory.total_reward}")
        print(f"Success:   {success}")

        # Timing breakdown
        if step_timings:
            plan_times = [s["plan_ms"] for s in step_timings]
            exec_times = [s["exec_ms"] for s in step_timings]
            print(f"\nTiming:")
            print(f"  Plan:    avg={sum(plan_times)/len(plan_times):.0f}ms, max={max(plan_times):.0f}ms")
            print(f"  Execute: avg={sum(exec_times)/len(exec_times):.0f}ms, max={max(exec_times):.0f}ms")
        print(f"{'='*60}")

        # Save trajectory
        trajectory.finish(success=success)
        if save_trajectory:
            traj_dir = run_logger.run_dir / "trajectories"
            traj_dir.mkdir(exist_ok=True)
            traj_path = traj_dir / f"{trajectory.task_id}.jsonl"
            trajectory.save_jsonl(traj_path)
            print(f"[Agent] Trajectory saved: {traj_path}")

        # Save run summary
        run_logger.save_summary(wall_time, len(trajectory.steps), success, step_timings)
        print(f"[Agent] Run logged to: {run_logger.run_dir}")

        return trajectory

    except Exception as e:
        print(f"\n[Agent] Fatal error: {e}")
        import traceback
        traceback.print_exc()
        raise

    finally:
        if browser:
            try:
                print("[Agent] Closing browser...")
                await browser.stop()
                print("[Agent] Browser closed cleanly")
            except Exception as cleanup_err:
                print(f"[Agent] Browser cleanup warning: {cleanup_err}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Figure Agent — General-Purpose Browser Automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url", required=True, help="Starting URL")

    goal_group = parser.add_mutually_exclusive_group(required=False)
    goal_group.add_argument("--goal", help="Goal to accomplish (natural language)")
    goal_group.add_argument("--goal-file", help="Path to text file containing the goal")

    parser.add_argument("--task-file", help="Path to YAML task definition file")
    parser.add_argument("--headless", action="store_true", help="Run headless browser")
    parser.add_argument("--max-runs", type=int, default=MAX_RUNS_DEFAULT,
                        help=f"Max runs to keep (default: {MAX_RUNS_DEFAULT})")
    parser.add_argument("--max-steps", type=int, default=120,
                        help="Max agent steps (default: 120)")
    parser.add_argument("--reward", choices=["binary", "progress", "llm_judge"], default="binary",
                        help="Reward function type (default: binary)")
    parser.add_argument("--reward-pattern", default=r"Step\s+(\d+)",
                        help="Regex pattern for progress reward")
    parser.add_argument("--no-trajectory", action="store_true",
                        help="Disable trajectory logging")

    args = parser.parse_args()

    # Resolve goal
    goal = args.goal or ""
    if args.goal_file:
        with open(args.goal_file) as f:
            goal = f.read().strip()

    task_id = ""
    url = args.url
    reward_type = args.reward
    reward_pattern = args.reward_pattern

    # Load task from YAML if specified
    if args.task_file:
        with open(args.task_file) as f:
            task_def = yaml.safe_load(f)
        if isinstance(task_def, list):
            task_def = task_def[0]  # use first task
        goal = goal or task_def.get("description", "")
        url = task_def.get("url", url)
        task_id = task_def.get("id", "")
        reward_type = task_def.get("reward_type", reward_type)
        reward_pattern = task_def.get("reward_pattern", reward_pattern)

    if not goal:
        parser.error("Either --goal, --goal-file, or --task-file with description is required")

    await run_agent(
        url=url,
        goal=goal,
        headless=args.headless,
        max_runs=args.max_runs,
        max_steps=args.max_steps,
        reward_type=reward_type,
        reward_pattern=reward_pattern,
        task_id=task_id,
        save_trajectory=not args.no_trajectory,
    )


if __name__ == "__main__":
    asyncio.run(main())
