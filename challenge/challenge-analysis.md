# Figure Browser Navigation Challenge Analysis

**Source:** https://serene-frangipane-7fd25b.netlify.app
**Tweet:** https://x.com/adcock_brett/status/2018417226895028414
**Offer:** $500k/year + millions in equity for Computer-Use team at Figure (AI robotics)

## Challenge Overview

30 steps of browser navigation challenges designed to test AI agent capabilities. Must complete all 30 in under 5 minutes.

## Challenge Types Observed

### 1. Modal Dismissal
**Pattern:** Multiple overlapping popups appear that must be dismissed before accessing content.

**Modal Types:**
- Cookie consent (Accept/Decline buttons)
- "Click here for amazing deals!" (Close button + X)
- "Important Notice!" (Close button)
- "Limited time offer!" (Close button + X)
- "Newsletter Signup" (Close button)
- "Please Select an Option" (scrollable modal with radio buttons)

**Strategy:** Click all Close/X buttons. Can be done in parallel or with brute-force clicking.

### 2. Radio Button Selection (Scrollable Modal)
**Pattern:** Modal with radio buttons, must scroll to see all options and select the correct one.

**Correct Answer Pattern:** `Option [A-Z] - Correct Choice`
- Step 1: "Option B - Correct Choice"
- Step 2: "Option C - Correct Choice"
- Step 3: "Option D - Correct Choice"

**Decoy Options (DO NOT SELECT):**
- "Correct answer"
- "This is correct"
- "The right choice"
- "Select this one"
- "Choose me"
- "Pick this option"
- "Wrong option 1/2/3"
- "Not this one"
- "Incorrect choice"
- Plain "Option A/B/C/D" (without "- Correct Choice")

**Strategy:** Regex match for `/Option [A-Z] - Correct Choice/`

### 3. Delayed Code Reveal
**Pattern:** Code appears after a delay (e.g., 6 seconds), must wait then enter it.

**Example (Step 4):**
- "Delayed Reveal: The code will appear after waiting 6 seconds..."
- Code revealed: `WWMR49`
- Enter in "6-character code" field
- Click "Submit Code"

**Strategy:** Wait for element with code to appear, extract 6-char alphanumeric string.

### 4. Hidden "Reveal Code" Button
**Pattern:** A button labeled "Reveal Code" or "Click to Reveal" that shows the 6-character code.

**DOM clue:** `button "Reveal Code"` or `generic "Click to Reveal:"`

**Strategy:** Find and click reveal button, then extract and enter the code.

### 5. Filler Content / Scroll Challenge
**Pattern:** 100 sections of "This is filler content. Keep scrolling to find the navigation button."

**Strategy:** Press End key or scroll to bottom quickly. The real navigation is often at the very end.

### 6. Decoy Navigation Buttons
**Pattern:** Many buttons that look like navigation but just scroll the page:
- "Next Page", "Next Step", "Next Section"
- "Continue", "Continue Reading", "Continue Journey"
- "Move On", "Go Forward", "Proceed", "Proceed Forward"
- "Advance", "Keep Going", "Click Here"

**Strategy:** These are distractions. The real navigation is:
1. Submit the correct code in the code entry field, OR
2. Complete the modal challenge (radio selection), OR
3. Find the actual navigation button (may require scrolling to bottom)

### 7. Floating Distractions
**Pattern:** Animated/positioned elements obscuring the UI:
- Orange/gradient boxes with text: "Try This!", "Here!", "Button!", "Link!", "Click Me!", "Click Here!"
- These float over content and are clickable but don't advance the challenge

**Strategy:** Ignore these. Target elements by DOM reference, not visual position.

### 8. Dynamic Content Loading
**Pattern:** Content appears at different times after page load:
- "This content appeared 500ms after page load"
- "This content appeared 1500ms after page load"
- "This content appeared 2500ms after page load"
- "This content appeared 3500ms after page load"

**Strategy:** Wait for DOM to stabilize before taking action. Use MutationObserver or polling.

## DOM Patterns for Solution

### Finding the Correct Radio Button
```javascript
// Look for radio with exact pattern
const correctRadio = Array.from(document.querySelectorAll('[role="radio"]'))
  .find(r => /Option [A-Z] - Correct Choice/.test(r.textContent));
```

### Finding the Code Entry Field
```javascript
const codeInput = document.querySelector('input[placeholder*="6-character"]');
const submitButton = document.querySelector('button[type="submit"]');
```

### Finding Reveal Code Button
```javascript
const revealBtn = Array.from(document.querySelectorAll('button'))
  .find(b => b.textContent.includes('Reveal'));
```

### Dismissing Modals
```javascript
// Click all close buttons
document.querySelectorAll('button').forEach(b => {
  if (b.textContent === 'Close' || b.textContent === 'Decline') {
    b.click();
  }
});
// Click all X buttons (usually have aria-label or are small)
```

## Recommended Architecture

Based on analysis, the optimal solution architecture:

```
┌─────────────────────────────────────────────────────┐
│  ORCHESTRATOR (Opus 4.5) - Background               │
│  - Maintains state across levels                    │
│  - Decides strategy per level type                  │
│  - Accumulates skills/patterns                      │
└─────────────────────────────────────────────────────┘
                          ▲
                          │ skills / observations
┌─────────────────────────┴───────────────────────────┐
│  FAST EXECUTOR (Gemini Flash / Haiku)               │
│  - DOM parsing (non-visual, accessibility tree)     │
│  - Pattern matching for known challenge types       │
│  - Rapid action execution                           │
│  - Report observations back to orchestrator         │
└─────────────────────────────────────────────────────┘
```

### Key Optimizations

1. **DOM over Vision** - Use accessibility tree / DOM parsing instead of screenshots
2. **Pattern Recognition** - Classify challenge type immediately, apply known solution
3. **Parallel Exploration** - Map action space with multiple fast agents
4. **Skill Caching** - Once a modal type is solved, replay solution instantly
5. **Minimal Waits** - Only wait when necessary (delayed reveals)

### Metrics to Track
- Time per level
- Token usage per level
- Total token cost
- Success rate per challenge type

### 9. Drag and Drop
**Pattern:** Elements that must be dragged to a target location.

**Details:** (To be filled in)
- Source element:
- Target element:
- Visual cues:

**Strategy:** Use mouse drag actions (mousedown → mousemove → mouseup) or HTML5 drag API.

### 10. Keyboard Sequence Challenge
**Pattern:** Press specific key combinations in sequence to reveal a code.

**Example:**
```
Required sequence: Control+A → Control+C → Control+V
Your input (2/3): [shows what you've pressed so far]
```

**Key combinations seen:**
- Control+A (Select All)
- Control+C (Copy)
- Control+V (Paste)

**Strategy:**
- Parse the required sequence from DOM
- Send keyboard events with modifiers: `key` action with "ctrl+a", "ctrl+c", "ctrl+v"
- Must press in exact order

**Gotcha:** The UI shows what you've pressed - if wrong keys are detected (like Shift+Shift), need to reset or continue with correct keys.

### 11. Hover to Reveal
**Pattern:** Must hover over an element to reveal hidden content (likely the code).

**Strategy:** Use `hover` action to move mouse to element without clicking. May need to hold position for a duration.

### 12. Drawing Challenge
**Pattern:** Canvas-based drawing - likely need to draw a shape or path.

**Possible patterns:**
- Draw a specific shape (circle, square, line)
- Connect dots in order
- Trace a path
- Free-form drawing that's validated

**Strategy:** Use `left_click_drag` with start_coordinate and end coordinate. May need multiple drag operations for complex shapes.

### 13. [Additional Challenge Types]
(Space for more patterns discovered during manual exploration)

---

## Full Challenge Walkthrough (Steps 1-30)

| Step | Challenge Type | Solution Pattern | Notes |
|------|---------------|------------------|-------|
| 1 | Modal + Radio | Dismiss modals, select "Option B - Correct Choice" | |
| 2 | Modal + Radio | Select "Option C - Correct Choice" | |
| 3 | Modal + Radio | Select "Option D - Correct Choice" | |
| 4 | Delayed Code Reveal | Wait 6s, enter WWMR49 | Floating distractions |
| 5-30 | TBD | (Fill in after manual exploration) | |

---

## Notes

- Brute-force clicking works but likely not the "spirit" of the challenge
- The challenge rewards generalization over special-casing
- No penalty observed for wrong clicks (clicking decoy buttons just scrolls)
- Each level has a `?version=1` parameter - may randomize on different versions
