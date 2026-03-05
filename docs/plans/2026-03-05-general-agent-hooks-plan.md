# General Agent: Hook System + Planner Improvements — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add post-action hooks (pattern scanning, popup dismissal, stuck detection) and improve the planner prompt so the general agent can complete the 30-step challenge in <5 min while generalizing to 10 other web tasks.

**Architecture:** Three hooks run after every executor action, feeding information (not taking actions) back to the planner. Planner prompt rewritten with generic web interaction heuristics. Task config extended with scan patterns and hook settings.

**Tech Stack:** Python 3.12, browser-use, Gemini 2.0 Flash (google-genai), Playwright

---

### Task 1: Create hooks.py — PatternScanner

**Files:**
- Create: `agents/browser-use/hooks.py`

**Step 1: Write hooks.py with PatternScanner**

```python
"""
Post-action hooks: run after every executor action, feed information to the planner.
Hooks are pure observers — they don't take actions, they enrich the next observation.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field


@dataclass
class HookResults:
    """Collected results from all hooks for one step."""
    patterns_found: list[dict] = field(default_factory=list)
    suggested_action: dict | None = None
    popups_dismissed: int = 0
    stuck: bool = False
    same_state_for: int = 0

    def to_prompt(self) -> str:
        """Format hook results for planner input."""
        lines = ["HOOKS:"]
        if self.patterns_found:
            lines.append(f"  patterns_found: {json.dumps(self.patterns_found)}")
        if self.suggested_action:
            lines.append(f"  suggested_action: {json.dumps(self.suggested_action)}")
        if self.popups_dismissed > 0:
            lines.append(f"  popups_dismissed: {self.popups_dismissed}")
        if self.stuck:
            lines.append(f"  stuck: true (same page state for {self.same_state_for} steps)")
        if not self.patterns_found and not self.stuck and self.popups_dismissed == 0:
            lines.append("  (no signals)")
        return "\n".join(lines)


# JS that scans for patterns across visible text, shadow DOM, hidden elements, data attributes
_PATTERN_SCAN_JS = r"""(patterns) => {
    var results = [];
    for (var p = 0; p < patterns.length; p++) {
        var pat = patterns[p];
        var re = new RegExp(pat.regex, 'g');
        var sources = pat.source === 'all'
            ? ['visible_text', 'shadow_dom', 'hidden_elements', 'data_attributes']
            : [pat.source];

        for (var s = 0; s < sources.length; s++) {
            var src = sources[s];
            var text = '';

            if (src === 'visible_text') {
                text = document.body ? document.body.innerText : '';
            } else if (src === 'shadow_dom') {
                document.querySelectorAll('*').forEach(function(el) {
                    if (el.shadowRoot) text += ' ' + (el.shadowRoot.textContent || '');
                });
            } else if (src === 'hidden_elements') {
                document.querySelectorAll('div, span, p').forEach(function(el) {
                    var cs = getComputedStyle(el);
                    if (cs.display === 'none' || cs.visibility === 'hidden' ||
                        cs.opacity === '0' || el.offsetHeight === 0) {
                        text += ' ' + el.textContent;
                    }
                });
            } else if (src === 'data_attributes') {
                document.querySelectorAll('*').forEach(function(el) {
                    for (var j = 0; j < el.attributes.length; j++) {
                        var attr = el.attributes[j];
                        if (attr.name.indexOf('data-') === 0) text += ' ' + attr.value;
                    }
                });
            }

            var match;
            while ((match = re.exec(text)) !== null) {
                results.push({name: pat.name, value: match[0], source: src});
            }
            re.lastIndex = 0;
        }
    }
    // Deduplicate by value
    var seen = {};
    return results.filter(function(r) {
        if (seen[r.value]) return false;
        seen[r.value] = true;
        return true;
    });
}"""


async def run_pattern_scanner(page, scan_patterns: list[dict]) -> list[dict]:
    """Scan page for configured patterns. Returns list of {name, value, source}."""
    if not scan_patterns:
        return []
    try:
        raw = await page.evaluate(_PATTERN_SCAN_JS, scan_patterns)
        if isinstance(raw, str):
            return json.loads(raw)
        return raw or []
    except Exception:
        return []


async def _check_submit_context(page) -> dict | None:
    """Check if there's a visible input + submit button for auto-suggest."""
    try:
        ctx = await page.evaluate(r"""() => {
            var input = null;
            var inputs = document.querySelectorAll('input[type="text"], input:not([type]), textarea');
            for (var i = 0; i < inputs.length; i++) {
                if (inputs[i].offsetWidth > 0) { input = inputs[i]; break; }
            }
            if (!input) return null;
            var btns = document.querySelectorAll('button');
            for (var i = 0; i < btns.length; i++) {
                var t = btns[i].textContent.trim();
                if (t === 'Submit Code' || t === 'Submit' || t === 'Go') {
                    return JSON.stringify({input_placeholder: input.placeholder || '', submit_text: t});
                }
            }
            return null;
        }""")
        if ctx:
            return json.loads(ctx) if isinstance(ctx, str) else ctx
        return None
    except Exception:
        return None


# ── Popup Dismisser ──────────────────────────────────────────────────────────

_DISMISS_JS = r"""() => {
    var dismissed = 0;
    var labels = [];
    var keywords = ['dismiss', 'close', 'decline', 'reject', 'accept',
                    'no thanks', 'not now', 'maybe later', 'got it', 'ok',
                    'acknowledge', 'i understand', 'confirm', 'agree',
                    'accept all', 'accept cookies', 'allow all'];

    // Click cookie/consent buttons
    document.querySelectorAll('button, [role="button"], a').forEach(function(el) {
        if (el.offsetWidth === 0 || el.offsetHeight === 0) return;
        var text = el.textContent.trim().toLowerCase();
        if (text.length > 50) return;
        for (var i = 0; i < keywords.length; i++) {
            if (text === keywords[i] || text.indexOf(keywords[i]) !== -1) {
                try { el.click(); dismissed++; labels.push(text); } catch(e) {}
                break;
            }
        }
    });

    // Hide overlays with high z-index
    document.querySelectorAll('div').forEach(function(el) {
        var s = getComputedStyle(el);
        if ((s.position === 'fixed' || s.position === 'absolute') &&
            parseInt(s.zIndex) >= 1000 && el.offsetHeight > 200) {
            el.style.display = 'none';
            dismissed++;
        }
    });

    return JSON.stringify({dismissed: dismissed, labels: labels.slice(0, 5)});
}"""


async def run_popup_dismisser(page) -> dict:
    """Dismiss cookie banners, consent popups, overlays. Returns {dismissed, labels}."""
    try:
        raw = await page.evaluate(_DISMISS_JS)
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {"dismissed": 0, "labels": []}


# ── Stuck Detector ───────────────────────────────────────────────────────────

class StuckDetector:
    """Tracks observation fingerprints to detect when the agent is stuck."""

    def __init__(self, threshold: int = 5):
        self.threshold = threshold
        self._history: list[str] = []

    def check(self, url: str, visible_text: str) -> dict:
        """Check if agent is stuck (same state for N steps)."""
        fingerprint = hashlib.md5((url + visible_text[:200]).encode()).hexdigest()[:12]
        self._history.append(fingerprint)

        # Count consecutive same fingerprints from the end
        same_count = 1
        for i in range(len(self._history) - 2, -1, -1):
            if self._history[i] == fingerprint:
                same_count += 1
            else:
                break

        stuck = same_count >= self.threshold
        return {"stuck": stuck, "same_state_for": same_count}

    def reset(self):
        self._history.clear()


# ── Main hook runner ─────────────────────────────────────────────────────────

async def run_hooks(
    page,
    url: str,
    visible_text: str,
    scan_patterns: list[dict] | None = None,
    dismiss_popups: bool = True,
    stuck_detector: StuckDetector | None = None,
) -> HookResults:
    """Run all post-action hooks and return collected results."""
    results = HookResults()

    # 1. Pattern scanner
    if scan_patterns:
        matches = await run_pattern_scanner(page, scan_patterns)
        results.patterns_found = matches

        # Generate suggested action if patterns found + submit context exists
        if matches:
            ctx = await _check_submit_context(page)
            if ctx:
                results.suggested_action = {
                    "action_type": "fill_form",
                    "params": {
                        "value": matches[0]["value"],
                        "submit_button": ctx["submit_text"],
                    },
                }

    # 2. Popup dismisser
    if dismiss_popups:
        popup_result = await run_popup_dismisser(page)
        results.popups_dismissed = popup_result.get("dismissed", 0)

    # 3. Stuck detector
    if stuck_detector:
        stuck_result = stuck_detector.check(url, visible_text)
        results.stuck = stuck_result["stuck"]
        results.same_state_for = stuck_result["same_state_for"]

    return results
```

**Step 2: Verify syntax**

Run: `cd agents/browser-use && python -c "import hooks; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add agents/browser-use/hooks.py
git commit -m "Add post-action hooks: pattern scanner, popup dismisser, stuck detector"
```

---

### Task 2: Rewrite planner.py — New prompt + hook results + rate limit backoff

**Files:**
- Modify: `agents/browser-use/planner.py`

**Step 1: Rewrite planner.py**

Replace `_SYSTEM_PROMPT` (lines 74-107) with the new prompt from the design doc. Modify `plan()` function (line 110) to accept `hook_results: str | None` parameter and include it in the user message. Add exponential backoff on 429 errors (retry 3 times with 5s, 10s, 20s delays — don't count as agent steps).

New `_SYSTEM_PROMPT`:

```python
_SYSTEM_PROMPT = """You are a browser automation agent. You observe a web page, decide one action, and execute it.

RULES:
1. Pick the simplest action that makes progress toward the task.
2. Use structural hints: hasCanvas → draw, hasDraggables → drag, hasShadowRoots → content may be hidden.
3. For multi-field forms: fill each field separately using selector/placeholder/name to target, then submit ONLY after ALL fields filled.
4. NEVER click decoy navigation buttons (Next, Continue, Proceed, Move On, Go Forward, Keep Going, Advance, Click Here, Next Step, Next Page, Next Section, Continue Reading, Continue Journey, Proceed Forward, Move On) unless the TASK specifically requires it.
5. If HOOKS.stuck is true, your last N actions had no effect. Try a COMPLETELY DIFFERENT action type or target.
6. If HOOKS.patterns_found has matches, consider whether they're relevant to your task. If HOOKS.suggested_action is provided and it matches your task goal, execute it.
7. After clicking a button that should reveal content, CHECK the observation for new text before clicking again.
8. For date pickers, dropdowns, and complex widgets: use evaluate_js to inspect the component structure first.
9. When extracting information, use evaluate_js to read specific DOM content rather than relying only on visible_text.
10. Signal done with success=true ONLY when you have completed the task objective and can report the requested information.
11. Use input metadata (id, name, type) to target the right field — e.g., use selector "#password" or placeholder "Password" to distinguish fields.

ACTIONS:
- click: {text, times} — Click element by visible text
- type: {text, selector, placeholder} — Type into ONE input field
- fill_form: {value, expression, placeholder, submit_button} — Fill input + optionally submit
- hover: {text, seconds} — Hover to reveal content
- scroll: {direction, pixels} — Scroll up/down
- drag: {} — Auto-detect and drag elements to targets
- draw: {strokes, selector} — Draw on canvas
- press_keys: {keys} — Press keyboard keys (e.g. ["ArrowUp", "Enter"])
- wait: {seconds} — Wait for content to load
- evaluate_js: {code} — Run JavaScript (escape hatch for novel interactions)
- done: {summary, success} — Task complete

Output JSON: {"thought": "...", "action_type": "...", "params": {...}}"""
```

Updated `plan()` signature and body:

```python
async def plan(observation_text: str, task: str, history: list[dict] | None = None,
               hook_results: str | None = None) -> dict:
    client = _get_client()

    parts = [f"TASK: {task}"]
    if history:
        history_lines = []
        for h in history[-5:]:
            action = h.get("action", "?")
            result = h.get("result", "?")
            if isinstance(result, dict):
                result = result.get("detail", str(result))
            history_lines.append(f"  - {action} → {str(result)[:100]}")
        parts.append(f"HISTORY (last {len(history_lines)} actions):\n" + "\n".join(history_lines))
    parts.append(f"OBSERVATION:\n{observation_text}")
    if hook_results:
        parts.append(hook_results)

    user_msg = "\n\n".join(parts)

    # Retry with exponential backoff on 429
    for attempt in range(4):
        try:
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=user_msg,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=_RESPONSE_SCHEMA,
                    temperature=0.1,
                    max_output_tokens=500,
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                if attempt < 3:
                    wait = [5, 10, 20][min(attempt, 2)]
                    print(f"    [planner] Rate limited, waiting {wait}s (attempt {attempt+1}/3)")
                    import asyncio
                    await asyncio.sleep(wait)
                    continue
            print(f"    [planner] Error: {e}")
            return {"thought": f"Planning failed: {e}", "action_type": "wait", "params": {"seconds": 2}}
```

**Step 2: Verify syntax**

Run: `cd agents/browser-use && python -c "import planner; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add agents/browser-use/planner.py
git commit -m "Rewrite planner prompt: decoy avoidance, hook integration, rate limit backoff"
```

---

### Task 3: Update observer.py — Observation diffing

**Files:**
- Modify: `agents/browser-use/observer.py`

**Step 1: Add observation diffing to Observation class**

Add a `changes` field to the `Observation` dataclass and a `diff_from(prev)` method. Update `to_prompt()` to include changes when present.

Add to `Observation` class:

```python
    changes: str = ""  # what changed since last observation

    def diff_from(self, prev: 'Observation') -> str:
        """Compute what changed since the previous observation."""
        diffs = []
        if self.url != prev.url:
            diffs.append(f"URL changed: {prev.url} → {self.url}")
        new_buttons = set(self.button_texts) - set(prev.button_texts)
        gone_buttons = set(prev.button_texts) - set(self.button_texts)
        if new_buttons:
            diffs.append(f"new buttons: {list(new_buttons)[:5]}")
        if gone_buttons:
            diffs.append(f"removed buttons: {list(gone_buttons)[:5]}")
        # Check for new visible text (first 200 chars diff)
        if self.visible_text[:200] != prev.visible_text[:200]:
            diffs.append("page content changed")
        # Input value changes
        prev_vals = {(i.get('name') or i.get('id') or str(idx)): i.get('value', '')
                     for idx, i in enumerate(prev.input_fields)}
        for idx, inp in enumerate(self.input_fields):
            key = inp.get('name') or inp.get('id') or str(idx)
            old_val = prev_vals.get(key, '')
            new_val = inp.get('value', '')
            if old_val != new_val:
                diffs.append(f"input '{key}' value: '{old_val}' → '{new_val}'")
        return "; ".join(diffs) if diffs else "no changes"
```

Update `to_prompt()` to include changes:

```python
    def to_prompt(self) -> str:
        parts = []
        if self.url:
            parts.append(f"url: {self.url}")
        if self.title:
            parts.append(f"title: {self.title}")
        if self.changes:
            parts.append(f"changes: {self.changes}")
        # ... rest unchanged
```

**Step 2: Verify syntax**

Run: `cd agents/browser-use && python -c "from observer import Observation; o1 = Observation(url='a'); o2 = Observation(url='b'); print(o2.diff_from(o1))"`
Expected: `URL changed: a → b`

**Step 3: Commit**

```bash
git add agents/browser-use/observer.py
git commit -m "Add observation diffing: track URL, button, content, and input changes"
```

---

### Task 4: Update curriculum.yaml — Add scan_patterns and hooks config

**Files:**
- Modify: `agents/browser-use/training/curriculum.yaml`

**Step 1: Add scan_patterns and hooks to all tasks**

Add `scan_patterns` and `hooks` fields to each task in curriculum.yaml. At minimum, every task gets `dismiss_popups: true` and a `stuck_threshold`. The challenge gets the code pattern scanner. Other tasks get task-appropriate patterns (prices, counts, etc.).

Key additions:
- `challenge-30step`: `scan_patterns: [{name: code, regex: "[A-HJ-NP-Z2-9]{6}", source: all}]`, `stuck_threshold: 3`
- `flight-search`: `scan_patterns: [{name: price, regex: "\\$[\\d,]+", source: visible_text}]`
- `job-market`: `scan_patterns: [{name: count, regex: "\\d+ open positions?", source: visible_text}]`
- All tasks: `hooks: {dismiss_popups: true, stuck_threshold: 5}`

**Step 2: Verify YAML syntax**

Run: `cd agents/browser-use && python -c "import yaml; tasks = yaml.safe_load(open('training/curriculum.yaml')); print(f'{len(tasks)} tasks, scan_patterns: {[t[\"id\"] for t in tasks if t.get(\"scan_patterns\")]}')" `
Expected: `11 tasks, scan_patterns: ['flight-search', 'job-market', 'challenge-30step']`

**Step 3: Commit**

```bash
git add agents/browser-use/training/curriculum.yaml
git commit -m "Add scan_patterns and hooks config to curriculum tasks"
```

---

### Task 5: Update agent.py — Wire hooks into the main loop

**Files:**
- Modify: `agents/browser-use/agent.py`

**Step 1: Import hooks and wire into the main loop**

At the top of agent.py, add import:

```python
from hooks import run_hooks, StuckDetector, HookResults
```

Update `run_agent()` signature to accept hook config:

```python
async def run_agent(
    url: str,
    goal: str,
    headless: bool = False,
    max_runs: int = MAX_RUNS_DEFAULT,
    max_steps: int = 120,
    reward_type: str = "binary",
    reward_pattern: str = r"Step\s+(\d+)",
    task_id: str = "",
    save_trajectory: bool = True,
    scan_patterns: list[dict] | None = None,
    dismiss_popups: bool = True,
    stuck_threshold: int = 5,
):
```

Before the step loop (after `t0 = time.time()`), initialize:

```python
        stuck_detector = StuckDetector(threshold=stuck_threshold)
        prev_obs: Observation | None = None
        hook_results_text: str | None = None
```

In the step loop, after OBSERVE and before PLAN, add diffing:

```python
            # Compute observation diff
            if prev_obs:
                obs.changes = obs.diff_from(prev_obs)
            prev_obs = obs
            obs_text = obs.to_prompt()
```

Pass `hook_results_text` to the planner call (line ~440):

```python
                action = await plan(obs_text, goal, history[-5:], hook_results=hook_results_text)
```

After EXECUTE (after `exec_ms` computation, ~line 458), add hook runner:

```python
            # 3.5. RUN HOOKS
            try:
                hook_res = await run_hooks(
                    page,
                    url=obs.url,
                    visible_text=obs.visible_text,
                    scan_patterns=scan_patterns,
                    dismiss_popups=dismiss_popups,
                    stuck_detector=stuck_detector,
                )
                hook_results_text = hook_res.to_prompt() if (hook_res.patterns_found or hook_res.stuck or hook_res.popups_dismissed > 0) else None
            except Exception as e:
                print(f"    [hooks] Error: {e}")
                hook_results_text = None
```

Update the CLI section to parse `scan_patterns` and `hooks` from task YAML:

```python
    scan_patterns = None
    dismiss_popups_flag = True
    stuck_threshold = 5

    if args.task_file:
        with open(args.task_file) as f:
            task_def = yaml.safe_load(f)
        if isinstance(task_def, list):
            task_def = task_def[0]
        goal = goal or task_def.get("description", "")
        url = task_def.get("url", url)
        task_id = task_def.get("id", "")
        reward_type = task_def.get("reward_type", reward_type)
        reward_pattern = task_def.get("reward_pattern", reward_pattern)
        scan_patterns = task_def.get("scan_patterns")
        hooks_cfg = task_def.get("hooks", {})
        dismiss_popups_flag = hooks_cfg.get("dismiss_popups", True)
        stuck_threshold = hooks_cfg.get("stuck_threshold", 5)
```

And pass them to `run_agent()`:

```python
    await run_agent(
        ...
        scan_patterns=scan_patterns,
        dismiss_popups=dismiss_popups_flag,
        stuck_threshold=stuck_threshold,
    )
```

Also add `--scan-pattern` CLI arg for ad-hoc pattern scanning:

```python
    parser.add_argument("--scan-pattern", help="Regex pattern to scan for after each action")
```

And in the resolution section:

```python
    if args.scan_pattern and not scan_patterns:
        scan_patterns = [{"name": "match", "regex": args.scan_pattern, "source": "all"}]
```

**Step 2: Verify syntax**

Run: `cd agents/browser-use && python -c "import agent; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add agents/browser-use/agent.py
git commit -m "Wire hooks into agent loop: pattern scanning, popup dismissal, stuck detection"
```

---

### Task 6: Integration test — Login

**Step 1: Run login test**

Run:
```bash
cd agents/browser-use && timeout 60 uv run python agent.py \
  --url "https://the-internet.herokuapp.com/login" \
  --goal "Log in with username 'tomsmith' and password 'SuperSecretPassword!'. After successful login, report the flash message." \
  --headless --max-steps 15
```

Expected: `Success: True` in 4-6 steps, <15s

**Step 2: Commit if any fixes needed**

---

### Task 7: Integration test — 30-step challenge via task file

**Step 1: Create a single-task YAML for the challenge**

Create `agents/browser-use/tasks/challenge.yaml`:

```yaml
id: challenge-30step
url: https://serene-frangipane-7fd25b.netlify.app/
description: |
  Complete all 30 steps of the browser challenge. Each step presents a different
  interaction puzzle (clicking, hovering, dragging, drawing on canvas, keyboard
  sequences, scrolling, shadow DOM, iframes, math problems, and more).

  After solving each puzzle, a 6-character alphanumeric code appears somewhere on
  the page. Find it, enter it in the code input field, and click "Submit Code" to
  advance to the next step.

  IMPORTANT: Do NOT click decoy navigation buttons (Next, Continue, Proceed, etc).
  Only "Submit Code" advances steps. The code uses characters A-HJ-NP-Z2-9
  (no I, O, 0, 1).
reward_type: progress
reward_pattern: "Step\\s+(\\d+)"
scan_patterns:
  - name: code
    regex: "[A-HJ-NP-Z2-9]{6}"
    source: all
hooks:
  dismiss_popups: true
  stuck_threshold: 3
```

**Step 2: Run challenge test**

Run:
```bash
cd agents/browser-use && timeout 360 uv run python agent.py \
  --task-file tasks/challenge.yaml \
  --headless --max-steps 120
```

Expected: Steps advancing (Step 1 → Step 2 → ...), pattern scanner finding codes.
Target: 30/30 in <5 min. May need debugging iterations.

**Step 3: Debug and fix**

If the challenge doesn't advance, check:
1. Is the pattern scanner finding codes? (Look for `patterns_found` in output)
2. Is the suggested_action being followed by the planner?
3. Is fill_form + Submit Code working? (Should be — we fixed this earlier)
4. Is the planner clicking decoy buttons? (Check the new prompt's Rule 4)

**Step 4: Commit fixes**

```bash
git add -A && git commit -m "Fix: [description of what was fixed for challenge]"
```

---

### Task 8: Integration test — Real-world task (Figure AI careers)

**Step 1: Run Figure AI careers task**

Run:
```bash
cd agents/browser-use && timeout 120 uv run python agent.py \
  --url "https://www.figure.ai/careers" \
  --goal "Count all open positions on the Figure AI careers page. Group them by department and report the total count and the department with the most openings." \
  --headless --max-steps 30
```

Expected: Agent scrolls through careers page, extracts position counts, reports via `done`.

**Step 2: Run BBC News task**

Run:
```bash
cd agents/browser-use && timeout 120 uv run python agent.py \
  --url "https://www.bbc.com/news" \
  --goal "Find today's top 3 headlines in world news. Report each headline and a one-sentence summary." \
  --headless --max-steps 30
```

Expected: Agent navigates BBC News, dismisses cookie banner (popup hook), extracts headlines.

**Step 3: Commit if fixes needed**

---

### Task 9: Final verification + push

**Step 1: Run all three tests in sequence**

```bash
# Login
timeout 60 uv run python agent.py --url "https://the-internet.herokuapp.com/login" \
  --goal "Log in with username 'tomsmith' and password 'SuperSecretPassword!'" --headless --max-steps 15

# Challenge (first 5 steps at least)
timeout 120 uv run python agent.py --task-file tasks/challenge.yaml --headless --max-steps 30

# Careers
timeout 120 uv run python agent.py --url "https://www.figure.ai/careers" \
  --goal "Count open positions at Figure AI" --headless --max-steps 20
```

**Step 2: Verify trajectory files exist**

Run: `ls -la runs/*/trajectories/*.jsonl | tail -3`
Expected: Three trajectory JSONL files from the three runs.

**Step 3: Commit and push**

```bash
git add -A
git commit -m "General agent v2: hooks, improved planner, observation diffing

- PatternScanner: configurable regex scanning across visible/shadow/hidden DOM
- PopupDismisser: auto-dismiss cookie banners and overlays
- StuckDetector: detect and break out of action loops
- Planner: decoy avoidance, hook-aware reasoning, rate limit backoff
- Observer: track what changed between steps
- Task config: scan_patterns and hooks in curriculum.yaml"

git push origin general-agent
```

---

## Execution Notes

- **Rate limits**: If Gemini 429s persist, use `--max-steps 30` for shorter test runs. The exponential backoff in the new planner should help.
- **Challenge debugging**: The pattern scanner + suggested_action is the key to challenge speed. If codes aren't being found, check the `_PATTERN_SCAN_JS` regex against actual page content.
- **No TDD**: This project uses integration tests against live websites, not unit tests. The "test" steps are live agent runs.
- **Working directory**: Always `cd agents/browser-use` before running commands.
