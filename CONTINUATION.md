# Continuation Guide

## Current State (2026-02-07)

### What's Working
- **Browser-use agent** at `agents/browser-use/` completes 28/30 challenge steps
- **Claude via local proxy** (`claude-code-api/`) routes through Max subscription ($0 API cost)
- **Gemini Flash** works for cheap benchmarking ($0.03/run)
- **Skills system**: pre_solve, dismiss_popups, compute, shadow_dom all functional
- **Test infrastructure**: test-runner.py and run-agent.sh paths fixed

### What's Not Working Yet
- **2 challenge types fail**: `drag_drop` and `gesture` — agent wastes ~10 steps each
- **Claude proxy structured output**: The proxy doesn't support OpenAI function calling natively. A tool-schema-in-system-prompt workaround was added but needs more testing
- **Agent efficiency**: 200 steps for 28 challenges is too many; target is ~60 (2 per challenge)

---

## Setup on New Machine

### 1. Prerequisites
```bash
# Node.js (for Claude CLI)
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs

# Claude CLI
npm install -g @anthropic-ai/claude-code

# Python 3.11+ with uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Playwright browsers
cd agents/browser-use && uv run playwright install --with-deps chromium
```

### 2. Authentication
```bash
# Claude CLI login (for Max subscription proxy)
claude login

# Create .env in agents/browser-use/
cat > agents/browser-use/.env << 'EOF'
ANTHROPIC_API_KEY=sk-ant-api03-...  # Only needed for claude-api model
GOOGLE_API_KEY=AIzaSy...            # For gemini model
EOF
```

### 3. Start the Claude Proxy
```bash
cd claude-code-api
python3 -m venv .venv && source .venv/bin/activate
pip install -e . greenlet requests
uvicorn claude_code_api.main:app --host 127.0.0.1 --port 8000 &

# Verify
curl http://127.0.0.1:8000/health
```

### 4. Start Challenge Server
```bash
cd challenge/challenge-arena
python3 server.py --port 8765
```

### 5. Run the Agent
```bash
cd agents/browser-use

# Default: Claude via Max subscription proxy (free)
uv run python agent.py \
  --url "http://127.0.0.1:8765/?type=visible&obstacles=0" \
  --goal-file prompts/browser-challenge.txt \
  --model claude --headless

# Gemini (cheap, good for benchmarking)
uv run python agent.py \
  --url "http://127.0.0.1:8765/?type=visible&obstacles=0" \
  --goal-file prompts/browser-challenge.txt \
  --model gemini --headless

# Claude direct API (costs ~$11/full run, best quality)
uv run python agent.py \
  --url "http://127.0.0.1:8765/?type=visible&obstacles=0" \
  --goal-file prompts/browser-challenge.txt \
  --model claude-api --headless
```

---

## Available Models

| Key | Model | Cost/run | Vision | Notes |
|-----|-------|----------|--------|-------|
| `claude` | Sonnet 4.5 via proxy | **$0** (Max sub) | Yes | Default. Requires proxy running on :8000 |
| `claude-api` | Sonnet 4.5 direct API | ~$11 | Yes | Best quality, expensive |
| `gemini` | Gemini 2.0 Flash | ~$0.03 | Yes | Cheap benchmarking |
| `gemini-2.5` | Gemini 2.5 Flash | ~$0.05 | Yes | Better reasoning |
| `kimi` | Kimi K2.5 | ~$0.10 | Yes | Needs KIMI_API_KEY |

---

## Key Architecture

```
browser-use agent (agent.py)
├── LLM (Claude/Gemini/Kimi via langchain)
├── Skills (auto-discovered JS, injected into browser)
│   ├── pre_solve     — auto-click buttons, find codes
│   ├── dismiss_popups — handle modals, overlays
│   ├── compute       — math eval, string ops
│   ├── shadow_dom    — traverse shadow roots
│   ├── clean_dom     — reduce DOM noise
│   ├── scroll_to     — scroll utilities
│   ├── search_dom    — find elements by text
│   └── observe_changes — DOM mutation tracking
├── pre_step_cleanup  — runs before each LLM step:
│   dismiss_popups → clean_dom → pre_solve → scroll-to-top
├── Custom tools: hover, drag, draw, send_keys, diagnose_page
└── Run logging → runs/<timestamp>/
    ├── screenshots/  (per-step + 3s stream)
    ├── llm/          (full request/response JSON)
    └── dom/          (mutation events)
```

### Claude Proxy Architecture
```
browser-use → ChatOpenAI("http://127.0.0.1:8000/v1")
  → claude-code-api (FastAPI)
    → claude CLI subprocess (-p --input-format stream-json)
      → Max subscription tokens (not API billing)
```

The proxy was modified to support **multimodal input** (screenshots):
- OpenAI `image_url` format → Anthropic `image` format conversion
- Uses `--input-format stream-json` to pipe images via stdin
- Tool schemas are injected into the system prompt for structured output

---

## Priority Work Items

### 1. Fix Claude Proxy Structured Output (HIGH)
The proxy passes tool schemas in the system prompt, but Claude sometimes uses wrong field names (e.g., `js_code` instead of `code`). Options:
- Add response post-processing to fix common field name errors
- Or implement proper OpenAI function calling → Anthropic tool use translation
- Or test more and see if the current approach works reliably enough

### 2. Improve Hard Challenge Types (HIGH)
- **drag_drop**: Agent can't find draggable elements by index. Need better drag implementation or JS-side drag handler
- **gesture**: Complex mouse gesture patterns. May need a gesture-recognition skill

### 3. Reduce Step Count (MEDIUM)
- Target: 2 steps per challenge (read + submit), currently averaging 6-7
- pre_solve already helps, but the LLM still over-thinks simple challenges
- Consider: more aggressive pre_solve that submits codes automatically when found

### 4. Benchmark All Models (LOW)
```bash
cd agents/browser-use
uv run python benchmark.py --models claude,gemini --all --no-obstacles
```

### 5. Computer-Use Specific Models (RESEARCH)
- **Gemini**: `gemini-2.5-computer-use-preview-10-2025` — dedicated CU model, might be better
- **Claude**: Same model, but enable `computer_20251124` tool via API beta header
- **OpenAI CUA**: `computer-use-preview` — different API (Responses), needs adapter

---

## Test Commands Quick Reference
```bash
# Single challenge type (no obstacles)
uv run python agent.py --url "http://127.0.0.1:8765/?type=visible&obstacles=0" \
  --goal-file prompts/browser-challenge.txt --model gemini --headless

# Full 30-step run
cd challenge/challenge-arena
python3 test-runner.py --all --no-obstacles

# Hard types individually
for type in drag_drop gesture shadow_dom sequence calculated split_parts; do
  uv run python agent.py --url "http://127.0.0.1:8765/?type=$type&obstacles=0" \
    --goal-file prompts/browser-challenge.txt --model gemini --headless
done
```
