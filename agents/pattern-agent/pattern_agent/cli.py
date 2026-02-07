import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from pattern_agent.runner import run_agent


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Pattern Agent (DOM-first, online skill learning)")
    parser.add_argument("--url", required=True, help="Start URL (local arena or live site)")
    parser.add_argument("--headless", action="store_true", help="Run browser headless (default: headed)")
    parser.add_argument("--channel", default="chrome", help="Playwright channel (default: chrome)")
    parser.add_argument("--max-steps", type=int, default=200, help="Max interaction steps (default: 200)")
    parser.add_argument("--llm", choices=["none", "openai"], default="none", help="LLM planner (default: none)")
    parser.add_argument("--memory-file", default="", help="Optional JSON file to persist learned patterns across runs")
    args = parser.parse_args()

    memory_file = Path(args.memory_file) if args.memory_file else None

    if args.llm == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            raise SystemExit("OPENAI_API_KEY is required for --llm openai")

    asyncio.run(
        run_agent(
            url=args.url,
            headless=args.headless,
            channel=args.channel,
            max_steps=args.max_steps,
            llm_mode=args.llm,
            memory_file=memory_file,
        )
    )

