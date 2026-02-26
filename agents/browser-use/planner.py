"""
Planner: LLM-based action planner for browser automation.

Uses Gemini 2.0 Flash for speed (~0.5-1.5s per call).
Stateless — no conversation history beyond last N actions.
Text-only — page features from observer, no screenshots.

Input: observation + task description + recent action history
Output: {thought, action_type, params}
"""

import json
import os
from google import genai
from google.genai import types


_client = None


def _get_client():
    global _client
    if _client is None:
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY required for planner")
        _client = genai.Client(api_key=api_key)
    return _client


_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "required": ["thought", "action_type", "params"],
    "properties": {
        "thought": {
            "type": "STRING",
            "description": "Brief reasoning about what to do next (1-2 sentences)",
        },
        "action_type": {
            "type": "STRING",
            "description": "Action to perform",
            "enum": ["click", "type", "fill_form", "hover", "scroll", "drag", "draw",
                     "press_keys", "wait", "evaluate_js", "done"],
        },
        "params": {
            "type": "OBJECT",
            "description": "Parameters for the action",
            "properties": {
                "text": {"type": "STRING", "description": "Text of element to click/hover, or text to type"},
                "value": {"type": "STRING", "description": "Value to fill in a form field"},
                "expression": {"type": "STRING", "description": "Math expression to evaluate"},
                "placeholder": {"type": "STRING", "description": "Input placeholder to target"},
                "selector": {"type": "STRING", "description": "CSS selector for targeting"},
                "submit_button": {"type": "STRING", "description": "Button text to click after filling form"},
                "direction": {"type": "STRING", "description": "Scroll direction: up or down"},
                "pixels": {"type": "INTEGER", "description": "Pixels to scroll"},
                "seconds": {"type": "NUMBER", "description": "Duration to wait or hover"},
                "times": {"type": "INTEGER", "description": "Number of times to repeat action"},
                "keys": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Keyboard keys to press (e.g. ArrowUp, Enter, Control+a)",
                },
                "strokes": {"type": "INTEGER", "description": "Number of strokes to draw"},
                "code": {"type": "STRING", "description": "JavaScript code to evaluate"},
                "summary": {"type": "STRING", "description": "Task completion summary"},
                "success": {"type": "BOOLEAN", "description": "Whether task succeeded"},
            },
        },
    },
}


_SYSTEM_PROMPT = """You are a browser automation agent. You observe web pages and decide what action to take next to accomplish the given task.

INPUT: You receive:
1. TASK: A description of what to accomplish
2. OBSERVATION: Current page state (URL, visible text, buttons, inputs, structural features)
3. HISTORY: Last few actions and their results

OUTPUT: You return a JSON action to execute.

AVAILABLE ACTIONS:
- click: Click an element. params: {text: "button or element text", times: N}
- type: Type into ONE input field. params: {text: "value to type", selector: "#id or css", placeholder: "placeholder text"}
  Use selector (e.g. "#username", "input[name='email']") or placeholder to target specific fields.
- fill_form: Fill ONE input field + optionally click a submit button. params: {value: "answer", expression: "math expr", placeholder: "hint", submit_button: "Submit"}
  Only set submit_button AFTER all fields are filled.
- hover: Hover to reveal content. params: {text: "element text", seconds: N}
- scroll: Scroll page. params: {direction: "down"/"up", pixels: N}
- drag: Drag elements to targets. params: {} (auto-detects sources/targets)
- draw: Draw on canvas. params: {strokes: N, selector: "canvas"}
- press_keys: Press keyboard keys. params: {keys: ["ArrowUp", "Enter", ...]}
- wait: Wait for content. params: {seconds: N}
- evaluate_js: Run JavaScript. params: {code: "..."}  (escape hatch for novel interactions)
- done: Task complete. params: {summary: "result", success: true/false}

RULES:
1. Pick the simplest action that makes progress toward the task
2. Use structural features as hints: hasCanvas → draw, hasDraggables → drag, etc.
3. If an action failed, try a different approach — don't repeat the same failing action
4. Use evaluate_js only as a last resort when standard actions can't accomplish the goal
5. Call done when the task objective is achieved or clearly impossible
6. Be precise with button/element text — match what's visible on the page
7. For multi-field forms (login, registration, search): fill each field separately with type/fill_form using selector or placeholder to target each one, then click submit ONLY after ALL fields are filled
8. Use input metadata from the observation (id, name, placeholder) to target the right field
"""


async def plan(observation_text: str, task: str, history: list[dict] | None = None) -> dict:
    """Plan the next action given the current observation and task.

    Args:
        observation_text: Formatted observation from observer.observe().to_prompt()
        task: Natural language task description
        history: List of recent {action, result} dicts (last 3-5)

    Returns:
        Dict with thought, action_type, params
    """
    client = _get_client()

    # Build user message
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

    user_msg = "\n\n".join(parts)

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
        result = json.loads(response.text)
        return result
    except Exception as e:
        print(f"    [planner] Error: {e}")
        return {"thought": f"Planning failed: {e}", "action_type": "wait", "params": {"seconds": 2}}
