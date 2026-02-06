---
name: observe_changes
type: script
description: >
  Persistent DOM change observer (auto-started). Logs element adds/removes
  and text changes to window.__domChangeLog. A summary is auto-injected into
  the DOM each step. Call observe_changes() to read the full change summary,
  or use observe_changes({reset: true}) to read and clear. Pair with
  capture_timed to act on fast-changing elements, or view_frames to see
  past screenshots.
---

# Observe Changes

Gives the agent temporal awareness of DOM changes. Since the agent only sees
static snapshots, this skill fills the gap by continuously logging mutations
in the background.

**How it works:**
1. First call: installs MutationObserver on the page (idempotent)
2. Observer logs element adds/removes and text changes to a circular buffer
3. Subsequent calls: return a grouped summary of recent changes
4. Summary shows what changed, how often, and example content

## Parameters
- `scope` (string): CSS selector for subtree to watch. Default: `'body'`.
- `maxBuffer` (number): Max events in circular buffer. Default: 200.
- `reset` (boolean): Clear buffer after reading. Default: false.

## Examples
```js
// Start observing (idempotent — safe to call multiple times)
window.__skills.observe_changes()

// Read recent changes
window.__skills.observe_changes()
// → "15 changes over 4.2s:
//     text:SPAN#timer: 10x (e.g. "2 seconds remaining")
//     added:DIV.code-display: 3x (e.g. "BMQ5PP")
//     removed:DIV.code-display: 2x (e.g. "BMQ5PP")"

// Read and clear buffer
window.__skills.observe_changes({reset: true})

// Access raw log directly
window.__domChangeLog
```

## Workflow with capture_timed
1. Call `observe_changes()` to start observing
2. On next step, call `observe_changes()` again to see what changed
3. Identify fast-changing elements from the summary
4. Use `capture_timed({waitFor: selector, clickSelector: button})` to act on them
