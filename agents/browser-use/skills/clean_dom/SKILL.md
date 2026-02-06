---
name: clean_dom
type: script
description: >
  Hide filler, repetitive, and decorative DOM elements from the agent's
  perception using data-browser-use-exclude attribute. Elements stay in
  the DOM (no side effects) but are invisible to browser-use's serializer.
  Runs automatically before each step via on_step_start hook.
---

# Clean DOM

Marks repetitive and filler elements with `data-browser-use-exclude="true"` so
browser-use's DOM serializer skips them entirely. This reduces what the LLM
perceives without removing elements from the actual DOM.

**Three phases:**
1. **Repetitive text blocks**: Groups elements by normalized text pattern. If 5+
   have the same pattern, keeps first for context, excludes the rest.
2. **Duplicate interactive elements**: Groups buttons/clickable elements by text.
   If 3+ have identical text, keeps first, excludes duplicates.
3. **Off-screen content** (aggressive mode): Excludes non-interactive elements
   far below the viewport.

**Never excludes**: form elements, inputs, shadow DOM hosts, elements containing
form controls, elements with `data-code` or `data-hidden` attributes.

## Parameters
- `aggressive` (boolean): Enable Phase 3 off-screen cleanup. Default: false.
- `keepSelector` (string): CSS selector for elements to always keep. Default: null.
- `minRepeat` (number): Threshold for Phase 1 text deduplication. Default: 5.
- `dedupeInteractive` (boolean): Enable Phase 2 button deduplication. Default: true.
- `interactiveMinRepeat` (number): Threshold for Phase 2. Default: 3.

## Examples
```js
// Standard cleanup (runs automatically via on_step_start)
window.__skills.clean_dom()

// Aggressive cleanup for very bloated pages
window.__skills.clean_dom({aggressive: true})

// Lower threshold for filler detection
window.__skills.clean_dom({minRepeat: 3})

// Keep specific elements during cleanup
window.__skills.clean_dom({keepSelector: '#challenge-area'})
```
