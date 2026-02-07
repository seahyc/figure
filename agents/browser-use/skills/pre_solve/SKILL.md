---
name: pre_solve
type: script
description: >
  Automated pattern handler that clicks action buttons, handles progress
  sequences, finds codes, and auto-submits them. Runs before LLM sees the
  page to reduce round-trips. Works on both local server and live Netlify site.
---

# Pre-Solve

Recognizes common web interaction patterns and attempts to progress them
automatically before the LLM processes the page. This reduces the number
of LLM round-trips needed per step.

## What It Does
1. Clicks action buttons (Reveal, Play, Start, Show, etc.)
2. Clicks progress buttons repeatedly when (N/M) progress indicators are visible
3. Clicks sequential navigation elements (Tab 1, Tab 2, etc.)
4. Detects countdowns/timers on the page
5. Auto-solves drag_drop challenges (fires drop events on empty slots)
6. Auto-solves gesture challenges (draws a stroke on canvas + clicks Complete)
7. Auto-solves hidden_dom click variant (clicks cursor-pointer N times)
8. Auto-scrolls for scroll_reveal challenges
9. Finds codes from: data attrs, meta tags, aria-labels, hidden elements,
   HTML comments, title attrs, CSS pseudo-elements, styled text
10. **Auto-submits**: types found code and clicks Submit Code
    - Supports both ID-based (#code-input/#submit-code) and text-based selectors
    - Handles disabled submit button (React state update enables it)

## What It Does NOT Do (left to LLM)
- Shadow DOM, canvas (non-gesture), keyboard sequences
- Video/audio challenges, sequence challenges
- Math or string manipulation (puzzle_solve, calculated)
- Hover-reveal challenges (needs sustained mouseover)
- Mutation observer challenges
- Encoded/base64 decoding

## Parameters
- `clickActionButtons` (boolean): Click buttons with action verbs. Default: true
- `clickProgressButtons` (boolean): Click progress-advancing buttons. Default: true
- `clickSequentialNav` (boolean): Click numbered tabs/sections. Default: true
- `waitForCountdowns` (boolean): Detect timers. Default: true
- `reportResults` (boolean): Inject results div into DOM. Default: true
- `autoSubmit` (boolean): Auto-submit found codes. Default: true

## Examples
```js
// Run full pre-solve with all patterns + auto-submit
window.__skills.pre_solve()

// Only click action buttons, no auto-submit
window.__skills.pre_solve({autoSubmit: false, clickProgressButtons: false})

// Disable DOM injection (for manual inspection)
window.__skills.pre_solve({reportResults: false})
```
