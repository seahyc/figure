---
name: compute
type: script
description: >
  Evaluate math expressions and perform string operations (reverse, base64,
  hex, ROT13, caesar). Use instead of LLM mental math for accuracy.
---

# Compute

Offloads computation from the LLM to JavaScript for accuracy. Handles
arithmetic evaluation, string manipulation, and encoding/decoding.

## Parameters (pass any combination)
- `math` (string): Arithmetic expression to evaluate. Supports +, -, *, /, ^, (), %.
- `reverse` (string): Reverse a string.
- `decode` (string): Base64 decode.
- `encode` (string): Base64 encode.
- `hexDecode` (string): Hex string to ASCII.
- `rot13` (string): ROT13 cipher.
- `caesar` (string): Caesar cipher with custom shift.
- `caesarShift` (number): Shift amount for caesar (default: 3).
- `upper` (string): Convert to uppercase.
- `lower` (string): Convert to lowercase.
- `length` (string): Get string length.
- `concat` (array): Concatenate array of strings.
- `sort` (string): Sort characters alphabetically.

Returns a JSON string with results for each requested operation.

## Examples
```js
// Evaluate arithmetic
window.__skills.compute({math: "20 * 7939 + 12365"})
// Returns: '{"math":"171145"}'

// Reverse a string
window.__skills.compute({reverse: "HZ7C36"})
// Returns: '{"reverse":"63C7ZH"}'

// Base64 decode
window.__skills.compute({decode: "REVDT0RFX01FXzE1"})
// Returns: '{"decode":"DECODE_ME_15"}'

// Multiple operations at once
window.__skills.compute({math: "256 * 3", reverse: "ABC123", decode: "SGVsbG8="})
// Returns: '{"math":"768","reverse":"321CBA","decode":"Hello"}'
```
