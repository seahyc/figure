#!/usr/bin/env python3
"""
Benchmark the browser-use agent across challenge types and models.

Usage:
  # Benchmark all models against all types (no obstacles)
  uv run python benchmark.py --models claude,gemini,kimi --all

  # Benchmark specific types
  uv run python benchmark.py --models claude,gemini --types shadow_dom,sequence,calculated

  # Single model, all types
  uv run python benchmark.py --models claude --all

  # Compare results from previous runs
  uv run python benchmark.py --report runs/benchmark-*.json
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

# Import agent components
from agent import MODELS, get_llm, get_fallback, run_agent as run_agent_fn

SCRIPT_DIR = Path(__file__).parent
RUNS_DIR = SCRIPT_DIR.parent.parent / "runs"
SERVER_PORT = 8765
BASE_URL = f"http://127.0.0.1:{SERVER_PORT}"

ALL_TYPES = [
    "visible", "hidden_dom", "click_reveal", "scroll_reveal", "delayed_reveal",
    "drag_drop", "keyboard_sequence", "memory", "hover_reveal",
    "timing", "canvas", "audio", "video", "split_parts",
    "encoded_base64", "rotating", "obfuscated",
    "multi_tab", "gesture", "sequence", "puzzle_solve", "calculated",
    "shadow_dom", "websocket", "service_worker", "mutation",
    "recursive_iframe", "conditional_reveal",
]

DIFFICULTY = {
    "easy": ["visible", "hidden_dom", "click_reveal", "delayed_reveal", "encoded_base64"],
    "medium": ["scroll_reveal", "hover_reveal", "split_parts", "rotating", "obfuscated", "calculated", "conditional_reveal"],
    "hard": ["drag_drop", "keyboard_sequence", "memory", "timing", "canvas", "audio", "video", "gesture", "sequence", "puzzle_solve"],
    "expert": ["multi_tab", "shadow_dom", "websocket", "service_worker", "mutation", "recursive_iframe"],
}


def api_request(path: str, method: str = "GET", data: dict | None = None) -> dict | None:
    url = f"{BASE_URL}{path}"
    try:
        if method == "POST":
            req = Request(url, data=json.dumps(data or {}).encode(), method="POST")
            req.add_header("Content-Type", "application/json")
        else:
            req = Request(url)
        with urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except (URLError, json.JSONDecodeError, OSError):
        return None


def check_server() -> bool:
    return api_request("/api/config") is not None


def verify_completion(challenge_type: str) -> bool:
    status = api_request("/api/status")
    if not status or not status.get("completions"):
        return False
    return any(c.get("type") == challenge_type for c in status["completions"].values())


def get_difficulty(challenge_type: str) -> str:
    for tier, types in DIFFICULTY.items():
        if challenge_type in types:
            return tier
    return "unknown"


async def benchmark_single(model_key: str, challenge_type: str, obstacles: bool = False,
                           timeout: int = 120) -> dict:
    """Run the agent against a single challenge type and return metrics."""
    url = f"{BASE_URL}/?type={challenge_type}"
    if not obstacles:
        url += "&obstacles=0"

    goal_file = SCRIPT_DIR / "prompts" / "browser-challenge.txt"
    goal = goal_file.read_text().strip()
    # For single-type benchmarking, simplify the goal
    goal = f"Complete this browser challenge step. Find the 6-character code and submit it."

    fallback_key = get_fallback(model_key)

    # Reset server state
    api_request("/api/reset", method="POST", data={"run_id": f"bench-{model_key}-{challenge_type}"})

    t0 = time.time()
    result = {
        "model": model_key,
        "type": challenge_type,
        "difficulty": get_difficulty(challenge_type),
        "obstacles": obstacles,
        "success": False,
        "wall_time_s": 0,
        "steps": 0,
        "tokens_total": 0,
        "cost": 0.0,
        "error": None,
    }

    try:
        history = await asyncio.wait_for(
            run_agent_fn(
                url=url,
                goal=goal,
                model_key=model_key,
                fallback_key=fallback_key,
                headless=True,
            ),
            timeout=timeout,
        )
        elapsed = time.time() - t0
        result["wall_time_s"] = round(elapsed, 1)
        result["steps"] = history.number_of_steps()
        result["success"] = history.is_successful() or verify_completion(challenge_type)
        if history.usage:
            result["tokens_total"] = history.usage.total_tokens
            result["cost"] = round(history.usage.total_cost, 4)
    except asyncio.TimeoutError:
        result["wall_time_s"] = timeout
        result["error"] = f"Timed out after {timeout}s"
    except Exception as e:
        result["wall_time_s"] = round(time.time() - t0, 1)
        result["error"] = str(e)

    # Verify server-side
    if not result["success"]:
        result["success"] = verify_completion(challenge_type)

    return result


def print_report(results_files: list[str]):
    """Print comparison report from one or more benchmark result files."""
    all_results = []
    for f in results_files:
        with open(f) as fh:
            data = json.load(fh)
            all_results.append(data)

    if not all_results:
        print("No results to report.")
        return

    # Aggregate by model
    model_stats = {}
    for run in all_results:
        model = run.get("model", "unknown")
        tests = run.get("tests", [])
        if model not in model_stats:
            model_stats[model] = {"pass": 0, "fail": 0, "total_time": 0, "total_steps": 0,
                                  "total_cost": 0, "count": 0, "tests": []}
        for t in tests:
            model_stats[model]["count"] += 1
            if t.get("success"):
                model_stats[model]["pass"] += 1
            else:
                model_stats[model]["fail"] += 1
            model_stats[model]["total_time"] += t.get("wall_time_s", 0)
            model_stats[model]["total_steps"] += t.get("steps", 0)
            model_stats[model]["total_cost"] += t.get("cost", 0)
            model_stats[model]["tests"].append(t)

    print()
    print("=" * 72)
    print("  BENCHMARK COMPARISON")
    print("=" * 72)
    print()
    print(f"  {'Model':<15s} | {'Pass':>6s} | {'Fail':>4s} | {'Avg Time':>8s} | {'Avg Steps':>9s} | {'Cost':>8s}")
    print(f"  {'-'*15} | {'-'*6} | {'-'*4} | {'-'*8} | {'-'*9} | {'-'*8}")

    for model, stats in sorted(model_stats.items()):
        n = stats["count"]
        p = stats["pass"]
        f = stats["fail"]
        avg_t = stats["total_time"] / n if n else 0
        avg_s = stats["total_steps"] / n if n else 0
        cost = stats["total_cost"]
        print(f"  {model:<15s} | {p:>3d}/{n:<2d} | {f:>4d} | {avg_t:>6.1f}s | {avg_s:>9.1f} | ${cost:>6.2f}")

    print()

    # Detail: which types each model fails
    for model, stats in sorted(model_stats.items()):
        failures = [t["type"] for t in stats["tests"] if not t.get("success")]
        if failures:
            print(f"  {model} failures: {', '.join(failures)}")

    print()
    print("=" * 72)


async def main():
    parser = argparse.ArgumentParser(
        description="Benchmark browser-use agent across models and challenge types",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    target = parser.add_mutually_exclusive_group()
    target.add_argument("--all", action="store_true", help="Test all challenge types")
    target.add_argument("--types", "-t", help="Comma-separated challenge types")
    target.add_argument("--report", nargs="+", metavar="FILE", help="Show report from result files")

    parser.add_argument("--models", "-m", default="claude",
                        help="Comma-separated model keys (default: claude)")
    parser.add_argument("--obstacles", action="store_true", help="Enable obstacles")
    parser.add_argument("--timeout", type=int, default=120, help="Per-type timeout (default: 120s)")
    parser.add_argument("--output", "-o", help="Output file path")

    args = parser.parse_args()

    if args.report:
        print_report(args.report)
        return

    if not check_server():
        print(f"Server not running on port {SERVER_PORT}. Start it with:")
        print(f"  cd figure/challenge/challenge-arena && python3 server.py --port {SERVER_PORT}")
        sys.exit(1)

    # Determine targets
    if args.all:
        types = ALL_TYPES
    elif args.types:
        types = [t.strip() for t in args.types.split(",")]
        for t in types:
            if t not in ALL_TYPES:
                print(f"Unknown type: {t}")
                sys.exit(1)
    else:
        parser.print_help()
        return

    models = [m.strip() for m in args.models.split(",")]
    for m in models:
        if m not in MODELS:
            print(f"Unknown model: {m}. Available: {list(MODELS.keys())}")
            sys.exit(1)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_path = Path(args.output) if args.output else RUNS_DIR / f"benchmark-{ts}.json"

    print()
    print("=" * 60)
    print(f"  Benchmark: {len(models)} models x {len(types)} types")
    print(f"  Models:    {', '.join(models)}")
    print(f"  Types:     {len(types)} challenge types")
    print(f"  Obstacles: {'ON' if args.obstacles else 'OFF'}")
    print(f"  Timeout:   {args.timeout}s per test")
    print("=" * 60)
    print()

    all_run_results = []

    for model_key in models:
        print(f"\n{'='*40}")
        print(f"  Benchmarking: {model_key}")
        print(f"{'='*40}\n")

        run_result = {
            "model": model_key,
            "started_at": datetime.now().isoformat(),
            "obstacles": args.obstacles,
            "tests": [],
        }

        for i, challenge_type in enumerate(types, 1):
            print(f"  [{i}/{len(types)}] {model_key} vs {challenge_type}...", end=" ", flush=True)

            result = await benchmark_single(
                model_key=model_key,
                challenge_type=challenge_type,
                obstacles=args.obstacles,
                timeout=args.timeout,
            )
            run_result["tests"].append(result)

            status = "PASS" if result["success"] else "FAIL"
            print(f"{status} ({result['wall_time_s']:.0f}s, {result['steps']} steps, ${result['cost']:.3f})")

        run_result["finished_at"] = datetime.now().isoformat()
        all_run_results.append(run_result)

        # Save incrementally
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(all_run_results, f, indent=2)

    # Print final report
    # Save each model as separate file for report compatibility
    for run in all_run_results:
        model = run["model"]
        model_path = output_path.parent / f"benchmark-{ts}-{model}.json"
        with open(model_path, "w") as f:
            json.dump(run, f, indent=2)

    print_report([str(output_path.parent / f"benchmark-{ts}-{m}.json") for m in models])
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
