"""
Page Classifier: Fast inline LLM call that classifies a web page into
an interaction pattern and returns structured action parameters.

Uses Gemini 2.0 Flash for speed (~0.5-1.5s per call).
Stateless — no conversation history, no caching overhead.
Text-only — page features extracted by JS, no screenshots.
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
            raise ValueError("GOOGLE_API_KEY required for classifier")
        _client = genai.Client(api_key=api_key)
    return _client


# Response schema for structured output
_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "required": ["action", "params", "reasoning"],
    "properties": {
        "action": {
            "type": "STRING",
            "description": "The interaction action type to perform",
        },
        "params": {
            "type": "OBJECT",
            "description": "Parameters for the action",
            "properties": {
                "button_text": {"type": "STRING", "description": "Text of button or element to click (required for click_button and click_element)"},
                "times": {"type": "INTEGER", "description": "Number of times to click"},
                "expression": {"type": "STRING", "description": "Math expression to evaluate"},
                "answer": {"type": "STRING", "description": "Computed answer to fill in"},
                "pixels": {"type": "INTEGER", "description": "Pixels to scroll"},
                "keys": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Keyboard keys to press",
                },
                "hover_text": {"type": "STRING", "description": "Text near hover target element"},
                "seconds": {"type": "NUMBER", "description": "Time to wait"},
                "type_text": {"type": "STRING", "description": "Text to type into an input"},
                "input_placeholder": {"type": "STRING", "description": "Placeholder of input to fill"},
            },
        },
        "reasoning": {
            "type": "STRING",
            "description": "Brief explanation of why this action was chosen",
        },
    },
}


_SYSTEM_PROMPT = """You classify browser challenge pages into action types using STRUCTURAL signals.

INPUT: visible_text, button_texts, and structural features (booleans/counts from DOM analysis).

STRUCTURAL SIGNAL → ACTION MAPPING (check in this priority order):

1. overlayCount>0 → "dismiss_popups" (ALWAYS handle overlays first)
2. hasCanvas=true → "draw_canvas"
3. hasDraggables=true → "drag_drop"
4. hasShadowRoots=true → "shadow_dom"
5. hasIframes=true, iframeDepth>0 → "recursive_iframe"
6. hasTabButtons=true → "multi_tab"
7. hasProgressSteps=true → "sequence" (click/hover/type/scroll pills)
8. hasMathExpression=true → "fill_form" (compute answer, fill input)
9. hasStatusIndicator=true + hasTerminalOutput=true + "Connect" in buttons → "websocket"
10. hasMultiStepFlow=true + "Register"/"Retrieve" in buttons → "service_worker"
11. hasRadioButtons=true → "dismiss_popups" (radio selection behind popups)
12. hasTimerOrCountdown=true → "wait" (seconds from visible text)
13. hasNestedLayers=true → "shadow_dom" if hasShadowRoots else "recursive_iframe"

FALLBACK (when no structural signal matches, use visible_text + buttons):
- "Reveal Code"/"Extract Code"/"Show"/"Unlock" button → "click_button"
- "click here N times" / "click this box" → "click_element" with button_text="click here" (or similar text ON the clickable element), times=N
- "scroll"/"below" in text → "scroll_down" pixels=800
- "hover" in text → "hover_element" seconds=1.5
- Key arrows (↑↓←→⏎) in text → "keyboard_sequence"
- "split"/"parts"/"scattered" in text → "split_parts"
- "frame"/"video" + ±1/±10 buttons → "video_frame"
- "Base64"/"decode"/"encoded" in text → "encoded_code"
- "Code revealed" or 6-char code visible → "wait" seconds=1
- "AUTO-SUBMITTED" → "wait" seconds=2

AVAILABLE ACTIONS: click_button, click_element, fill_form, scroll_down, hover_element,
keyboard_sequence, drag_drop, draw_canvas, shadow_dom, service_worker, split_parts,
video_frame, dismiss_popups, sequence, encoded_code, multi_step, type_text, wait,
websocket, recursive_iframe, mutation, multi_tab, unknown

RULES:
- NEVER click decoy buttons: Next, Continue, Proceed, Advance, Go Forward, Move Forward, Keep Going, Next Step, Next Page, Continue Reading, Move On
- "Submit Code" is auto-handled — never return it
- For click_button: button_text must be EXACT text from button_texts
- For click_element: button_text is the text ON/NEAR the clickable element (e.g. "click here", "click this box")
- ALWAYS provide button_text for click_button and click_element — never leave it empty
- Math: use "fill_form" with computed answer
- Keys: ↑=ArrowUp ↓=ArrowDown ←=ArrowLeft →=ArrowRight ⏎=Enter
- "click N more times" or "(3/5)": set times accordingly
"""


async def classify_page(page_info: dict) -> dict:
    """
    Classify a page and return a structured action.

    Args:
        page_info: Dict with visibleTextPreview, buttonTexts, and structural
                   signals (hasCanvas, hasDraggables, hasShadowRoots, hasIframes,
                   iframeDepth, hasTabButtons, tabCount, hasStatusIndicator,
                   hasTerminalOutput, hasProgressSteps, hasTimerOrCountdown,
                   hasMathExpression, hasNestedLayers, hasMultiStepFlow,
                   overlayCount, hasRadioButtons, interactiveElementCount,
                   inputCount, buttonCount)

    Returns:
        Dict with action, params, reasoning
    """
    client = _get_client()

    # Build compact page description
    parts = []
    text = page_info.get("visibleTextPreview", "")
    if text:
        parts.append(f"visible_text: {text}")

    btns = page_info.get("buttonTexts", [])
    if btns:
        parts.append(f"button_texts: {btns}")

    # Forward all structural features
    features = []
    bool_keys = [
        "hasCanvas", "hasDraggables", "hasShadowRoots", "hasIframes",
        "hasTabButtons", "hasStatusIndicator", "hasTerminalOutput",
        "hasProgressSteps", "hasTimerOrCountdown", "hasMathExpression",
        "hasNestedLayers", "hasMultiStepFlow", "hasRadioButtons",
    ]
    for key in bool_keys:
        val = page_info.get(key)
        if val is not None:
            features.append(f"{key}={'true' if val else 'false'}")

    int_keys = [
        "inputCount", "buttonCount", "iframeDepth", "tabCount",
        "overlayCount", "interactiveElementCount",
    ]
    for key in int_keys:
        val = page_info.get(key)
        if val is not None:
            features.append(f"{key}={val}")

    parts.append(f"features: {', '.join(features)}")

    user_msg = "\n".join(parts)

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
        print(f"    [classifier] Error: {e}")
        return {"action": "unknown", "params": {}, "reasoning": f"Classification failed: {e}"}
