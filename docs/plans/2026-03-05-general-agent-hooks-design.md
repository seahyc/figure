# General Agent: Hook System + Planner Improvements

## Date: 2026-03-05
## Branch: general-agent

## Goal

30/30 challenge in <5 min with no challenge-specific code. Same agent generalizes to 10 other curriculum tasks (flights, hotels, products, recipes, news, academic, jobs, courses, maps, math).

## Architecture: Option D (Hybrid)

Smarter planner prompt handles task-specific reasoning. Lightweight post-action hooks handle universal mechanical behaviors (scanning, popups, stuck detection). Hooks feed information to the planner — they don't take actions.

## Loop Change

```
BEFORE: observe → plan → execute → reward → log
AFTER:  observe → plan → execute → run_hooks → reward → log
                                      ↓
                              hook results injected
                              into next observe step
```

## Hook System

### hooks.py (NEW)

Three hooks run after every executor action. Pure information gatherers — enrich the next observation, don't take actions.

**PatternScanner**
- Task config defines `scan_patterns`: `[{name, regex, source}]`
- Sources: `visible_text`, `shadow_dom`, `hidden_elements`, `data_attributes`, `all`
- Returns: `[{name: "code", value: "VBUD5F", source: "visible_text"}]`
- When matches found AND visible input + submit button exist, adds `suggested_action`

**PopupDismisser**
- Runs existing `dismiss_popups` skill JS
- Returns: `{dismissed: N, labels: [...]}`

**StuckDetector**
- Tracks last N observation fingerprints (hash of visible_text + url)
- If same state for `stuck_threshold` consecutive steps, flags it
- Returns: `{stuck: bool, same_state_for: N}`

### Hook Results Format

Appended to planner input:

```
HOOKS:
  patterns_found: [{name: "code", value: "VBUD5F"}]
  suggested_action: {action_type: "fill_form", params: {value: "VBUD5F", submit_button: "Submit Code"}}
  popups_dismissed: 1
  stuck: false
```

## Planner Prompt (Full Rewrite)

```
You are a browser automation agent. You observe a web page, decide one action, and execute it.

RULES:
1. Pick the simplest action that makes progress toward the task.
2. Use structural hints: hasCanvas->draw, hasDraggables->drag, hasShadowRoots->content may be hidden.
3. For multi-field forms: fill each field separately using selector/placeholder/name to target, then submit ONLY after ALL fields filled.
4. NEVER click decoy navigation buttons (Next, Continue, Proceed, Move On, Go Forward, Keep Going, Advance, Click Here, Next Step, Next Page, Next Section) unless the TASK specifically requires it.
5. If HOOKS.stuck is true, your last N actions had no effect. Try a DIFFERENT action type or target.
6. If HOOKS.patterns_found has matches, consider whether they're relevant to your task. If a found pattern is the answer you need, use it.
7. After clicking a button that should reveal content, CHECK the observation for changes before clicking again.
8. For date pickers, dropdowns, and complex widgets: use evaluate_js to inspect structure first.
9. When extracting information, use evaluate_js to read specific DOM content rather than relying only on visible_text.
10. Signal done with success=true ONLY when you have completed the task objective, not just when you've taken actions.

ACTIONS: click, type, fill_form, hover, scroll, drag, draw, press_keys, wait, evaluate_js, done
Output JSON: {"thought": "...", "action_type": "...", "params": {...}}
```

## Observer Changes

- Add observation diffing: track what changed between steps (new buttons, URL change, new text)
- Include input current values in observation (already partially done)
- Surface hook results in observation text

## Task Configuration

Extend curriculum.yaml with hook config:

```yaml
- id: challenge-30step
  url: https://serene-frangipane-7fd25b.netlify.app/
  description: "Complete all 30 steps..."
  reward_type: progress
  reward_pattern: "Step\\s+(\\d+)"
  scan_patterns:
    - name: code
      regex: "[A-HJ-NP-Z2-9]{6}"
      source: all
  hooks:
    dismiss_popups: true
    stuck_threshold: 3

- id: flight-search
  url: https://www.google.com/travel/flights
  description: "Find cheapest round-trip PEK->SIN..."
  reward_type: llm_judge
  scan_patterns:
    - name: price
      regex: "\\$[\\d,]+"
      source: visible_text
  hooks:
    dismiss_popups: true
    stuck_threshold: 5
```

## Rate Limit Handling

- Exponential backoff on 429: 5s, 10s, 20s (not counted as agent steps)
- Add --model flag for switching between gemini-2.0-flash, kimi-k2
- Investigate why paid tier key hits free_tier quotas

## File Changes

| File | Change |
|---|---|
| hooks.py | NEW: PatternScanner, PopupDismisser, StuckDetector |
| agent.py | Add hook runner between execute and observe, pass results to planner |
| planner.py | Rewrite system prompt, accept hook_results, add model switching |
| observer.py | Add observation diffing, surface hook results |
| curriculum.yaml | Add scan_patterns and hooks config per task |

## Performance Target

Challenge: ~2-3 actions per step x 30 steps = 60-90 total steps.
At ~1.5s per planner call = 90-135s planning + execution = ~2-3 min.
Target: <5 min with margin.

## Passing Criteria

1. 30/30 challenge in <5 min (no challenge-specific code)
2. Login test still passes
3. At least 2 curriculum tasks produce reasonable results (e.g., figure.ai/careers, BBC news)
4. Trajectory logging unchanged — every step logged for DPO
