---
name: shadow_dom
type: script
description: >
  Traverse and interact with shadow DOM elements that are invisible to the
  browser-use DOM serializer. Clicks elements inside nested shadow roots.
---

# Shadow DOM

Recursively traverses shadow DOM boundaries to find and click elements that the
browser-use framework cannot see because its DOM serializer doesn't cross
shadow root boundaries.

## Parameters
- `selector` (string): CSS selector for shadow host elements. Default: '.shadow-level-host'
- `action` (string): Action to perform: 'click' (default) or 'list'

## Examples
```js
// Click through all nested shadow DOM levels (default selector)
window.__skills.shadow_dom()

// Use a custom shadow host selector
window.__skills.shadow_dom({selector: '[data-shadow-host]'})

// List shadow DOM elements without clicking
window.__skills.shadow_dom({action: 'list'})
```
