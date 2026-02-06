---
name: dismiss_popups
type: script
description: >
  Dismiss all popup modals, cookie banners, and overlay dialogs
  by clicking common dismiss buttons and hiding overlay elements.
  Use when popups or modals block interaction with page content.
---

# Dismiss Popups

Clicks all common dismiss/close/decline buttons globally, then hides any remaining
fixed-position overlay elements that may still be blocking the page.

## Parameters
- `keywords` (array): Button text keywords to match (case-insensitive exact match).
  Default: ['dismiss', 'close', 'decline', 'reject', 'accept', 'no thanks', 'not now', 'maybe later', 'got it', 'ok']
- `selectors` (array): Additional CSS selectors for close elements.
  Default: ['.close', '.close-btn', '.modal-close', '[class*="close-"]']
- `hideOverlays` (boolean): Also hide remaining fixed/absolute overlays with high z-index.
  Default: true

## Examples
```js
// Dismiss all with defaults
window.__skills.dismiss_popups()

// Custom keywords for a specific site
window.__skills.dismiss_popups({keywords: ['skip', 'later', 'no', 'continue']})

// Only click buttons, don't hide overlays (safer for SPAs)
window.__skills.dismiss_popups({hideOverlays: false})

// Add extra selectors for site-specific close buttons
window.__skills.dismiss_popups({selectors: ['#cookie-close', '.banner-dismiss']})

// Combine options
window.__skills.dismiss_popups({keywords: ['skip ad'], selectors: ['#ad-close'], hideOverlays: true})
```
