# Pattern Agent

Fast, DOM-first browser agent for the Figure Browser Navigation Challenge.

Design goals:
- Deterministic where possible (DOM parsing, safe interaction primitives).
- Online pattern learning: after solving something once, reuse the same recipe when it appears again.
- Minimal LLM dependence. The agent can run without any API keys for the local arena.

## Setup

```bash
cd figure/agents/pattern-agent

# Avoid sandboxed cache permissions
export UV_CACHE_DIR=/tmp/uv-cache

uv sync
```

Playwright browsers:
- Default is to launch installed Chrome via `--channel chrome` (no Playwright browser download).
- If you want Playwright's bundled Chromium, run `uv run playwright install chromium`.

## Run

Local arena:
```bash
# Terminal 1: start the arena
python3 ../../challenge/challenge-arena/server.py --port 8765

# Terminal 2: run the agent
uv run python -m pattern_agent --url http://127.0.0.1:8765/?step=1
```

Live site:
```bash
uv run python -m pattern_agent --url https://serene-frangipane-7fd25b.netlify.app/
```

## Environment

Copy `.env.example` to `.env` and fill keys only if you enable LLM mode.
