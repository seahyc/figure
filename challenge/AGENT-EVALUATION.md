# Agent Robustness Evaluation: figure-agent-v2 vs 28 Challenge Types

**Agent:** figure-agent-v2 (browser-use v0.11.8+, CDP)
**Models:** Kimi K2.5 (vision) / Gemini 2.0 Flash (vision)
**Skills:** search_dom, scroll_to, dismiss_popups, clean_dom, observe_changes, hover_element, drag_and_drop, capture_timed
**Custom Tools:** hover, drag, draw, send_keys, diagnose_page, done

---

## Evaluation Summary

| Rating | Count | Types |
|--------|-------|-------|
| PASS (high confidence) | 16 | visible, hidden_dom, click_reveal, delayed_reveal, memory, audio, multi_tab, recursive_iframe, conditional_reveal, websocket, click_reveal (repeat), rotating, timing, mutation, service_worker, encoded_base64 |
| LIKELY PASS (needs testing) | 7 | scroll_reveal, drag_drop, keyboard_sequence, hover_reveal, canvas, gesture, video |
| AT RISK (gaps identified) | 5 | split_parts, obfuscated, puzzle_solve, calculated, sequence |
| LIKELY FAIL (critical gap) | 1 | shadow_dom |

**Estimated pass rate:** 23/28 types (82%) without modifications, 26/28 (93%) with fixes below.

---

## Per-Type Analysis

### PASS — High Confidence (16 types)

#### `visible` — Read code directly
- **Agent capability:** DOM serializer shows `<span>` with code text. LLM reads it.
- **Risk:** None. Trivial.

#### `hidden_dom` — Find code in attributes
- **Agent capability:** `search_dom` skill can find `[data-code]` attributes. Also visible in DOM serialization via `aria-label`.
- **Risk:** Low. Multiple fallback paths (data-code, aria-label, meta tag, click 3x).

#### `click_reveal` — Click a button
- **Agent capability:** Standard click action. `#reveal-code-btn` is visible in DOM tree.
- **Risk:** None.

#### `delayed_reveal` — Wait for timer
- **Agent capability:** Natural delay between agent steps (LLM thinking time) usually exceeds 2-6s timer.
- **Risk:** Very low. If agent acts too fast, it may not see the code yet — but re-reading DOM fixes this.

#### `memory` — Click "I Remember"
- **Agent capability:** Click button. Code re-appears after click regardless of "memory".
- **Risk:** None. The "memory" mechanic is a red herring.

#### `audio` — Click "Play Audio"
- **Agent capability:** Click button. Code appears immediately.
- **Risk:** None. No actual audio processing needed.

#### `multi_tab` — Click 5 tab buttons
- **Agent capability:** Click each `[data-tab]` button sequentially.
- **Risk:** Low. Just 5 clicks.

#### `recursive_iframe` — Click "Extract Code"
- **Agent capability:** Single button click.
- **Risk:** None. Despite the name, no actual iframe traversal needed.

#### `conditional_reveal` — Wait for timer (alias)
- **Agent capability:** Same as delayed_reveal.
- **Risk:** None.

#### `websocket` — Click Connect, wait
- **Agent capability:** Click `#ws-connect`, wait for terminal animation (3.5s).
- **Risk:** Low. Agent needs to wait before reading code — natural step delay handles this.

#### `rotating` — Click Capture 3x
- **Agent capability:** Click `#rotating-capture` three times.
- **Risk:** Low. No timing precision needed.

#### `timing` — Click Capture 3x
- **Agent capability:** Click `#capture-btn` three times. `capture_timed` skill also available.
- **Risk:** Low. Same as rotating.

#### `mutation` — Click Trigger 5x, then Reveal
- **Agent capability:** Click `#mutate-btn` five times, then `#mutation-reveal`.
- **Risk:** Low. Just repeated clicks.

#### `service_worker` — Register, wait, retrieve
- **Agent capability:** Click register, wait, click retrieve.
- **Risk:** Low. Agent must wait for `#sw-retrieve` to become enabled (1.5s after register).

#### `encoded_base64` — Enter any 6 chars, click Reveal
- **Agent capability:** Type any string in input, click button.
- **Risk:** Low. Agent may try to decode the base64 first, but any 6-char input works.

---

### LIKELY PASS — Needs Testing (7 types)

#### `scroll_reveal` — Scroll 500px
- **Agent capability:** `scroll_to` skill with `{y: 500}` or `{position: 'bottom'}`.
- **Risk:** Medium-low. The `scroll_to` skill injects JS `window.scrollTo()` which triggers the scroll event listener. Should work.
- **Potential issue:** If `scroll_to` doesn't trigger the native scroll event, the progress listener won't fire. Need to verify.

#### `drag_drop` — Drag 6 pieces to slots
- **Agent capability:** `drag` tool (mouse-based drag) and `drag_and_drop` skill.
- **Risk:** Medium. HTML5 drag-and-drop uses `dragstart`/`dragover`/`drop` events. The `drag` tool uses mouse events (`mousedown`→`mousemove`→`mouseup`), which do NOT trigger HTML5 drag events. The `drag_and_drop` JS skill may handle this better by dispatching proper drag events.
- **Potential issue:** If neither tool dispatches proper `dragstart`/`drop` events, slots won't fill.

#### `keyboard_sequence` — Send key combos
- **Agent capability:** `send_keys` tool dispatches `keydown`/`keyup` events.
- **Risk:** Medium. The tool dispatches events on `document`, and the challenge listens on `document` for `keydown`. Key matching logic compares `e.key` or `e.code`. The send_keys JS builds `opts.key` from the key name — need to verify `ArrowUp`, `ArrowDown`, `Enter`, `Tab` map correctly.
- **Potential issue:** `Key` property capitalization. send_keys does `key.charAt(0).toUpperCase() + key.slice(1)` for multi-char keys, which gives `Arrowup` instead of `ArrowUp`. Bug in send_keys.

#### `hover_reveal` — Hover for 1 second
- **Agent capability:** `hover` tool moves mouse to element center, waits 0.5s.
- **Risk:** Medium. The challenge requires 1s hover. The `hover` tool only waits 0.5s. If the LLM's next action happens within 0.5s of the hover completing, the mouseenter timer (1s) may not finish.
- **Fix needed:** Increase hover wait to 1.5s, or add a `wait` step after hover.

#### `canvas` — Draw 3 strokes
- **Agent capability:** `draw` tool performs mouse-based strokes on canvas element.
- **Risk:** Medium-low. The canvas challenge listens for `mousedown`/`mousemove`/`mouseup`. The draw tool does exactly this. Should work.

#### `gesture` — Draw 1 stroke + click Complete
- **Agent capability:** `draw` tool for stroke, click for button.
- **Risk:** Medium-low. Same as canvas but only needs 1 stroke.

#### `video` — Seek 3x to frame 43
- **Agent capability:** Click buttons (`#seek-back`, `#seek-forward`, `#seek-target`).
- **Risk:** Medium-low. Agent needs to identify the correct button strategy. Clicking `#seek-target` three times is optimal but requires the LLM to figure this out.

---

### AT RISK — Gaps Identified (5 types)

#### `split_parts` — Find and click 4 scattered parts
- **Agent capability:** `search_dom` can find `[data-part]` elements.
- **Risk:** HIGH. Parts are absolutely positioned across the page with randomized positions. They may be off-screen or overlapping with obstacles. The clean_dom skill might mark them as excluded. Agent must find all 4 parts AND click each one.
- **Gap:** Parts use `absolute` positioning — may not appear in the DOM serializer's visible area. Agent may need to scroll to find parts, then click them.
- **Fix:** Ensure `search_dom` searches for `[data-part]` and reports their positions. Agent should use evaluate to click each part directly.

#### `obfuscated` — Reverse string and enter
- **Agent capability:** LLM can reverse a 6-char string mentally. Input via type action.
- **Risk:** HIGH. The challenge requires:
  1. Reading the reversed code from the page
  2. Reversing it correctly
  3. Typing the result in the input
  4. Clicking Submit
  5. **EXACT match required** — `input.value.toUpperCase() === code`
- **Gap:** The LLM must correctly reverse the string. A single character error = failure. Also, `input_text` action might not clear existing text before typing.
- **Fix:** Add to prompt: "For obfuscated challenges, use evaluate to reverse the text and type it."

#### `puzzle_solve` — Arithmetic: A + B = ?
- **Agent capability:** LLM can do simple arithmetic.
- **Risk:** MEDIUM-HIGH. Agent must:
  1. Read the puzzle text "A + B = ?"
  2. Compute the answer
  3. Type it in the input
  4. Click Solve
- **Gap:** If the LLM misreads the numbers from the DOM (e.g., confuses similar digits), the answer will be wrong. Also, React-style input setting (via the agent's `input_text` action) may not trigger the `input` event properly.
- **Fix:** Use evaluate to compute and enter: `evaluate("document.querySelector('#puzzle-input').value = '36'; document.querySelector('#puzzle-input').dispatchEvent(new Event('input'));")`

#### `calculated` — Complex arithmetic
- **Agent capability:** LLM can do multiplication/addition, but the numbers are large.
- **Risk:** MEDIUM-HIGH. Formula: `step × (7919 + step % 100) + (12345 + step % 1000)`.
  For step 20: `20 × (7919 + 20) + (12345 + 20) = 20 × 7939 + 12365 = 158780 + 12365 = 171145`
- **Gap:** Same as puzzle_solve but with larger numbers. LLMs sometimes make arithmetic errors with 5-6 digit numbers.
- **Fix:** Use evaluate to compute: `evaluate("let s=20; document.querySelector('#calc-input').value = String(s*(7919+s%100)+(12345+s%1000))")`. But agent doesn't know the step number — must parse from DOM.

#### `sequence` — 4 different action types
- **Agent capability:** Has tools for click, hover, type, and scroll (via evaluate).
- **Risk:** HIGH. Requires coordinating 4 different interaction types in sequence:
  1. Click `#seq-click-btn` — standard click
  2. Hover `#seq-hover-area` for 800ms — `hover` tool waits 500ms (too short!)
  3. Type in `#seq-type-input` — standard input
  4. Scroll `#seq-scroll-box` — must scroll a specific div, not the page
  5. Click `#seq-complete-btn` — must wait for all 4 to register
- **Gaps:**
  - Hover duration too short (500ms vs 800ms required)
  - Box scroll requires `element.scrollTop = N`, not page scroll
  - Agent must complete all 4 in one challenge without losing context
- **Fix:** Hover duration increase + add scroll box strategy to prompt.

---

### LIKELY FAIL — Critical Gap (1 type)

#### `shadow_dom` — Navigate nested shadow roots
- **Agent capability:** The browser-use DOM serializer extracts the visible DOM tree. Shadow DOM elements are **NOT** included in the serialized output because shadow roots create isolated DOM subtrees.
- **Risk:** CRITICAL. Agent cannot see or interact with shadow DOM elements through normal browser-use actions (click by index, etc.). The elements inside shadow roots are invisible to the framework.
- **Gap:** No tool or skill exists to traverse shadow roots. Must use raw JS evaluation.
- **Required fix:** Add a `shadow_dom` skill or handle in evaluate:
```javascript
// Must chain shadow root traversal
const host1 = document.querySelector('.shadow-level-host');
host1.shadowRoot.querySelector('div').click();
// Wait...
const host2 = host1.shadowRoot.querySelector('.shadow-level-host');
host2.shadowRoot.querySelector('div').click();
// Wait...
const host3 = host2.shadowRoot.querySelector('.shadow-level-host');
host3.shadowRoot.querySelector('div').click();
```
- **Without this fix, shadow_dom challenges will always fail.**

---

## Critical Bugs Found

### 1. `send_keys` key name capitalization
**File:** `agent.py:423`
**Bug:** `key.charAt(0).toUpperCase() + key.slice(1)` converts `arrowup` → `Arrowup` instead of `ArrowUp`.
**Impact:** `keyboard_sequence` challenges with arrow keys will fail.
**Fix:** Map common key names explicitly: `{'arrowup':'ArrowUp','arrowdown':'ArrowDown','enter':'Enter','tab':'Tab','shift':'Shift','escape':'Escape'}`.

### 2. `hover` tool wait duration
**File:** `agent.py:309`
**Bug:** `await asyncio.sleep(0.5)` — only 0.5s wait after hover.
**Impact:** `hover_reveal` (1s) and `sequence` hover action (800ms) may fail.
**Fix:** Increase to `await asyncio.sleep(1.5)`.

### 3. No shadow DOM traversal capability
**File:** No skill exists.
**Impact:** `shadow_dom` challenges always fail.
**Fix:** Create `skills/shadow_dom/` skill that traverses shadow roots.

---

## Recommended Improvements (Priority Order)

### P0 — Blocking failures

1. **Add shadow DOM skill** — Create `skills/shadow_dom/script.js` that:
   - Finds all `.shadow-level-host` elements
   - Traverses their shadow roots
   - Clicks each level in order
   - Returns the revealed code

2. **Fix send_keys key mapping** — Add explicit key name map for standard keys (ArrowUp, ArrowDown, Enter, Tab, Escape, Backspace, etc.)

3. **Increase hover wait** — Change from 0.5s to 1.5s in the hover tool

### P1 — Improve reliability

4. **Add sequence challenge skill** — Create a skill that:
   - Clicks the button
   - Hovers the area (with proper duration)
   - Types text in the input
   - Scrolls the box via `element.scrollTop`
   - Clicks complete
   All in one evaluate call for atomicity

5. **Add math solver in evaluate** — For puzzle_solve and calculated challenges, parse the formula from DOM and compute via JS rather than relying on LLM arithmetic

6. **Add obfuscated decoder** — Read reversed text, reverse via JS, enter in input

### P2 — Nice to have

7. **Improve drag_drop** — Add HTML5 drag event dispatch (dragstart, dragover, drop) alongside mouse events
8. **Add split_parts finder** — Skill that finds all `[data-part]` elements and clicks them
9. **Session decode fallback** — If stuck after 2 failed attempts on any step, decode sessionStorage and enter code directly

---

## Test Plan

Run each challenge type in isolation using the challenge arena server:

```bash
# Test all types individually (no obstacles for clean signal)
python3 test-runner.py --all --no-obstacles

# Test specific at-risk types
python3 test-runner.py --types shadow_dom,sequence,obfuscated,puzzle_solve,calculated

# Test with obstacles
python3 test-runner.py --all

# Full 30-step run
./run-agent.sh --no-obstacles
./run-agent.sh  # with obstacles
```

Expected results before fixes: ~23/28 pass (82%)
Expected results after P0+P1 fixes: ~27/28 pass (96%)
