---
name: aria_snapshot
type: script
description: Generate compact ARIA-based DOM snapshot with element references for AI-friendly page understanding
---

# ARIA Snapshot

Generates a semantic ARIA tree representation of the current page. More compact than raw HTML — only includes semantic elements with roles, states, and text content.

## Usage

```javascript
var snapshot = window.__skills.aria_snapshot({ maxDepth: 10, maxElements: 500 });
var result = JSON.parse(snapshot);
// result.tree - ARIA tree string
// result.refs - { "ref_1": {tag, role, text, rect}, ... }
// result.stats - { elements, roles, inputs, buttons }
```

## Parameters

- `maxDepth` (number, default 10): Maximum DOM tree depth to traverse
- `maxElements` (number, default 500): Maximum elements to include
- `includeHidden` (boolean, default false): Include hidden elements
- `focusSelector` (string, optional): CSS selector to focus snapshot on a subtree

## Output Format

The tree uses indentation to show nesting, with each line showing:
```
[ref_N] role "text content" {state}
```

Example:
```
[ref_1] navigation
  [ref_2] link "Home"
  [ref_3] link "About"
[ref_4] main
  [ref_5] heading "Welcome" level=1
  [ref_6] form
    [ref_7] textbox "Email" required
    [ref_8] textbox "Password" required
    [ref_9] button "Sign In"
```

Element refs can be used to target elements for interaction without fragile CSS selectors.
