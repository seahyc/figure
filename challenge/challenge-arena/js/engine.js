/**
 * Challenge Engine — session management, code generation, validation.
 * Mirrors the original site's logic exactly.
 */

const TOTAL_STEPS = 30;
const XOR_KEY = "WO_2024_CHALLENGE";
const CODE_CHARSET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

// ---------------------------------------------------------------------------
// XOR encode/decode (symmetric)
// ---------------------------------------------------------------------------
function xorCrypt(str) {
  let out = "";
  for (let i = 0; i < str.length; i++) {
    out += String.fromCharCode(str.charCodeAt(i) ^ XOR_KEY.charCodeAt(i % XOR_KEY.length));
  }
  return out;
}

// ---------------------------------------------------------------------------
// Random code generation
// ---------------------------------------------------------------------------
function generateCode() {
  const bytes = new Uint8Array(6);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, b => CODE_CHARSET[b % CODE_CHARSET.length]).join("");
}

function generateSessionId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
}

// ---------------------------------------------------------------------------
// Step → challenge type mapping (version-dependent, matches original)
// ---------------------------------------------------------------------------
const POOLS = [
  { range: [1, 5],   types: ["visible", "hidden_dom", "click_reveal", "scroll_reveal", "delayed_reveal"] },
  { range: [6, 10],  types: ["drag_drop", "keyboard_sequence", "memory", "hover_reveal", "click_reveal"] },
  { range: [11, 15], types: ["timing", "canvas", "audio", "video", "split_parts", "encoded_base64", "rotating", "obfuscated"] },
  { range: [16, 20], types: ["multi_tab", "gesture", "sequence", "puzzle_solve", "calculated"] },
  { range: [21, 30], types: ["shadow_dom", "websocket", "service_worker", "mutation", "recursive_iframe", "conditional_reveal", "multi_tab", "sequence", "calculated"] },
];

function getChallengeType(step, version = 1) {
  for (const pool of POOLS) {
    if (step >= pool.range[0] && step <= pool.range[1]) {
      const idx = (step - pool.range[0] + version - 1) % pool.types.length;
      return pool.types[idx];
    }
  }
  return "visible";
}

// Minimum completion times per type (ms) — warning only, not enforced
const MIN_TIMES = {
  visible: 500, hidden_dom: 1000, click_reveal: 500, hover_reveal: 300,
  scroll_reveal: 1000, delayed_reveal: 2000, drag_drop: 2000,
  keyboard_sequence: 1500, memory: 3000, timing: 3000, canvas: 3000,
  audio: 2000, video: 2000, multi_tab: 3000, gesture: 2000,
  sequence: 3000, shadow_dom: 2000, websocket: 2000, service_worker: 2000,
  mutation: 2000, recursive_iframe: 2000, encoded_base64: 1000,
  obfuscated: 1000, rotating: 1000, puzzle_solve: 2000, split_parts: 1500,
  calculated: 1000, conditional_reveal: 2000,
};

// ---------------------------------------------------------------------------
// Session class
// ---------------------------------------------------------------------------
class ChallengeSession {
  constructor() {
    this.sessionId = null;
    this.codes = new Map();          // step (1-based) → 6-char code
    this.completed = new Set();
    this.startTimes = new Map();     // step → epoch ms
  }

  init() {
    // Try to restore from sessionStorage
    const stored = sessionStorage.getItem("wo_session");
    if (stored) {
      try {
        const json = JSON.parse(xorCrypt(atob(stored)));
        this.sessionId = json.sessionId;
        json.codes.forEach((c, i) => this.codes.set(i + 1, c));
        json.completed.forEach(s => this.completed.add(s));
        return;
      } catch (_) { /* corrupt — regenerate */ }
    }
    this.sessionId = generateSessionId();
    for (let i = 1; i <= TOTAL_STEPS; i++) this.codes.set(i, generateCode());
    this.completed.clear();
    this._persist();
  }

  _persist() {
    const payload = JSON.stringify({
      sessionId: this.sessionId,
      codes: Array.from({ length: TOTAL_STEPS }, (_, i) => this.codes.get(i + 1) || ""),
      completed: Array.from(this.completed),
    });
    sessionStorage.setItem("wo_session", btoa(xorCrypt(payload)));
  }

  getCode(step) {
    if (step === 1) return this.codes.get(1) || null;
    return this.completed.has(step - 1) ? (this.codes.get(step) || null) : null;
  }

  startChallenge(step) {
    this.startTimes.set(step, Date.now());
  }

  markComplete(step, proof) {
    const start = this.startTimes.get(step) || Date.now();
    const elapsed = (proof?.timestamp || Date.now()) - start;
    const challengeType = getChallengeType(step);
    const minTime = MIN_TIMES[challengeType] || 500;
    if (elapsed < minTime) {
      console.warn(`Step ${step} completed too quickly: ${elapsed}ms < ${minTime}ms`);
    }
    if (!proof?.type || !proof?.timestamp || !proof?.data) {
      console.warn("Invalid proof", proof);
      return null;
    }
    this.completed.add(step);
    this._persist();
    return this.codes.get(step + 1) || null;
  }

  // Validate code entry — matches original's off-by-one: checks codes[step+1]
  validateCode(step, userCode) {
    const expected = this.codes.get(step + 1);
    if (!expected) return false;
    return userCode.toUpperCase() === expected.toUpperCase();
  }

  getStepConfig(step, version = 1) {
    const type = getChallengeType(step, version);
    return { step, type, version, code: this.codes.get(step) };
  }
}

// ---------------------------------------------------------------------------
// Singleton + exports
// ---------------------------------------------------------------------------
const session = new ChallengeSession();

window.ChallengeEngine = {
  TOTAL_STEPS,
  session,
  getChallengeType,
  MIN_TIMES,
  init: () => session.init(),
  startChallenge: (s) => session.startChallenge(s),
  markComplete: (s, p) => session.markComplete(s, p),
  validateCode: (s, c) => session.validateCode(s, c),
  getCode: (s) => session.getCode(s),
  getStepConfig: (s, v) => session.getStepConfig(s, v),
  getAllCodes: () => Array.from({ length: TOTAL_STEPS }, (_, i) => ({ step: i + 1, code: session.codes.get(i + 1) })),
};

export default window.ChallengeEngine;
