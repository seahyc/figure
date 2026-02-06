# Challenge Arena — Browser Navigation Challenge (Local Reproduction)

Local reproduction of the [live Browser Navigation Challenge](https://serene-frangipane-7fd25b.netlify.app/) for agent testing and evaluation.

## Quick Start

```bash
# Start the server
python3 server.py --port 8765

# Open in browser
open http://127.0.0.1:8765
```

## What This Is

A 30-step browser automation challenge. Each step presents a **challenge** (find a hidden 6-character code) and an **obstacle layer** (popups, modals, fake buttons). The agent must:

1. Dismiss obstacles (cookie banners, popups, blocking MCQ modal)
2. Solve the challenge to reveal a 6-character code
3. Enter the code in the submission input and click Submit
4. Repeat for all 30 steps

## 28 Challenge Types

| Difficulty | Types |
|-----------|-------|
| Easy | `visible`, `hidden_dom`, `click_reveal`, `delayed_reveal`, `memory`, `audio`, `recursive_iframe`, `conditional_reveal` |
| Medium | `scroll_reveal`, `hover_reveal`, `multi_tab`, `rotating`, `timing`, `mutation`, `encoded_base64`, `canvas`, `gesture`, `video`, `service_worker`, `websocket` |
| Hard | `drag_drop`, `keyboard_sequence`, `split_parts`, `obfuscated`, `puzzle_solve`, `calculated`, `sequence`, `shadow_dom` |

## URL Parameters

| Param | Example | Description |
|-------|---------|-------------|
| `step` | `?step=5` | Jump to step N (1-30) |
| `type` | `?type=hover_reveal` | Test a specific challenge type |
| `obstacles` | `?obstacles=0` | Disable obstacle layer |
| `debug` | `?debug=1` | Show debug panel with all codes |
| `version` | `?version=2` | Change type mapping |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/config` | GET | Step-to-type mapping, all URLs |
| `/api/status` | GET | Current session status |
| `/api/complete` | POST | Log a step completion `{step, type, code}` |
| `/api/reset` | POST | Reset session |

## Running the Agent

```bash
# Single challenge type (no obstacles, clean signal)
./run-agent.sh --type visible --no-obstacles

# Specific step with obstacles
./run-agent.sh --step 10

# Full 30-step run
./run-agent.sh

# Batch test all types
python3 test-runner.py --all --no-obstacles
```

## File Structure

```
challenge-arena/
  index.html          # Single-page app shell
  server.py           # Python HTTP server with API endpoints
  run-agent.sh        # Launch figure-agent-v2 against this server
  test-runner.py      # Batch test runner
  js/
    engine.js          # Session, codes, validation logic
    challenges.js      # All 28 challenge type implementations
    obstacles.js       # Popup/modal/distraction layer
    app.js             # Routing, step lifecycle, code submission
  css/
    style.css          # Styling (Tailwind CDN also loaded)
  results/             # Test run output (JSON)
```

## Key Behaviors Matching Live Site

- **Validation off-by-one**: `validateCode(step, code)` checks `codes[step+1]`, and the displayed code IS `codes[step+1]` — this is intentional, not a bug
- **Step 30 bypass**: `codes[31]` doesn't exist, so step 30 always passes validation regardless of input
- **MCQ reshuffle on selection**: Blocking modal radio options reshuffle every time you select one (selected stays checked but moves position)
- **Variable code input distance**: Random 50-350px gap between challenge area and code submission input
- **Obstacle z-index layering**: Obstacles z=10000-20000, challenges z=10005, code entry z=10002

## Differences from Live Site

| Aspect | Live Site | Local |
|--------|-----------|-------|
| Routing | Path-based (`/step5`) | Query-param (`?step=5`) |
| Framework | React + Radix UI | Vanilla JS |
| UI Library | Radix radio buttons | Native HTML radio |
| Session | `sessionStorage` with XOR encoding | Same encoding logic |
| Obstacles | React components | Vanilla JS DOM |
