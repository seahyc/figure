"""
Batch trajectory collector: run the agent on curriculum tasks.

Usage:
    python training/collect.py --curriculum training/curriculum.yaml --runs 3
    python training/collect.py --tasks news-extraction,job-market --runs 2
    python training/collect.py --task-file tasks/challenge.yaml --runs 1
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Add parent dir for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml


async def run_single_task(task_def: dict, run_idx: int, headless: bool = True,
                          max_steps: int = 60) -> dict:
    """Run the agent on a single task and return result summary."""
    from agent import run_agent

    task_id = task_def.get("id", "unknown")
    url = task_def.get("url", "")
    goal = task_def.get("description", "")
    reward_type = task_def.get("reward_type", "binary")
    reward_pattern = task_def.get("reward_pattern", r"Step\s+(\d+)")
    scan_patterns = task_def.get("scan_patterns")
    hooks_cfg = task_def.get("hooks", {})
    dismiss_popups = hooks_cfg.get("dismiss_popups", True)
    stuck_threshold = hooks_cfg.get("stuck_threshold", 5)

    print(f"\n{'='*60}")
    print(f"Task: {task_id} (run {run_idx})")
    print(f"URL:  {url}")
    print(f"{'='*60}")

    t0 = time.time()
    try:
        trajectory = await run_agent(
            url=url,
            goal=goal,
            headless=headless,
            max_steps=max_steps,
            reward_type=reward_type,
            reward_pattern=reward_pattern,
            task_id=task_id,
            save_trajectory=True,
            scan_patterns=scan_patterns,
            dismiss_popups=dismiss_popups,
            stuck_threshold=stuck_threshold,
        )
        return {
            "task_id": task_id,
            "run": run_idx,
            "success": trajectory.success,
            "reward": trajectory.total_reward,
            "steps": len(trajectory.steps),
            "time_s": round(time.time() - t0, 1),
        }
    except Exception as e:
        print(f"  [collect] Task {task_id} run {run_idx} failed: {e}")
        return {
            "task_id": task_id,
            "run": run_idx,
            "success": False,
            "reward": 0.0,
            "steps": 0,
            "time_s": round(time.time() - t0, 1),
            "error": str(e),
        }


async def main():
    parser = argparse.ArgumentParser(description="Batch trajectory collector")
    parser.add_argument("--curriculum", "-c", default="training/curriculum.yaml",
                        help="Path to curriculum YAML (default: training/curriculum.yaml)")
    parser.add_argument("--tasks", "-t", help="Comma-separated task IDs to run (default: all)")
    parser.add_argument("--task-file", help="Single task YAML file to run")
    parser.add_argument("--runs", "-r", type=int, default=1,
                        help="Number of runs per task (default: 1)")
    parser.add_argument("--max-steps", type=int, default=60,
                        help="Max steps per task (default: 60)")
    parser.add_argument("--headless", action="store_true", default=True,
                        help="Run headless (default: true)")
    args = parser.parse_args()

    # Load tasks
    if args.task_file:
        with open(args.task_file) as f:
            task_defs = yaml.safe_load(f)
        if isinstance(task_defs, dict):
            task_defs = [task_defs]
    else:
        with open(args.curriculum) as f:
            task_defs = yaml.safe_load(f)

    # Filter tasks if specified
    if args.tasks:
        selected = set(args.tasks.split(","))
        task_defs = [t for t in task_defs if t.get("id") in selected]
        if not task_defs:
            print(f"No tasks matched: {args.tasks}")
            return

    print(f"Collecting trajectories for {len(task_defs)} tasks x {args.runs} runs = {len(task_defs) * args.runs} episodes")
    print(f"Max steps per task: {args.max_steps}")

    results = []
    for task_def in task_defs:
        for run_idx in range(1, args.runs + 1):
            result = await run_single_task(task_def, run_idx, headless=args.headless,
                                           max_steps=args.max_steps)
            results.append(result)

            # Brief pause between runs to avoid rate limits
            if run_idx < args.runs or task_def != task_defs[-1]:
                await asyncio.sleep(2)

    # Summary
    print(f"\n{'='*60}")
    print(f"COLLECTION COMPLETE")
    print(f"{'='*60}")
    print(f"{'Task':<25} {'Run':>4} {'Success':>8} {'Reward':>7} {'Steps':>6} {'Time':>6}")
    print(f"{'-'*25} {'-'*4} {'-'*8} {'-'*7} {'-'*6} {'-'*6}")
    for r in results:
        status = "YES" if r["success"] else "NO"
        print(f"{r['task_id']:<25} {r['run']:>4} {status:>8} {r['reward']:>7.1f} {r['steps']:>6} {r['time_s']:>5.0f}s")

    total_success = sum(1 for r in results if r["success"])
    total_reward = sum(r["reward"] for r in results)
    print(f"\nTotal: {total_success}/{len(results)} successful, {total_reward:.1f} total reward")


if __name__ == "__main__":
    asyncio.run(main())
