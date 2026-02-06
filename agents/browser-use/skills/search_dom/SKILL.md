---
name: search_dom
type: script
description: >
  Search the DOM for elements matching text content, attributes, or CSS selectors.
  Returns matching elements with their text, tag, class, id, and position.
  Use when you need to find hidden elements, locate specific content, or
  understand page structure without scrolling.
---

# Search DOM

Searches the entire DOM tree for elements matching a text pattern or CSS selector.
Returns structured info about matches including position, visibility, and attributes.
Much faster and more reliable than scrolling through long pages.

## Parameters
- `text` (string): Text pattern to search for (case-insensitive substring match). Mutually exclusive with `selector`.
- `selector` (string): CSS selector to find elements. Mutually exclusive with `text`.
- `maxResults` (number): Maximum matches to return. Default: 20.
- `includeHidden` (boolean): Include elements with display:none or visibility:hidden. Default: true.

## Examples
```js
// Find elements containing specific text
window.__skills.search_dom({text: 'Part'})

// Find elements by CSS selector
window.__skills.search_dom({selector: '[data-hidden]'})

// Find clickable elements with specific text
window.__skills.search_dom({text: 'Reveal'})

// Find hidden elements that might contain codes
window.__skills.search_dom({text: 'code', includeHidden: true})
```
