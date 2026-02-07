#!/usr/bin/env python3
"""
Batch test runner for pattern-agent against the local challenge server.

Examples:
  python3 test-runner-pattern-agent.py --all --no-obstacles
  python3 test-runner-pattern-agent.py --types visible,shadow_dom --no-obstacles
  python3 test-runner-pattern-agent.py --steps 1,10,20
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).parent
AGENT_DIR = SCRIPT_DIR.parent.parent / "agents" / "pattern-agent"
RESULTS_DIR = SCRIPT_DIR / "results"
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


def start_server() -> subprocess.Popen | None:
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


def build_url(*, step: int | None = None, challenge_type: str | None = None, obstacles: bool = False) -> str:
    params = []
    if challenge_type:
        params.append(f"type={challenge_type}")
    elif step:
        params.append(f"step={step}")
    else:
        params.append("step=1")
    if not obstacles:
        params.append("obstacles=0")
    return f"{BASE_URL}/?{'&'.join(params)}"


def verify_completion(*, step: int | None = None, challenge_type: str | None = None) -> bool:
    status = api_request("/api/status")
    if not status or not status.get("completions"):
        return False
    completions = status["completions"]
    if step:
        return str(step) in completions
    if challenge_type:
        return any(c.get("type") == challenge_type for c in completions.values())
    return len(completions) > 0


def run_agent(url: str, *, headless: bool, timeout_s: int = 120) -> dict:
    env = os.environ.copy()
    env.setdefault("UV_CACHE_DIR", "/tmp/uv-cache")

    cmd = ["uv", "run", "python", "-m", "pattern_agent", "--url", url]
    if headless:
        cmd.append("--headless")

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(AGENT_DIR),
            env=env,
        )
        return {
            "exit_code": proc.returncode,
            "elapsed_s": round(time.time() - t0, 1),
            "stdout_tail": (proc.stdout or "")[-2000:],
            "stderr_tail": (proc.stderr or "")[-1000:],
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "elapsed_s": timeout_s, "stdout_tail": "", "stderr_tail": "timeout"}


def main() -> None:
    parser = argparse.ArgumentParser(description="pattern-agent test runner")
    parser.add_argument("--all", action="store_true", help="Test all unique challenge types")
    parser.add_argument("--types", "-t", help="Comma-separated challenge types")
    parser.add_argument("--steps", "-s", help="Comma-separated step numbers (with obstacles ON by default)")
    parser.add_argument("--no-obstacles", action="store_true", help="Disable obstacle layer")
    parser.add_argument("--headless", action="store_true", help="Run agent headless")
    args = parser.parse_args()

    if not check_server():
        proc = start_server()
        if not proc:
            raise SystemExit("Failed to start local server")

    targets: list[dict] = []
    if args.all:
        targets = [{"type": t} for t in ALL_TYPES]
    elif args.types:
        targets = [{"type": t.strip()} for t in args.types.split(",") if t.strip()]
    elif args.steps:
        targets = [{"step": int(s)} for s in args.steps.split(",") if s.strip()]
    else:
        targets = [{"step": 1}]

    obstacles = not args.no_obstacles

    RESULTS_DIR.mkdir(exist_ok=True)
    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"{run_id}_pattern-agent.json"

    results = {"run_id": run_id, "started_at": datetime.now().isoformat(), "tests": []}

    # Reset server tracking
    api_request("/api/reset", method="POST", data={"run_id": run_id})

    for t in targets:
        step = t.get("step")
        ctype = t.get("type")
        label = ctype or f"step_{step}"
        url = build_url(step=step, challenge_type=ctype, obstacles=obstacles)
        print(f"[test] {label} -> {url}")

        agent_res = run_agent(url, headless=args.headless)
        server_ok = verify_completion(step=step, challenge_type=ctype)

        rec = {
            "label": label,
            "url": url,
            "step": step,
            "type": ctype,
            "server_verified": server_ok,
            **agent_res,
        }
        results["tests"].append(rec)
        out_path.write_text(json.dumps(results, indent=2))

    passed = sum(1 for r in results["tests"] if r["server_verified"])
    total = len(results["tests"])
    print(f"\nPassed: {passed}/{total}")
    print(f"Results: {out_path}")


if __name__ == "__main__":
    main()
