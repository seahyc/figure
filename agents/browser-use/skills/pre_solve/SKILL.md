---
name: pre_solve
type: script
description: >
  Automated pattern handler that clicks action buttons, handles progress
  sequences, and reports visible codes. Runs before LLM sees the page
  to reduce round-trips.
---

# Pre-Solve

Recognizes common web interaction patterns and attempts to progress them
automatically before the LLM processes the page. This reduces the number
of LLM round-trips needed per step.

## What It Does
1. Clicks action buttons (Reveal, Play, Start, Show, etc.)
2. Clicks progress buttons repeatedly when N/M progress indicators are visible
3. Clicks sequential navigation elements (Tab 1, Tab 2, etc.)
4. Detects countdowns/timers on the page
5. Auto-solves drag_drop challenges (fires drop events on empty slots)
6. Auto-solves gesture challenges (draws a stroke on canvas + clicks Complete)
7. Reports any visible 6-character codes found on the page

## What It Does NOT Do (left to LLM)
- No hardcoded CSS selectors
- Shadow DOM, canvas (non-gesture), keyboard sequences
- Code reading or submission
- Math or string manipulation

## Parameters
- `clickActionButtons` (boolean): Click buttons with action verbs. Default: true
- `clickProgressButtons` (boolean): Click progress-advancing buttons. Default: true
- `clickSequentialNav` (boolean): Click numbered tabs/sections. Default: true
- `waitForCountdowns` (boolean): Detect timers. Default: true
- `reportResults` (boolean): Inject results div into DOM. Default: true

## Examples
```js
// Run full pre-solve with all patterns
window.__skills.pre_solve()

// Only click action buttons
window.__skills.pre_solve({clickProgressButtons: false, clickSequentialNav: false})

// Disable DOM injection (for manual inspection)
window.__skills.pre_solve({reportResults: false})
```
