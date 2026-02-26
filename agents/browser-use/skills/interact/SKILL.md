---
name: interact
type: script
description: Generic page interaction - clicks action buttons, fills forms with computed answers, handles drag-drop, canvas, scroll/hover reveals
---

# Interact

Automatic page interaction skill. Analyzes the page structure and performs common interactions:
- Clicks action buttons (Reveal, Show, Start, Play, etc.) while avoiding decoy/navigation buttons
- Solves and fills math expressions into nearby inputs
- Handles drag-and-drop by detecting draggable and droppable elements
- Draws on canvas elements
- Scrolls to reveal hidden content
- Queues click targets for Playwright trusted click execution

## Usage

```javascript
var result = window.__skills.interact({
    clickButtons: true,
    solveForms: true,
    handleDragDrop: true,
    drawCanvas: true,
    scrollReveal: true
});
var data = JSON.parse(result);
// data.actions - what was done
// data.clickTargets - [{x, y, label}] for Playwright
// data.filledFields - fields that were filled
```

## Parameters

- `clickButtons` (boolean, default true): Click action buttons
- `solveForms` (boolean, default true): Find and solve math/puzzles, fill inputs
- `handleDragDrop` (boolean, default true): Execute drag-and-drop
- `drawCanvas` (boolean, default true): Draw on canvas elements
- `scrollReveal` (boolean, default true): Scroll to reveal hidden content
- `avoidPatterns` (array, optional): Regex patterns for button text to avoid clicking
