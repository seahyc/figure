---
name: page_assist
type: script
description: >
  Webpage skill that handles common interaction patterns: clicks action buttons,
  manages progress sequences, extracts codes, and submits them. Runs before the
  LLM sees the page to reduce round-trips. Generalizable to arbitrary web tasks.
---

# Page Assist

Recognizes common web interaction patterns and attempts to progress them
automatically before the LLM processes the page. This reduces the number
of LLM round-trips needed per step.

## What It Does
1. Clicks action buttons (Reveal, Play, Start, Show, etc.)
2. Clicks progress buttons repeatedly when (N/M) progress indicators are visible
3. Clicks sequential navigation elements (Tab 1, Tab 2, etc.)
4. Detects countdowns/timers on the page
5. Handles drag-and-drop challenges (fires drop events on empty slots)
6. Handles gesture challenges (draws a stroke on canvas + clicks Complete)
7. Handles hidden DOM click patterns (clicks cursor-pointer N times)
8. Handles scroll-reveal challenges
9. Finds codes from: data attrs, meta tags, aria-labels, hidden elements,
   HTML comments, title attrs, CSS pseudo-elements, styled text
10. **Submits codes**: types found code and clicks Submit Code
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
- `autoSubmit` (boolean): Submit found codes. Default: true

## Examples
```js
// Run full page assist with all patterns + code submission
window.__skills.page_assist()

// Only click action buttons, no submission
window.__skills.page_assist({autoSubmit: false, clickProgressButtons: false})

// Disable DOM injection (for manual inspection)
window.__skills.page_assist({reportResults: false})
```
