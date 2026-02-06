# Figure - Browser Agent Challenge

Multi-agent testing framework for browser automation challenges.

## Structure

```
figure/
├── agents/                 # Agent implementations
│   └── browser-use/        # Python agent using browser-use framework
├── challenge/              # Challenge arena and test infrastructure
│   ├── challenge-arena/    # Hosted challenge UI
│   └── docs/               # Challenge documentation
└── runs/                   # Test run outputs (gitignored)
    └── <timestamp>-<agent>/
        ├── screenshots/
        ├── llm/
        ├── dom/
        └── summary.md
```

## Running the Agent

```bash
cd figure/agents/browser-use
uv run python agent.py --url https://serene-frangipane-7fd25b.netlify.app/
```

## Challenge Types

28 challenge types with varying difficulty. Key categories:
- **Visible**: Code displayed directly on page
- **Scroll/Hidden**: Requires scrolling or interaction to reveal
- **Complex**: Shadow DOM, keyboard sequences, timing, canvas

See `challenge/AGENT-EVALUATION.md` for full analysis.

## Adding New Agents

Create a new folder under `agents/` with:
1. Entry script (e.g., `agent.py`)
2. Required dependencies
3. Optional: `skills/` folder for injected JS

## Logging

Runs output to `runs/<timestamp>-<agent>/`:
- `screenshots/` - Screenshot after each step
- `llm/` - Full LLM request/response JSON
- `dom/` - DOM event stream
- `config.json` - Run configuration
- `summary.md` - Run results

## Key Rules

1. **NEVER click decoy buttons** - Only "Submit Code" advances steps
2. Each step has a unique 6-character alphanumeric code
3. Target: Complete all 30 steps in <5 minutes
