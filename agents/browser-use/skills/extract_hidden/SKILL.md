---
name: extract_hidden
type: script
description: Extract hidden or hard-to-find text content from the page (data attributes, hidden elements, CSS pseudo-elements, HTML comments, etc.)
---

# Extract Hidden Content

Searches the entire page for text content that may be hidden or embedded in non-obvious locations. Useful when visible text doesn't contain what you're looking for.

## Usage

```javascript
var result = window.__skills.extract_hidden({ pattern: '[A-Z0-9]{6}', maxResults: 20 });
var data = JSON.parse(result);
// data.matches - array of {text, source, selector, visible}
// data.summary - human-readable summary
```

## Parameters

- `pattern` (string, optional): Regex pattern to filter results. If not provided, returns all found hidden text.
- `maxResults` (number, default 20): Maximum matches to return
- `searchAreas` (array, optional): Limit search to specific areas: "data_attrs", "hidden_elements", "css_content", "comments", "meta", "scripts", "noscript". Default: all.

## Search Areas

1. **data_attrs**: All `data-*` attributes on all elements
2. **hidden_elements**: Text in elements with `display:none`, `visibility:hidden`, `opacity:0`
3. **css_content**: CSS `::before`/`::after` `content` properties
4. **comments**: HTML comment nodes (`<!-- ... -->`)
5. **meta**: Meta tags, title attributes, aria-label, alt text
6. **scripts**: JSON data embedded in script tags (type="application/json", etc.)
7. **noscript**: Content inside `<noscript>` tags
8. **aria**: aria-describedby, aria-details referenced content
