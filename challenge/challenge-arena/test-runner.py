#!/usr/bin/env python3
"""
Test runner for figure-agent-v2 against the local challenge server.

Runs the agent against individual challenge types or steps, tracks results,
and generates a report. Results are saved incrementally to a JSON file so
runs can be resumed.

Usage:
  # Test all unique challenge types (no obstacles, fastest feedback)
  python test-runner.py --all

  # Test specific types
  python test-runner.py --types click_reveal,hover_reveal,drag_drop

  # Test specific steps (with obstacles)
  python test-runner.py --steps 1,5,10 --obstacles

  # Test with a specific model
  python test-runner.py --types visible --model gemini-2.5

  # Re-run only failed types from a previous run
  python test-runner.py --retry-failed results/run_20240101_120000.json

  # Show report from a previous run
  python test-runner.py --report results/run_20240101_120000.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

SCRIPT_DIR = Path(__file__).parent
AGENT_DIR = SCRIPT_DIR.parent.parent / "agents" / "browser-use"
RESULTS_DIR = SCRIPT_DIR / "results"
SERVER_PORT = 8765
BASE_URL = f"http://127.0.0.1:{SERVER_PORT}"

# All unique challenge types in the order they appear
ALL_TYPES = [
    "visible", "hidden_dom", "click_reveal", "scroll_reveal", "delayed_reveal",
    "drag_drop", "keyboard_sequence", "memory", "hover_reveal",
    "timing", "canvas", "audio", "video", "split_parts",
    "encoded_base64", "rotating", "obfuscated",
    "multi_tab", "gesture", "sequence", "puzzle_solve", "calculated",
    "shadow_dom", "websocket", "service_worker", "mutation",
    "recursive_iframe", "conditional_reveal",
]

# Difficulty tiers for reporting
DIFFICULTY = {
    "easy": ["visible", "hidden_dom", "click_reveal", "delayed_reveal", "encoded_base64"],
    "medium": ["scroll_reveal", "hover_reveal", "split_parts", "rotating", "obfuscated", "calculated", "conditional_reveal"],
    "hard": ["drag_drop", "keyboard_sequence", "memory", "timing", "canvas", "audio", "video", "gesture", "sequence", "puzzle_solve"],
    "expert": ["multi_tab", "shadow_dom", "websocket", "service_worker", "mutation", "recursive_iframe"],
}


def api_request(path: str, method: str = "GET", data: dict | None = None) -> dict | None:
    """Make a request to the local server API."""
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
    """Check if the local server is running."""
    return api_request("/api/config") is not None


def start_server() -> subprocess.Popen | None:
    """Start the local challenge server."""
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT_DIR / "server.py"), "--port", str(SERVER_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(10):
        time.sleep(0.5)
        if check_server():
            return proc
    proc.kill()
    return None


def build_url(*, step: int | None = None, challenge_type: str | None = None,
              obstacles: bool = False, debug: bool = False, version: int = 1) -> str:
    """Build the challenge URL with query params."""
    params = []
    if challenge_type:
        params.append(f"type={challenge_type}")
    elif step:
        params.append(f"step={step}")
    else:
        params.append("step=1")

    if not obstacles:
        params.append("obstacles=0")
    if debug:
        params.append("debug=1")
    if version != 1:
        params.append(f"version={version}")

    return f"{BASE_URL}/?{'&'.join(params)}"


def parse_agent_output(output: str) -> dict:
    """Parse agent stdout for metrics."""
    result = {
        "success": False,
        "steps": 0,
        "wall_time_s": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_total": 0,
        "cost": 0.0,
        "api_calls": 0,
    }

    for line in output.split("\n"):
        line = line.strip()
        if line.startswith("Success:"):
            result["success"] = "True" in line
        elif line.startswith("Steps:"):
            m = re.search(r"(\d+)", line)
            if m:
                result["steps"] = int(m.group(1))
        elif line.startswith("Wall time:"):
            m = re.search(r"([\d.]+)s", line)
            if m:
                result["wall_time_s"] = float(m.group(1))
        elif line.startswith("Tokens:"):
            m = re.findall(r"([\d,]+)", line)
            if len(m) >= 3:
                result["tokens_in"] = int(m[0].replace(",", ""))
                result["tokens_out"] = int(m[1].replace(",", ""))
                result["tokens_total"] = int(m[2].replace(",", ""))
        elif line.startswith("Cost:"):
            m = re.search(r"\$([\d.]+)", line)
            if m:
                result["cost"] = float(m.group(1))
        elif line.startswith("API calls:"):
            m = re.search(r"(\d+)", line)
            if m:
                result["api_calls"] = int(m.group(1))

    return result


def run_agent(url: str, model: str, timeout: int = 300) -> dict:
    """Run the agent against a URL and return parsed results."""
    goal_file = AGENT_DIR / "prompts" / "browser-challenge.txt"
    # Use venv python if available, otherwise fallback to sys.executable
    venv_python = AGENT_DIR / ".venv" / "bin" / "python3"
    python_exe = str(venv_python) if venv_python.exists() else sys.executable
    cmd = [
        python_exe, str(AGENT_DIR / "agent.py"),
        "--url", url,
        "--goal-file", str(goal_file),
        "--model", model,
        "--headless",
    ]

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(AGENT_DIR),
        )
        elapsed = time.time() - t0
        result = parse_agent_output(proc.stdout)
        result["exit_code"] = proc.returncode
        result["elapsed_s"] = round(elapsed, 1)
        result["stdout_tail"] = proc.stdout[-2000:] if proc.stdout else ""
        result["stderr_tail"] = proc.stderr[-1000:] if proc.stderr else ""
        return result
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "exit_code": -1,
            "elapsed_s": timeout,
            "error": f"Timed out after {timeout}s",
            "steps": 0,
            "wall_time_s": timeout,
            "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
            "cost": 0.0, "api_calls": 0,
            "stdout_tail": "", "stderr_tail": "",
        }
    except Exception as e:
        return {
            "success": False,
            "exit_code": -2,
            "elapsed_s": time.time() - t0,
            "error": str(e),
            "steps": 0,
            "wall_time_s": 0,
            "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
            "cost": 0.0, "api_calls": 0,
            "stdout_tail": "", "stderr_tail": "",
        }


def verify_completion(step: int | None = None, challenge_type: str | None = None) -> bool:
    """Check server-side completion tracking to verify the agent actually completed."""
    status = api_request("/api/status")
    if not status or not status.get("completions"):
        return False
    completions = status["completions"]
    if step:
        return str(step) in completions
    if challenge_type:
        return any(c.get("type") == challenge_type for c in completions.values())
    return len(completions) > 0


def get_difficulty(challenge_type: str) -> str:
    """Get difficulty tier for a challenge type."""
    for tier, types in DIFFICULTY.items():
        if challenge_type in types:
            return tier
    return "unknown"


def print_report(results: dict):
    """Print a formatted report from results."""
    tests = results.get("tests", [])
    if not tests:
        print("No test results to report.")
        return

    run_id = results.get("run_id", "unknown")
    model = results.get("model", "unknown")
    total = len(tests)
    passed = sum(1 for t in tests if t.get("agent_success") or t.get("server_verified"))
    failed = total - passed
    total_cost = sum(t.get("cost", 0) for t in tests)
    total_time = sum(t.get("elapsed_s", 0) for t in tests)

    print()
    print("=" * 72)
    print(f"  TEST REPORT: {run_id}")
    print(f"  Model: {model}  |  Date: {results.get('started_at', 'N/A')}")
    print("=" * 72)
    print()

    # Summary
    pct = (passed / total * 100) if total > 0 else 0
    print(f"  PASS: {passed}/{total} ({pct:.0f}%)  |  FAIL: {failed}  |  Cost: ${total_cost:.4f}  |  Time: {total_time:.0f}s")
    print()

    # By difficulty
    print("  By Difficulty:")
    for tier in ["easy", "medium", "hard", "expert"]:
        tier_tests = [t for t in tests if get_difficulty(t.get("type", "")) == tier]
        if not tier_tests:
            continue
        tier_pass = sum(1 for t in tier_tests if t.get("agent_success") or t.get("server_verified"))
        print(f"    {tier:8s}: {tier_pass}/{len(tier_tests)}")
    print()

    # Detail table
    print(f"  {'Type':<24s} {'Result':>8s} {'Steps':>6s} {'Time':>8s} {'Cost':>8s} {'Tokens':>10s}")
    print(f"  {'-'*24} {'-'*8} {'-'*6} {'-'*8} {'-'*8} {'-'*10}")

    for t in sorted(tests, key=lambda x: (0 if x.get("agent_success") or x.get("server_verified") else 1, x.get("type", ""))):
        name = t.get("type") or f"step_{t.get('step', '?')}"
        ok = t.get("agent_success") or t.get("server_verified")
        status = "PASS" if ok else "FAIL"
        steps = str(t.get("steps", 0))
        elapsed = f"{t.get('elapsed_s', 0):.0f}s"
        cost = f"${t.get('cost', 0):.4f}"
        tokens = f"{t.get('tokens_total', 0):,}"
        print(f"  {name:<24s} {status:>8s} {steps:>6s} {elapsed:>8s} {cost:>8s} {tokens:>10s}")

    # Failures detail
    failures = [t for t in tests if not (t.get("agent_success") or t.get("server_verified"))]
    if failures:
        print()
        print("  FAILURES:")
        for t in failures:
            name = t.get("type") or f"step_{t.get('step', '?')}"
            error = t.get("error", "")
            stderr = t.get("stderr_tail", "")[:200]
            print(f"    {name}: {error or 'Agent reported failure'}")
            if stderr:
                print(f"      stderr: {stderr}")

    print()
    print("=" * 72)


def save_results(results: dict, path: Path):
    """Save results to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Strip verbose stdout/stderr from saved file to keep it clean
    clean = dict(results)
    clean["tests"] = []
    for t in results.get("tests", []):
        ct = dict(t)
        ct.pop("stdout_tail", None)
        ct.pop("stderr_tail", None)
        clean["tests"].append(ct)
    with open(path, "w") as f:
        json.dump(clean, f, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Test runner for figure-agent-v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    target = parser.add_mutually_exclusive_group()
    target.add_argument("--all", action="store_true", help="Test all unique challenge types")
    target.add_argument("--types", "-t", help="Comma-separated challenge types to test")
    target.add_argument("--steps", "-s", help="Comma-separated step numbers to test")
    target.add_argument("--retry-failed", metavar="FILE", help="Re-run failed tests from a previous result file")
    target.add_argument("--report", metavar="FILE", help="Show report from a previous result file")

    parser.add_argument("--model", "-m", default="gemini", help="Model to use (default: gemini)")
    parser.add_argument("--obstacles", action="store_true", help="Enable obstacle layer")
    parser.add_argument("--timeout", type=int, default=300, help="Per-test timeout in seconds (default: 300)")
    parser.add_argument("--output", "-o", help="Output file path (default: auto-generated)")

    args = parser.parse_args()

    # Report mode: just print and exit
    if args.report:
        with open(args.report) as f:
            print_report(json.load(f))
        return

    # Determine targets
    targets: list[dict] = []

    if args.retry_failed:
        with open(args.retry_failed) as f:
            prev = json.load(f)
        for t in prev.get("tests", []):
            if not (t.get("agent_success") or t.get("server_verified")):
                if t.get("type"):
                    targets.append({"type": t["type"]})
                elif t.get("step"):
                    targets.append({"step": t["step"]})
        if not targets:
            print("No failed tests to retry.")
            return
        print(f"Retrying {len(targets)} failed tests from {args.retry_failed}")

    elif args.all:
        targets = [{"type": t} for t in ALL_TYPES]

    elif args.types:
        for t in args.types.split(","):
            t = t.strip()
            if t not in ALL_TYPES:
                print(f"Unknown type: {t}")
                print(f"Available: {', '.join(ALL_TYPES)}")
                return
            targets.append({"type": t})

    elif args.steps:
        for s in args.steps.split(","):
            s = int(s.strip())
            if s < 1 or s > 30:
                print(f"Step must be 1-30, got: {s}")
                return
            targets.append({"step": s})

    else:
        parser.print_help()
        return

    # Check/start server
    server_proc = None
    if not check_server():
        print(f"Starting local server on port {SERVER_PORT}...")
        server_proc = start_server()
        if not server_proc:
            print("Failed to start server!")
            return
        print("Server started.")

    # Check agent exists
    if not (AGENT_DIR / "agent.py").exists():
        print(f"Agent not found at {AGENT_DIR / 'agent.py'}")
        return

    # Setup results
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    results = {
        "run_id": run_id,
        "model": args.model,
        "obstacles": args.obstacles,
        "timeout": args.timeout,
        "started_at": datetime.now().isoformat(),
        "tests": [],
    }

    output_path = Path(args.output) if args.output else RESULTS_DIR / f"{run_id}.json"

    # Reset server completion state
    api_request("/api/reset", method="POST", data={"run_id": run_id})

    print()
    print("=" * 60)
    print(f"  Figure Agent v2 — Test Runner")
    print(f"  Run ID:    {run_id}")
    print(f"  Model:     {args.model}")
    print(f"  Targets:   {len(targets)}")
    print(f"  Obstacles: {'ON' if args.obstacles else 'OFF'}")
    print(f"  Timeout:   {args.timeout}s per test")
    print(f"  Output:    {output_path}")
    print("=" * 60)
    print()

    try:
        for i, target in enumerate(targets, 1):
            challenge_type = target.get("type")
            step = target.get("step")
            label = challenge_type or f"step_{step}"

            print(f"[{i}/{len(targets)}] Testing: {label}")

            # Reset server state for this test
            api_request("/api/reset", method="POST", data={"run_id": run_id})

            # Build URL
            url = build_url(
                step=step,
                challenge_type=challenge_type,
                obstacles=args.obstacles,
            )
            print(f"  URL: {url}")

            # Run agent
            t0 = time.time()
            result = run_agent(url, model=args.model, timeout=args.timeout)
            elapsed = time.time() - t0

            # Check server-side verification
            server_ok = verify_completion(step=step, challenge_type=challenge_type)

            # Build test record
            test_record = {
                "type": challenge_type,
                "step": step,
                "url": url,
                "agent_success": result.get("success", False),
                "server_verified": server_ok,
                "difficulty": get_difficulty(challenge_type) if challenge_type else "N/A",
                **{k: v for k, v in result.items() if k not in ("stdout_tail", "stderr_tail", "success")},
            }
            results["tests"].append(test_record)

            # Status
            ok = result.get("success", False) or server_ok
            status = "PASS" if ok else "FAIL"
            print(f"  Result: {status} | Steps: {result.get('steps', 0)} | Time: {elapsed:.0f}s | Cost: ${result.get('cost', 0):.4f}")
            if not ok and result.get("error"):
                print(f"  Error: {result['error']}")
            print()

            # Save incrementally
            save_results(results, output_path)

    except KeyboardInterrupt:
        print("\n\nInterrupted! Saving partial results...")
        results["interrupted"] = True

    finally:
        results["finished_at"] = datetime.now().isoformat()
        save_results(results, output_path)

        if server_proc:
            server_proc.kill()

    # Print report
    print_report(results)
    print(f"Results saved to: {output_path}")


if __name__ == "__main__":
    main()
