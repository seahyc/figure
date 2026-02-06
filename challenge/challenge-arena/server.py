#!/usr/bin/env python3
"""
Simple HTTP server for local challenge.
Serves static files and provides API endpoints for the test harness.

Usage:
  python server.py              # Start on port 8765
  python server.py --port 9000  # Custom port

API:
  GET  /api/config    - Challenge config (types, steps, URLs)
  GET  /api/status    - Current completion state
  POST /api/complete  - Report step completion (called by client JS)
  POST /api/reset     - Reset completion state for new test run
"""

import argparse
import json
import os
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

CHALLENGE_DIR = Path(__file__).parent

# In-memory completion tracking (reset per test run)
_completions: dict[str, dict] = {}  # run_id → {step, type, code, timestamp}
_current_run: str = ""


class ChallengeHandler(SimpleHTTPRequestHandler):
    """Handler that serves SPA and API endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CHALLENGE_DIR), **kwargs)

    def do_GET(self):
        path_no_qs = self.path.split("?")[0]

        # API: challenge config
        if path_no_qs == "/api/config":
            self._json_response(self._build_config())
            return

        # API: current completion status
        if path_no_qs == "/api/status":
            self._json_response({
                "run_id": _current_run,
                "completions": _completions.get(_current_run, {}),
            })
            return

        # SPA fallback: serve index.html for paths without extensions
        if not os.path.splitext(path_no_qs)[1] and path_no_qs != "/":
            self.path = "/index.html"

        super().do_GET()

    def do_POST(self):
        global _current_run
        path_no_qs = self.path.split("?")[0]

        # API: report step completion
        if path_no_qs == "/api/complete":
            body = self._read_body()
            if body is None:
                return
            step = body.get("step")
            if step is None:
                self._json_response({"error": "missing step"}, status=400)
                return
            run_id = _current_run or "default"
            if run_id not in _completions:
                _completions[run_id] = {}
            _completions[run_id][str(step)] = {
                "step": step,
                "type": body.get("type", ""),
                "code": body.get("code", ""),
                "timestamp": time.time(),
            }
            self._json_response({"ok": True, "run_id": run_id, "step": step})
            return

        # API: reset for new test run
        if path_no_qs == "/api/reset":
            body = self._read_body()
            run_id = (body or {}).get("run_id", f"run_{int(time.time())}")
            _current_run = run_id
            _completions[run_id] = {}
            self._json_response({"ok": True, "run_id": run_id})
            return

        self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _read_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            self._json_response({"error": "invalid JSON"}, status=400)
            return None

    def _json_response(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _build_config(self):
        pools = [
            {"range": [1, 5], "types": ["visible", "hidden_dom", "click_reveal", "scroll_reveal", "delayed_reveal"]},
            {"range": [6, 10], "types": ["drag_drop", "keyboard_sequence", "memory", "hover_reveal", "click_reveal"]},
            {"range": [11, 15], "types": ["timing", "canvas", "audio", "video", "split_parts", "encoded_base64", "rotating", "obfuscated"]},
            {"range": [16, 20], "types": ["multi_tab", "gesture", "sequence", "puzzle_solve", "calculated"]},
            {"range": [21, 30], "types": ["shadow_dom", "websocket", "service_worker", "mutation", "recursive_iframe", "conditional_reveal", "multi_tab", "sequence", "calculated"]},
        ]
        steps = {}
        for step in range(1, 31):
            for pool in pools:
                if pool["range"][0] <= step <= pool["range"][1]:
                    idx = (step - pool["range"][0]) % len(pool["types"])
                    steps[step] = pool["types"][idx]
                    break
        unique_types = sorted(set(steps.values()))
        return {
            "total_steps": 30,
            "steps": steps,
            "unique_types": unique_types,
            "type_count": len(unique_types),
            "urls": {
                "full_run": "/?step=1",
                "by_step": {str(s): f"/?step={s}" for s in range(1, 31)},
                "by_type": {t: f"/?type={t}" for t in unique_types},
                "no_obstacles": "/?step=1&obstacles=0",
                "debug": "/?step=1&debug=1",
            },
        }

    def log_message(self, format, *args):
        # Quieter logging — only show non-static requests
        req = str(args[0]) if args else ""
        if any(ext in req for ext in [".js", ".css", ".ico", ".png"]):
            return
        super().log_message(format, *args)


def main():
    parser = argparse.ArgumentParser(description="Local challenge server")
    parser.add_argument("--port", "-p", type=int, default=8765, help="Port (default: 8765)")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), ChallengeHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Local challenge server running at {url}")
    print(f"  Full run:       {url}/?step=1")
    print(f"  No obstacles:   {url}/?step=1&obstacles=0")
    print(f"  Debug mode:     {url}/?step=1&debug=1")
    print(f"  Specific type:  {url}/?type=hover_reveal")
    print(f"  API config:     {url}/api/config")
    print(f"  API status:     {url}/api/status")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
