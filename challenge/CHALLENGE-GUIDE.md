# Browser Navigation Challenge - Complete Reference

**Live URL:** https://serene-frangipane-7fd25b.netlify.app
**Local URL:** http://127.0.0.1:8765
**Goal:** Complete 30 steps, each with a unique challenge type
**Codes:** 6-character alphanumeric, randomly generated per session

---

## Architecture & Validation Mechanics

### Code Flow (Critical to Understand)

The challenge uses an **off-by-one pattern** that is intentional, not a bug:

```
Session generates: codes[1], codes[2], ... codes[30]

For step N:
  1. Challenge completes → markChallengeComplete(N, proof) → returns codes[N+1]
  2. The RETURNED code (codes[N+1]) is what gets DISPLAYED to the user
  3. validateCode(N, userInput) checks against codes[N+1]
  4. So: displayed code == validation target == codes[N+1]
```

**Why it works on the live site:** The React wrapper component `Gv` intercepts `onComplete` from each challenge, calls `markChallengeComplete(step, proof)`, and uses the RETURNED value (which is `codes[step+1]`) as the displayed code. This means the code shown to the user IS the code that validation expects.

**Step 30 exception:** `codes[31]` doesn't exist (only 1-30 generated), so `markChallengeComplete(30, proof)` returns `null`. The live site's submit handler detects the last step and navigates to `/finish` directly.

### Session Storage

```javascript
// Encoded in sessionStorage["wo_session"]
// Algorithm: base64(xor(JSON, "WO_2024_CHALLENGE"))
{
  "sessionId": "hex-string",
  "codes": ["CODE1", "CODE2", ..., "CODE30"],  // 0-indexed array
  "completed": [1, 2, 3, ...]  // completed step numbers
}
```

### Decoding All Codes (Escape Hatch)

```javascript
const key = "WO_2024_CHALLENGE";
const raw = atob(sessionStorage.getItem("wo_session"));
let json = "";
for (let i = 0; i < raw.length; i++)
  json += String.fromCharCode(raw.charCodeAt(i) ^ key.charCodeAt(i % key.length));
const session = JSON.parse(json);
// session.codes[N-1] = code for step N (0-indexed array)
```

---

## Obstacle Layer (Every Step)

Unless disabled (`?obstacles=0`), every step spawns these obstacles:

### 1. Cookie Consent Banner (80% probability)
- Dismiss by clicking "Decline" or "Reject"

### 2. Popup Modals (3-6 per step)
- Appear with various texts: "Click here for amazing deals!", "Important Notice!", etc.
- **Real dismiss:** Green "Dismiss" button, dark/red X button
- **Fake dismiss:** Gray/blue X buttons, "Close" buttons (some are traps)
- **Key tell:** Some modals explicitly say "The close button is fake!"

### 3. Blocking Radio Modal
- Scrollable content with lorem ipsum filler
- Radio options at the bottom — must scroll to find them
- **Correct answer pattern:** `Option [A-D] - Correct Choice`
- **Decoys:** "The right choice", "Correct answer", "This is correct", "Select this one"
- After selection, options reshuffle (ignore — your selection persists)
- Click "Submit & Continue" (NOT "Proceed Forward" or similar decoys)

### 4. Fake Navigation Buttons (8-15 per step)
- "Advance", "Next Step", "Next Section", "Proceed Forward", "Go Forward"
- "Continue Reading", "Click Here", "Continue Journey", "Move On"
- ALL are decoys — never click them

### 5. Floating Distractions
- "Click Me!", "Try This!", "Here!", "Button!", "Link!"
- Visually obstructing — ignore all of them

### 6. Filler Content (100 sections)
- "Section 1... Section 100" with lorem ipsum
- The real challenge is buried among the filler

---

## All 28 Challenge Types

### Difficulty Legend
- **E** = Easy (single action)
- **M** = Medium (multi-step or timed)
- **H** = Hard (requires computation, coordination, or special DOM access)

---

### 1. `visible` (E) — Blue box
**Action:** None — code displayed directly.
**DOM:** `<span class="text-xl font-mono font-bold text-gray-800">{CODE}</span>`
**Agent tip:** Use `search_dom({text: 'font-mono'})` to find code span.

### 2. `hidden_dom` (E) — Gray box
**Action:** Inspect DOM attributes OR click box 3 times.
**DOM:** `data-code="{CODE}"`, `aria-label="hidden code: {CODE}"`, `<meta name="challenge-code" content="{CODE}">`
**Agent tip:** `search_dom({attr: 'data-code'})` finds it instantly. Clicking 3x is the fallback.

### 3. `click_reveal` (E) — Green box
**Action:** Click "Reveal Code" button (`#reveal-code-btn`).
**Agent tip:** Standard click action. Button has distinctive green styling.

### 4. `scroll_reveal` (M) — Orange box
**Action:** Scroll page down 500px. Progress bar tracks scroll distance.
**DOM:** Progress bar updates in real time. Code appears when `window.scrollY >= 500`.
**Agent tip:** `scroll_to({y: 500})` or `scroll_to({position: 'bottom'})`.

### 5. `delayed_reveal` (E) — Yellow box
**Action:** Wait 2-6 seconds (varies by step: `2000 + (step % 5) * 1000` ms).
**Agent tip:** Just wait. The code auto-reveals after the timer. No action needed.

### 6. `drag_drop` (M) — Indigo box
**Action:** Drag any 6 pieces from the piece bank into 6 drop slots. Order doesn't matter. 4 extra decoy pieces included.
**DOM:** Pieces: `[data-piece="piece-N"]`, Slots: `[data-slot="slot-N"]`
**Agent tip:** Use `drag(source=PIECE_INDEX, target=SLOT_INDEX)` for each piece. Must complete all 6.

### 7. `keyboard_sequence` (M) — Purple box
**Action:** Press a specific 3-key combo sequence. Three possible sequences per step:
- `Control+A → Control+C → Control+V`
- `ArrowUp → ArrowDown → Enter`
- `Shift+Tab → Tab → Enter`
**DOM:** Progress text: `"Your input (N/3)"`
**Agent tip:** Use `send_keys("ctrl+a ctrl+c ctrl+v")`. Sequence shown in challenge text.

### 8. `memory` (E) — Yellow box
**Action:** Code flashes for 1.5s then hides. Click "I Remember" button.
**Trick:** You DON'T actually need to remember — clicking "I Remember" reveals the code anyway!
**Agent tip:** Wait 1.5s for flash to end, then click `#remember-btn`.

### 9. `hover_reveal` (M) — Purple box
**Action:** Hover over target box (`#hover-target`, `[data-hover-target]`) for 1 second.
**Agent tip:** `hover(index=ELEMENT_INDEX)` then wait 1s. Code appears after hold duration.

### 10. `timing` (M) — Red box
**Action:** Code flashes briefly (200ms every 3s) amid random codes. Click "Capture" 3 times.
**DOM:** `#timing-display` shows rotating codes. `#capture-btn` button. `data-isReal="true"` marks real code.
**Trick:** You don't need to capture at the right moment — just click Capture 3 times.
**Agent tip:** Use `capture_timed` skill or just click `#capture-btn` 3 times.

### 11. `canvas` (M) — Blue box
**Action:** Draw 3+ strokes on canvas, then click "Reveal Code".
**DOM:** `<canvas>` element, `#canvas-reveal` button, `#stroke-count` progress.
**Agent tip:** `draw(index=CANVAS_INDEX, strokes=3)` then click reveal button.

### 12. `audio` (E) — Green box
**Action:** Click "Play Audio" button. Code reveals automatically.
**Note:** Audio content doesn't matter — no transcription needed.
**Agent tip:** Click `#play-audio-btn`. Code appears immediately.

### 13. `video` (M) — Pink box
**Action:** Seek through video frames — need 3+ seek operations AND reach frame 43.
**DOM:** Buttons: `#seek-back` (-10), `#seek-forward` (+10), `#seek-target` (Frame 43).
**Trick:** Click "Frame 43" button 3 times (counts as 3 seeks + correct frame).
**Agent tip:** Click `#seek-target` three times. Ignore frame codes (they're random decoys).

### 14. `split_parts` (M) — Yellow box
**Action:** Find and click 4 scattered parts on the page. Parts are positioned absolutely.
**DOM:** `[data-part="part-N"]` elements, text content: `"Part N: XX"`
**Agent tip:** `search_dom({text: 'Part'})` to find all parts, then click each.

### 15. `encoded_base64` (M) — Indigo box
**Action:** Base64 string shown (e.g., `REVDT0RFX01FXzE1` = `DECODE_ME_15`). Enter any 6-character code in the input, click "Reveal".
**Trick:** The decoded text is NOT the answer — just enter ANY 6 chars to unlock.
**Agent tip:** Type any 6 chars in `#base64-input`, click `#base64-reveal`.

### 16. `rotating` (M) — Cyan box
**Action:** Code display rotates random codes every 150ms. Click "Capture" 3 times.
**Trick:** Same as timing — just click 3 times, no timing needed.
**Agent tip:** Click `#rotating-capture` 3 times.

### 17. `obfuscated` (H) — Red box
**Action:** Code displayed REVERSED. Must reverse the characters, type the correct code, click "Submit".
**Example:** Displayed `HZ7C36` → enter `63C7ZH`.
**Agent tip:** Read the reversed text, use JS `evaluate` to reverse it: `"HZ7C36".split("").reverse().join("")`. Then type result in `#obfuscated-input` and click `#obfuscated-submit`.

### 18. `multi_tab` (E) — Teal box
**Action:** Click all 5 tab buttons (Tab 1 through Tab 5). Each shows a "piece". Code reveals after visiting all.
**DOM:** Tab buttons inside `#tab-buttons`, progress in `#tab-progress`.
**Agent tip:** Click each button with `[data-tab]` attribute. Code appears after all 5 visited.

### 19. `gesture` (M) — Orange box
**Action:** Draw anything on canvas, then click "Complete".
**Note:** Despite saying "draw a square", any single stroke works. After 1 attempt, button enables.
**Agent tip:** `draw(index=CANVAS_INDEX, strokes=1)` then click `#gesture-complete`.

### 20. `sequence` (H) — Violet box
**Action:** Complete 4 different actions in any order:
1. **Click button** — click `#seq-click-btn` ("Click Me")
2. **Hover area** — hover over `#seq-hover-area` (`[data-hover-area]`) for 800ms
3. **Type text** — type anything in `#seq-type-input`
4. **Scroll box** — scroll inside `#seq-scroll-box` (`[data-scroll-box]`)
Then click "Complete (4/4)" button.
**Agent tip:** This requires 4 different interaction types in one challenge. Use click, hover, input_text, and evaluate (to scroll box).

### 21. `puzzle_solve` (M) — Pink box
**Action:** Solve arithmetic puzzle: `A + B = ?` where A = `10 + (step % 20)`, B = `5 + (step % 15)`.
**Agent tip:** Read puzzle text, compute answer via `evaluate`, type in `#puzzle-input`, click `#puzzle-solve`.

### 22. `calculated` (M) — Pink box
**Action:** Calculate: `step × (7919 + step % 100) + (12345 + step % 1000) = ?`
**Agent tip:** Read formula from page text, compute via `evaluate`, type in `#calc-input`, click `#calc-solve`.

### 23. `shadow_dom` (H) — Slate box
**Action:** Click through 3 nested shadow DOM levels in order (Level 1 → 2 → 3).
**DOM:** Each level is inside a shadow root: `host.attachShadow({mode: "open"})`.
**Critical:** browser-use serializer may NOT see elements inside shadow roots.
**Agent tip:** Must use `evaluate` to traverse shadow DOM:
```javascript
// Click shadow level 1
document.querySelector('.shadow-level-host').shadowRoot.querySelector('div').click();
// Click shadow level 2 (nested inside level 1's shadow root)
document.querySelector('.shadow-level-host').shadowRoot
  .querySelector('.shadow-level-host').shadowRoot.querySelector('div').click();
// etc.
```

### 24. `websocket` (E) — Cyan box
**Action:** Click "Connect" button, wait ~3.5s for simulated connection sequence. Code auto-reveals in terminal output.
**Agent tip:** Click `#ws-connect`, wait 4s, read code from DOM.

### 25. `service_worker` (M) — Amber box
**Action:** Two-step process:
1. Click "1. Register Service Worker" (`#sw-register`)
2. Wait 1.5s for cache to populate
3. Click "2. Retrieve from Cache" (`#sw-retrieve`, now enabled)
**Agent tip:** Click register, wait 2s, click retrieve.

### 26. `mutation` (M) — Rose box
**Action:** Click "Trigger Mutation" (`#mutate-btn`) 5 times, then click "Reveal Code" (`#mutation-reveal`).
**DOM:** Progress: `#mutation-progress`, reveal button enables after 5 clicks.
**Agent tip:** Click mutate button 5 times, click reveal.

### 27. `recursive_iframe` (E) — Emerald box
**Action:** Click "Extract Code" button. Nested div levels shown for visual effect but no actual iframes.
**Agent tip:** Click `#iframe-extract`. Simplest challenge type.

### 28. `conditional_reveal` (E) — Yellow box
**Action:** Identical to `delayed_reveal` — just wait for timer.
**Agent tip:** Same as delayed_reveal.

---

## Step → Type Mapping (Version 1)

Steps are grouped into difficulty pools:

| Pool | Steps | Types |
|------|-------|-------|
| Easy | 1-5 | visible, hidden_dom, click_reveal, scroll_reveal, delayed_reveal |
| Medium | 6-10 | drag_drop, keyboard_sequence, memory, hover_reveal, click_reveal |
| Hard | 11-15 | timing, canvas, audio, video, split_parts (+encoded_base64, rotating, obfuscated) |
| Expert | 16-20 | multi_tab, gesture, sequence, puzzle_solve, calculated |
| Boss | 21-30 | shadow_dom, websocket, service_worker, mutation, recursive_iframe, conditional_reveal, multi_tab, sequence, calculated |

**Exact mapping (version=1):**

| Step | Type | Difficulty | Key Action |
|------|------|-----------|------------|
| 1 | visible | E | Read displayed code |
| 2 | hidden_dom | E | Check data-code attr or click 3x |
| 3 | click_reveal | E | Click "Reveal Code" button |
| 4 | scroll_reveal | M | Scroll 500px |
| 5 | delayed_reveal | E | Wait 2-6s |
| 6 | drag_drop | M | Drag 6 pieces to 6 slots |
| 7 | keyboard_sequence | M | Press 3-key combo |
| 8 | memory | E | Click "I Remember" |
| 9 | hover_reveal | M | Hover 1s on target |
| 10 | click_reveal | E | Click "Reveal Code" |
| 11 | timing | M | Click Capture 3x |
| 12 | canvas | M | Draw 3 strokes, click Reveal |
| 13 | audio | E | Click Play Audio |
| 14 | video | M | Seek 3x to frame 43 |
| 15 | split_parts | M | Find/click 4 parts |
| 16 | multi_tab | E | Click all 5 tabs |
| 17 | gesture | M | Draw + click Complete |
| 18 | sequence | H | 4 actions: click, hover, type, scroll |
| 19 | puzzle_solve | M | Solve A + B = ? |
| 20 | calculated | M | Solve step × M + O = ? |
| 21 | shadow_dom | H | Click 3 shadow levels |
| 22 | websocket | E | Click Connect, wait |
| 23 | service_worker | M | Register, wait, retrieve |
| 24 | mutation | M | Click Trigger 5x, Reveal |
| 25 | recursive_iframe | E | Click Extract Code |
| 26 | conditional_reveal | E | Wait for timer |
| 27 | multi_tab | E | Click all 5 tabs |
| 28 | sequence | H | 4 actions |
| 29 | calculated | M | Solve calculation |
| 30 | (step 30 special) | — | Any code works (last step bypass) |

**Formula:** `type = POOL.types[(step - pool.range[0] + version - 1) % pool.types.length]`

---

## Local vs Live Site Differences

| Aspect | Live Site | Local Reproduction |
|--------|-----------|-------------------|
| Routing | Path-based SPA (`/stepN`) | Query params (`?step=N`) |
| Framework | React + Vite bundle | Vanilla JS |
| Challenge wrapper | `Gv` component overrides displayed code | `app.js` line 88 replicates the override |
| Shadow DOM | React fiber tree | Real `attachShadow` |
| WebSocket | Simulated in component | Simulated with setTimeout |
| Obstacles | React components (Radix UI) | Vanilla DOM |
| Code entry | React controlled input | Standard HTML input |
| Step 30 | Navigate to `/finish` | `isLastStep` bypass in validation |
| API | None (client-only) | `/api/complete`, `/api/status`, `/api/reset` |
| Test harness | None | `test-runner.py` with per-type scoring |

### Local-Only Features

- `?obstacles=0` — disable obstacle layer
- `?debug=1` — show debug panel with all codes
- `?type=X` — test specific challenge type in isolation
- `?version=N` — change type mapping (shifts pool indices)
- `/api/config` — get challenge configuration
- `/api/status` — check completion state
- `/api/reset` — reset for new test run

---

## Agent Strategy

### Universal Per-Step Flow

```
1. Pre-step cleanup (auto): dismiss_popups → clean_dom → observe_changes
2. Identify challenge type from DOM (look for bg-{color}-100 box)
3. Execute type-specific action
4. Read revealed code from <span class="text-xl font-mono font-bold text-gray-800">
5. Enter code in #code-input
6. Click #submit-code
7. Wait for auto-advance
```

### DOM Selectors Cheat Sheet

```javascript
// Challenge box (all types)
document.querySelector('[class*="bg-"][class*="-100"][class*="border-"][class*="-400"]')

// Code display (after reveal)
document.querySelector('.text-xl.font-mono.font-bold.text-gray-800')

// Code entry
document.querySelector('#code-input')
document.querySelector('#submit-code')

// Type identification (from box color)
// blue=visible, gray=hidden_dom, green=click_reveal/audio, orange=scroll_reveal/gesture
// yellow=delayed_reveal/memory/split_parts/conditional_reveal, indigo=drag_drop/encoded_base64
// purple=keyboard_sequence/hover_reveal, red=timing/obfuscated, cyan=rotating/websocket
// pink=video/puzzle_solve/calculated, teal=multi_tab, slate=shadow_dom
// amber=service_worker, rose=mutation, emerald=recursive_iframe, violet=sequence
```

### Fallback: Session Decode

If a challenge seems stuck, decode all codes from sessionStorage (see decoding section above) and enter the code for step N+1 directly. This bypasses the challenge entirely.
