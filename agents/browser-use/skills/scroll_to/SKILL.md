---
name: scroll_to
type: script
description: >
  Reliably scroll the page to a specific position or element using JavaScript.
  More reliable than browser scroll actions which depend on element indices.
  Use when you need to scroll to top, bottom, or a specific element.
---

# Scroll To

Scrolls the page using `window.scrollTo()` or `element.scrollIntoView()`.
Much more reliable than browser-use's scroll action because it doesn't depend
on element indices which shift as you scroll.

## Parameters
- `position` (string): Scroll to 'top', 'bottom', or omit to use selector/y.
- `selector` (string): CSS selector of element to scroll into view.
- `y` (number): Absolute Y pixel position to scroll to.
- `smooth` (boolean): Use smooth scrolling animation. Default: false.

## Examples
```js
// Scroll to the very top of the page
window.__skills.scroll_to({position: 'top'})

// Scroll to the bottom
window.__skills.scroll_to({position: 'bottom'})

// Scroll to a specific element
window.__skills.scroll_to({selector: '#challenge-area'})

// Scroll to an absolute pixel position
window.__skills.scroll_to({y: 500})
```
