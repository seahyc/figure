/**
 * Challenge type implementations — matches live site DOM patterns exactly.
 * Each factory: createXxx(container, code, stepNum) → { onComplete: Promise<proof> }
 */

// ---------------------------------------------------------------------------
// Type → color mapping (extracted from live site bundle)
// ---------------------------------------------------------------------------
const CHALLENGE_COLORS = {
  visible:           { bg: "blue",    label: "Visible Code:" },
  hidden_dom:        { bg: "gray",    label: "Hidden DOM Challenge:" },
  click_reveal:      { bg: "green",   label: "Click to Reveal:" },
  scroll_reveal:     { bg: "orange",  label: "Scroll to Reveal:" },
  delayed_reveal:    { bg: "yellow",  label: "Delayed Reveal:" },
  drag_drop:         { bg: "indigo",  label: "Drag-and-Drop Challenge:" },
  keyboard_sequence: { bg: "purple",  label: "Keyboard Sequence Challenge:" },
  memory:            { bg: "yellow",  label: "Memory Challenge:" },
  hover_reveal:      { bg: "purple",  label: "Hover Challenge:" },
  timing:            { bg: "red",     label: "Timing Challenge:" },
  canvas:            { bg: "blue",    label: "Canvas Challenge:" },
  audio:             { bg: "green",   label: "Audio Challenge:" },
  video:             { bg: "pink",    label: "Video Challenge:" },
  split_parts:       { bg: "yellow",  label: "Split Parts Challenge:" },
  encoded_base64:    { bg: "indigo",  label: "Encoded Code Challenge:" },
  rotating:          { bg: "cyan",    label: "Rotating Code Challenge:" },
  obfuscated:        { bg: "red",     label: "Obfuscated Code Challenge:" },
  multi_tab:         { bg: "teal",    label: "Multi-Tab Challenge:" },
  gesture:           { bg: "orange",  label: "Gesture Challenge:" },
  sequence:          { bg: "violet",  label: "Sequence Challenge:" },
  puzzle_solve:      { bg: "pink",    label: "Puzzle Challenge:" },
  calculated:        { bg: "pink",    label: "Calculation Challenge:" },
  shadow_dom:        { bg: "slate",   label: "Shadow DOM Challenge:" },
  websocket:         { bg: "cyan",    label: "WebSocket Challenge:" },
  service_worker:    { bg: "amber",   label: "Service Worker Challenge:" },
  mutation:          { bg: "rose",    label: "Mutation Challenge:" },
  recursive_iframe:  { bg: "emerald", label: "Recursive Iframe Challenge:" },
  conditional_reveal:{ bg: "yellow",  label: "Conditional Reveal:" },
};

// ---------------------------------------------------------------------------
// DOM helpers — match live site structure exactly
// ---------------------------------------------------------------------------
function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => {
    if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "className") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
    else el.setAttribute(k, v);
  });
  children.forEach(c => {
    if (typeof c === "string") el.appendChild(document.createTextNode(c));
    else if (c) el.appendChild(c);
  });
  return el;
}

/**
 * Create the standard challenge box wrapper matching live site:
 * <div class="mt-4 p-4 bg-{color}-100 border-2 border-{color}-400 rounded relative z-[10005]">
 *   <strong>Label:</strong> description...
 * </div>
 */
function challengeBox(type, description, children = []) {
  const config = CHALLENGE_COLORS[type] || { bg: "gray", label: type + ":" };
  const color = config.bg;

  const box = document.createElement("div");
  box.className = `mt-4 p-4 bg-${color}-100 border-2 border-${color}-400 rounded relative z-[10005]`;

  // Label line: <strong>Type:</strong> description
  const label = document.createElement("p");
  label.className = "mb-2";
  const strong = document.createElement("strong");
  strong.textContent = config.label;
  label.appendChild(strong);
  label.appendChild(document.createTextNode(" " + description));
  box.appendChild(label);

  children.forEach(c => { if (c) box.appendChild(c); });
  return box;
}

/**
 * Create the code display element matching live site:
 * <div class="mt-2 p-3 bg-white border border-gray-300 rounded">
 *   <span class="text-xl font-mono font-bold text-gray-800">{code}</span>
 * </div>
 */
function codeDisplay(code, visible = true) {
  const wrapper = document.createElement("div");
  wrapper.className = "mt-2 p-3 bg-white border border-gray-300 rounded";
  if (!visible) wrapper.style.display = "none";

  const span = document.createElement("span");
  span.className = "text-xl font-mono font-bold text-gray-800";
  span.textContent = code;
  wrapper.appendChild(span);

  return wrapper;
}

// ===========================================================================
// 1. VISIBLE — code shown directly
// ===========================================================================
function createVisible(container, code, step) {
  const display = codeDisplay(code, true);
  const box = challengeBox("visible",
    "The code is displayed below. Enter it to proceed.",
    [display]);
  container.appendChild(box);
  return {
    onComplete: Promise.resolve({ type: "visible", timestamp: Date.now(), data: { code } }),
  };
}

// ===========================================================================
// 2. HIDDEN_DOM — click 3x or inspect attributes
// ===========================================================================
function createHiddenDom(container, code, step) {
  let clicks = 0;
  let resolved = false;
  const REQUIRED_CLICKS = 3;

  const counterText = document.createElement("p");
  counterText.className = "mt-2 text-sm text-gray-600";
  counterText.textContent = `Click here ${REQUIRED_CLICKS} more times to reveal the code.`;

  const display = codeDisplay(code, false);

  const box = challengeBox("hidden_dom",
    "The code is hidden somewhere in the DOM. Check element attributes, aria labels, or meta tags. Or click this box 3 times.",
    [counterText, display]);

  // Data attributes for DOM inspection
  box.setAttribute("data-code", code);
  box.setAttribute("aria-label", `hidden code: ${code}`);
  box.style.cursor = "pointer";

  container.appendChild(box);

  // Also hide code in a meta tag
  const meta = document.createElement("meta");
  meta.name = "challenge-code";
  meta.content = code;
  document.head.appendChild(meta);

  return {
    onComplete: new Promise(resolve => {
      box.addEventListener("click", () => {
        clicks++;
        const remaining = REQUIRED_CLICKS - clicks;
        if (remaining > 0) {
          counterText.textContent = `Click here ${remaining} more time${remaining > 1 ? "s" : ""} to reveal the code.`;
        }
        if (clicks >= REQUIRED_CLICKS && !resolved) {
          resolved = true;
          counterText.textContent = "Code revealed!";
          display.style.display = "block";
          resolve({ type: "hidden_dom", timestamp: Date.now(), data: { code, method: "click" } });
        }
      });
    }),
  };
}

// ===========================================================================
// 3. CLICK_REVEAL — click "Reveal Code" button
// ===========================================================================
function createClickReveal(container, code, step) {
  const btn = h("button", {
    className: "mt-2 px-6 py-2 bg-green-500 text-white font-semibold rounded hover:bg-green-600",
    id: "reveal-code-btn",
  }, ["Reveal Code"]);
  const display = codeDisplay(code, false);

  const box = challengeBox("click_reveal",
    "Click the button below to reveal the code.",
    [btn, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      btn.addEventListener("click", () => {
        display.style.display = "block";
        resolve({ type: "click_reveal", timestamp: Date.now(), data: { code } });
      });
    }),
  };
}

// ===========================================================================
// 4. SCROLL_REVEAL — scroll 500px to reveal
// ===========================================================================
function createScrollReveal(container, code, step) {
  const TARGET = 500;

  // Progress bar
  const progressOuter = document.createElement("div");
  progressOuter.className = "mt-2 w-full bg-gray-200 rounded-full h-4";
  const progressInner = document.createElement("div");
  progressInner.className = "bg-orange-500 h-4 rounded-full transition-all";
  progressInner.style.width = "0%";
  progressOuter.appendChild(progressInner);

  const progressText = document.createElement("p");
  progressText.className = "mt-1 text-sm text-gray-600";
  progressText.textContent = `Scrolled: 0px / ${TARGET}px`;

  const display = codeDisplay(code, false);

  const box = challengeBox("scroll_reveal",
    `Scroll down ${TARGET}px to reveal the code.`,
    [progressOuter, progressText, display]);

  // Add spacer to enable scrolling
  const spacer = h("div", { style: { height: "600px" } });
  container.appendChild(box);
  container.appendChild(spacer);

  return {
    onComplete: new Promise(resolve => {
      let maxScroll = 0;
      const handler = () => {
        maxScroll = Math.max(maxScroll, window.scrollY);
        const progress = Math.min(maxScroll, TARGET);
        const pct = Math.min(100, (progress / TARGET) * 100);
        progressInner.style.width = pct + "%";
        progressText.textContent = `Scrolled: ${progress}px / ${TARGET}px`;
        if (maxScroll >= TARGET) {
          window.removeEventListener("scroll", handler);
          display.style.display = "block";
          resolve({ type: "scroll_reveal", timestamp: Date.now(), data: { code, scrolled: maxScroll } });
        }
      };
      window.addEventListener("scroll", handler);
    }),
  };
}

// ===========================================================================
// 5. DELAYED_REVEAL — wait N seconds
// ===========================================================================
function createDelayedReveal(container, code, step) {
  const delay = 2000 + (step % 5) * 1000;
  const delaySec = delay / 1000;

  const msg = document.createElement("p");
  msg.className = "mt-2 text-sm text-gray-600";
  msg.id = "delay-msg";
  msg.textContent = `The code will appear after waiting ${delaySec} seconds...`;

  const display = codeDisplay(code, false);

  const box = challengeBox("delayed_reveal",
    `Wait ${delaySec} seconds for the code to appear.`,
    [msg, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      setTimeout(() => {
        msg.textContent = "Wait complete! Code revealed:";
        display.style.display = "block";
        resolve({ type: "delayed_reveal", timestamp: Date.now(), data: { code, delay } });
      }, delay);
    }),
  };
}

// ===========================================================================
// 6. DRAG_DROP — drag pieces to slots
// ===========================================================================
function createDragDrop(container, code, step) {
  const pieceCount = 6;
  const pieces = code.split("").map((c, i) => ({ id: `piece-${i}`, char: c }));
  const extras = ["X", "Z", "Q", "W"].map((c, i) => ({ id: `piece-extra-${i}`, char: c }));
  const allPieces = [...pieces, ...extras].sort(() => Math.random() - 0.5);

  const piecesDiv = h("div", { className: "flex flex-wrap gap-2 mt-3", id: "drag-pieces" });
  allPieces.forEach(p => {
    const el = h("div", {
      className: "px-4 py-3 bg-indigo-200 border-2 border-indigo-400 rounded-lg font-bold text-lg cursor-grab select-none",
      draggable: "true",
      "data-piece": p.id,
    }, [p.char]);
    el.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/plain", p.id));
    piecesDiv.appendChild(el);
  });

  const slotsDiv = h("div", { className: "flex flex-wrap gap-2 mt-3", id: "drop-slots" });
  let filled = 0;
  const resolvers = [];

  for (let i = 0; i < pieceCount; i++) {
    const slot = h("div", {
      className: "w-14 h-14 border-2 border-dashed border-gray-400 rounded-lg flex items-center justify-center text-gray-400 text-sm",
      "data-slot": `slot-${i}`,
    }, ["Drop"]);
    slot.addEventListener("dragover", e => e.preventDefault());
    slot.addEventListener("drop", (e) => {
      e.preventDefault();
      if (slot.dataset.filled) return;
      slot.dataset.filled = "true";
      slot.textContent = "\u2713";
      slot.className = "w-14 h-14 border-2 border-solid border-green-500 rounded-lg flex items-center justify-center bg-green-100 text-green-600 text-2xl";
      filled++;
      if (filled >= pieceCount) {
        display.style.display = "block";
        resolvers.forEach(r => r());
      }
    });
    slotsDiv.appendChild(slot);
  }

  const display = codeDisplay(code, false);

  const box = challengeBox("drag_drop",
    `Drag any ${pieceCount} pieces from the available pieces to the drop slots.`,
    [piecesDiv, slotsDiv, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      resolvers.push(() => resolve({ type: "drag_drop", timestamp: Date.now(), data: { code, filled: pieceCount } }));
    }),
  };
}

// ===========================================================================
// 7. KEYBOARD_SEQUENCE — press key combos
// ===========================================================================
function createKeyboardSequence(container, code, step) {
  const sequences = [
    ["Control+A", "Control+C", "Control+V"],
    ["ArrowUp", "ArrowDown", "Enter"],
    ["Shift+Tab", "Tab", "Enter"],
  ];
  const seq = sequences[step % sequences.length];
  let current = 0;

  const progress = h("p", { className: "mt-2 text-sm text-gray-600", id: "kb-progress" },
    [`Required sequence: ${seq.join(" \u2192 ")}  |  Your input (${current}/${seq.length})`]);
  const display = codeDisplay(code, false);

  const box = challengeBox("keyboard_sequence",
    `Press the following key sequence: ${seq.join(" \u2192 ")}`,
    [progress, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      const handler = (e) => {
        const expected = seq[current];
        const parts = expected.split("+");
        const key = parts[parts.length - 1];
        const needCtrl = parts.includes("Control");
        const needShift = parts.includes("Shift");
        const needAlt = parts.includes("Alt");
        const keyMatch = e.key === key || e.key.toLowerCase() === key.toLowerCase() ||
                         e.code === key || e.code === `Key${key.toUpperCase()}`;
        const modMatch = (!needCtrl || e.ctrlKey) && (!needShift || e.shiftKey) && (!needAlt || e.altKey);
        if (keyMatch && modMatch) {
          current++;
          progress.textContent = `Required sequence: ${seq.join(" \u2192 ")}  |  Your input (${current}/${seq.length})`;
          if (current >= seq.length) {
            document.removeEventListener("keydown", handler);
            display.style.display = "block";
            resolve({ type: "keyboard_sequence", timestamp: Date.now(), data: { code, sequence: seq } });
          }
        }
      };
      document.addEventListener("keydown", handler);
    }),
  };
}

// ===========================================================================
// 8. MEMORY — flash code briefly
// ===========================================================================
function createMemory(container, code, step) {
  const flashDuration = 1500;

  const flashDisplay = codeDisplay(code, true);
  flashDisplay.id = "flash-code";

  const btn = h("button", {
    className: "mt-2 px-6 py-2 bg-yellow-500 text-white font-semibold rounded hover:bg-yellow-600",
    id: "remember-btn",
    style: { display: "none" },
  }, ["I Remember"]);

  const resultDisplay = codeDisplay(code, false);
  resultDisplay.id = "memory-result";

  const box = challengeBox("memory",
    "The code will flash for 1.5 seconds. Remember it!",
    [flashDisplay, btn, resultDisplay]);
  container.appendChild(box);

  setTimeout(() => {
    flashDisplay.style.display = "none";
    btn.style.display = "block";
  }, flashDuration);

  return {
    onComplete: new Promise(resolve => {
      btn.addEventListener("click", () => {
        resultDisplay.style.display = "block";
        resolve({ type: "memory", timestamp: Date.now(), data: { code } });
      });
    }),
  };
}

// ===========================================================================
// 9. HOVER_REVEAL — hover for 1 second
// ===========================================================================
function createHoverReveal(container, code, step) {
  const target = h("div", {
    className: "mt-3 w-48 h-24 bg-purple-300 rounded-lg flex items-center justify-center cursor-pointer mx-auto font-semibold",
    id: "hover-target",
    "data-hover-target": "true",
  }, ["Hover here for 1s"]);

  const display = codeDisplay(code, false);

  const box = challengeBox("hover_reveal",
    "Hover over the target box below for at least 1 second.",
    [target, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      let hoverTimer = null;
      target.addEventListener("mouseenter", () => {
        hoverTimer = setTimeout(() => {
          target.textContent = "Revealed!";
          target.className = "mt-3 w-48 h-24 bg-green-400 rounded-lg flex items-center justify-center cursor-default mx-auto font-semibold text-white";
          display.style.display = "block";
          resolve({ type: "hover_reveal", timestamp: Date.now(), data: { code } });
        }, 1000);
      });
      target.addEventListener("mouseleave", () => clearTimeout(hoverTimer));
    }),
  };
}

// ===========================================================================
// 10. TIMING — capture code during timed window
// ===========================================================================
function createTiming(container, code, step) {
  let revealed = false;
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

  const timingDisplay = h("div", {
    className: "mt-2 p-3 bg-white border border-gray-300 rounded text-center",
    id: "timing-display",
  });
  const timingSpan = h("span", { className: "text-xl font-mono font-bold text-gray-800" }, ["------"]);
  timingDisplay.appendChild(timingSpan);

  const btn = h("button", {
    className: "mt-2 px-6 py-2 bg-red-500 text-white font-semibold rounded hover:bg-red-600",
    id: "capture-btn",
  }, ["Capture"]);

  const countText = h("p", { className: "mt-1 text-sm text-gray-600", id: "capture-count" }, ["Captures: 0/3"]);
  const display = codeDisplay(code, false);

  const box = challengeBox("timing",
    "The code flashes rapidly. Click 'Capture' at the right moment.",
    [timingDisplay, btn, countText, display]);
  container.appendChild(box);

  const interval = setInterval(() => {
    if (revealed) return;
    const showReal = Date.now() % 3000 < 200;
    if (showReal) {
      timingSpan.textContent = code;
      timingSpan.dataset.isReal = "true";
    } else {
      timingSpan.textContent = Array.from({ length: 6 }, () => chars[Math.floor(Math.random() * chars.length)]).join("");
      timingSpan.dataset.isReal = "false";
    }
  }, 100);

  let captures = 0;
  return {
    onComplete: new Promise(resolve => {
      btn.addEventListener("click", () => {
        captures++;
        countText.textContent = `Captures: ${captures}/3`;
        if (captures >= 3) {
          revealed = true;
          clearInterval(interval);
          display.style.display = "block";
          resolve({ type: "timing", timestamp: Date.now(), data: { code, captures } });
        }
      });
    }),
  };
}

// ===========================================================================
// 11. CANVAS — draw 3+ strokes
// ===========================================================================
function createCanvas(container, code, step) {
  const canvas = document.createElement("canvas");
  canvas.width = 400; canvas.height = 300;
  canvas.className = "mt-3 border-2 border-gray-400 rounded-lg cursor-crosshair block mx-auto";
  const ctx = canvas.getContext("2d");
  ctx.strokeStyle = "#333"; ctx.lineWidth = 3;
  let drawing = false, strokes = 0;

  canvas.addEventListener("mousedown", (e) => {
    drawing = true; ctx.beginPath();
    const r = canvas.getBoundingClientRect();
    ctx.moveTo(e.clientX - r.left, e.clientY - r.top);
  });
  canvas.addEventListener("mousemove", (e) => {
    if (!drawing) return;
    const r = canvas.getBoundingClientRect();
    ctx.lineTo(e.clientX - r.left, e.clientY - r.top); ctx.stroke();
  });

  const strokeCount = h("p", { className: "mt-1 text-sm text-gray-600", id: "stroke-count" }, ["Strokes: 0/3"]);
  const btn = h("button", {
    className: "mt-2 px-6 py-2 bg-blue-500 text-white font-semibold rounded opacity-50 cursor-not-allowed",
    id: "canvas-reveal", disabled: "true",
  }, ["Draw something first"]);
  const display = codeDisplay(code, false);

  const box = challengeBox("canvas",
    "Draw at least 3 strokes on the canvas.",
    [canvas, strokeCount, btn, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      canvas.addEventListener("mouseup", () => {
        if (drawing) {
          drawing = false; strokes++;
          strokeCount.textContent = `Strokes: ${strokes}/3 ${strokes >= 3 ? "\u2713" : ""}`;
          if (strokes >= 3) {
            btn.disabled = false;
            btn.className = "mt-2 px-6 py-2 bg-blue-500 text-white font-semibold rounded hover:bg-blue-600 cursor-pointer";
            btn.textContent = "Reveal Code";
            btn.addEventListener("click", () => {
              display.style.display = "block";
              resolve({ type: "canvas", timestamp: Date.now(), data: { code, strokes } });
            }, { once: true });
          }
        }
      });
    }),
  };
}

// ===========================================================================
// 12. AUDIO — play audio to reveal
// ===========================================================================
function createAudio(container, code, step) {
  const btn = h("button", {
    className: "mt-2 px-6 py-2 bg-green-500 text-white font-semibold rounded hover:bg-green-600",
    id: "play-audio-btn",
  }, ["Play Audio"]);
  const status = h("p", { className: "mt-1 text-sm text-gray-600", id: "audio-status" });
  const display = codeDisplay(code, false);

  const box = challengeBox("audio",
    "Click Play Audio to reveal the code.",
    [btn, status, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      btn.addEventListener("click", () => {
        try {
          const actx = new AudioContext();
          const osc = actx.createOscillator();
          osc.type = "sine"; osc.frequency.value = 440;
          osc.connect(actx.destination); osc.start();
          setTimeout(() => osc.stop(), 500);
        } catch (_) {}
        status.textContent = "Audio played!";
        display.style.display = "block";
        resolve({ type: "audio", timestamp: Date.now(), data: { code } });
      });
    }),
  };
}

// ===========================================================================
// 13. VIDEO — seek to frame
// ===========================================================================
function createVideo(container, code, step) {
  const targetFrame = 43, totalFrames = 60;
  let currentFrame = 0, seeks = 0;
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

  const player = h("div", { className: "mt-3 bg-black rounded-lg p-6 text-center" });
  const frameText = h("div", { className: "text-white text-xl font-mono", id: "video-frame" }, [`Frame ${currentFrame}/${totalFrames}`]);
  const frameCode = h("div", { className: "text-red-400 text-sm mt-2 font-mono", id: "frame-decoy-code" });
  player.appendChild(frameText); player.appendChild(frameCode);

  const controls = h("div", { className: "flex gap-2 justify-center mt-3" });
  const btnBack = h("button", { className: "px-4 py-2 bg-gray-700 text-white rounded", id: "seek-back" }, ["-10"]);
  const btnFwd = h("button", { className: "px-4 py-2 bg-gray-700 text-white rounded", id: "seek-forward" }, ["+10"]);
  const btnTarget = h("button", { className: "px-4 py-2 bg-gray-700 text-white rounded", id: "seek-target" }, [`Frame ${targetFrame}`]);
  controls.appendChild(btnBack); controls.appendChild(btnFwd); controls.appendChild(btnTarget);

  const seekCount = h("p", { className: "mt-1 text-sm text-gray-600", id: "seek-count" }, ["Seeks: 0"]);
  const display = codeDisplay(code, false);

  const box = challengeBox("video",
    `Seek through video frames. Navigate to frame ${targetFrame}.`,
    [player, controls, seekCount, display]);
  container.appendChild(box);

  function updateFrame() {
    frameText.textContent = `Frame ${currentFrame}/${totalFrames}`;
    frameCode.textContent = `Code: ${Array.from({ length: 6 }, () => chars[Math.floor(Math.random() * 32)]).join("")}`;
  }

  return {
    onComplete: new Promise(resolve => {
      const doSeek = (delta) => {
        currentFrame = Math.max(0, Math.min(totalFrames, currentFrame + delta));
        seeks++;
        seekCount.textContent = `Seeks: ${seeks}`;
        updateFrame();
        if (seeks >= 3 && currentFrame === targetFrame) {
          display.style.display = "block";
          resolve({ type: "video", timestamp: Date.now(), data: { code, frame: currentFrame, seeks } });
        }
      };
      btnBack.addEventListener("click", () => doSeek(-10));
      btnFwd.addEventListener("click", () => doSeek(10));
      btnTarget.addEventListener("click", () => { currentFrame = targetFrame; seeks++; seekCount.textContent = `Seeks: ${seeks}`; updateFrame();
        if (seeks >= 3) { display.style.display = "block"; resolve({ type: "video", timestamp: Date.now(), data: { code, frame: currentFrame, seeks } }); }
      });
    }),
  };
}

// ===========================================================================
// 14. SPLIT_PARTS — find and click scattered parts
// ===========================================================================
function createSplitParts(container, code, step) {
  const partCount = 4;
  const parts = [];
  for (let i = 0; i < partCount; i++) {
    parts.push(code.substring(i * Math.floor(code.length / partCount),
      (i + 1) * Math.floor(code.length / partCount) + (i === partCount - 1 ? code.length % partCount : 0)));
  }

  const progress = h("p", { className: "mt-1 text-sm text-gray-600", id: "parts-progress" }, [`0/${partCount} found`]);
  const display = codeDisplay(code, false);

  const box = challengeBox("split_parts",
    `Find and click all ${partCount} parts scattered on the page.`,
    [progress, display]);
  container.appendChild(box);

  let found = 0;
  const resolvers = [];

  parts.forEach((part, i) => {
    const el = h("div", {
      className: "absolute px-4 py-2 bg-yellow-400 rounded-lg cursor-pointer font-bold z-[100]",
      "data-part": `part-${i}`,
      style: {
        top: `${20 + (i * 15) + Math.random() * 10}%`,
        left: `${10 + (i * 20) + Math.random() * 10}%`,
      },
    }, [`Part ${i + 1}: ${part}`]);
    el.addEventListener("click", () => {
      if (el.dataset.clicked) return;
      el.dataset.clicked = "true";
      el.className = "absolute px-4 py-2 bg-green-400 rounded-lg font-bold z-[100] text-white";
      found++;
      progress.textContent = `${found}/${partCount} found`;
      if (found >= partCount) {
        display.style.display = "block";
        resolvers.forEach(r => r());
      }
    });
    container.appendChild(el);
  });

  return {
    onComplete: new Promise(resolve => {
      resolvers.push(() => resolve({ type: "split_parts", timestamp: Date.now(), data: { code, parts: partCount } }));
    }),
  };
}

// ===========================================================================
// 15. ENCODED_BASE64 — decode and enter
// ===========================================================================
function createEncodedBase64(container, code, step) {
  const encoded = btoa(`DECODE_ME_${step}`);

  const encodedText = h("p", { className: "mt-2 font-mono text-sm bg-white p-2 rounded border border-gray-300" }, [encoded]);
  const hint = h("p", { className: "mt-1 text-sm text-gray-500" }, ["Hint: Decode it, then enter any 6-character code attempt."]);
  const input = h("input", { type: "text", className: "mt-2 px-3 py-2 border border-gray-300 rounded font-mono text-center text-lg uppercase", placeholder: "Enter 6-char code", id: "base64-input", maxLength: "6" });
  const btn = h("button", {
    className: "mt-2 ml-2 px-6 py-2 bg-indigo-500 text-white font-semibold rounded hover:bg-indigo-600",
    id: "base64-reveal",
  }, ["Reveal"]);
  const display = codeDisplay(code, false);

  const box = challengeBox("encoded_base64",
    `Base64 encoded string below:`,
    [encodedText, hint, input, btn, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      btn.addEventListener("click", () => {
        if (input.value.length >= 6) {
          display.style.display = "block";
          resolve({ type: "encoded_base64", timestamp: Date.now(), data: { code, decoded: atob(encoded) } });
        }
      });
    }),
  };
}

// ===========================================================================
// 16. ROTATING — capture rotating code
// ===========================================================================
function createRotating(container, code, step) {
  let captures = 0, revealed = false;
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

  const rotatingDisplay = h("div", { className: "mt-2 p-3 bg-white border border-gray-300 rounded text-center", id: "rotating-display" });
  const rotatingSpan = h("span", { className: "text-xl font-mono font-bold text-gray-800" }, ["------"]);
  rotatingDisplay.appendChild(rotatingSpan);

  const btn = h("button", {
    className: "mt-2 px-6 py-2 bg-cyan-500 text-white font-semibold rounded hover:bg-cyan-600",
    id: "rotating-capture",
  }, ["Capture"]);
  const countText = h("p", { className: "mt-1 text-sm text-gray-600", id: "rotating-count" }, ["Captures: 0/3"]);
  const display = codeDisplay(code, false);

  const box = challengeBox("rotating",
    "The code rotates rapidly. Click Capture 3 times.",
    [rotatingDisplay, btn, countText, display]);
  container.appendChild(box);

  const interval = setInterval(() => {
    if (revealed) return;
    rotatingSpan.textContent = Array.from({ length: 6 }, () => chars[Math.floor(Math.random() * chars.length)]).join("");
  }, 150);

  return {
    onComplete: new Promise(resolve => {
      btn.addEventListener("click", () => {
        captures++;
        countText.textContent = `Captures: ${captures}/3`;
        if (captures >= 3) {
          revealed = true; clearInterval(interval);
          display.style.display = "block";
          resolve({ type: "rotating", timestamp: Date.now(), data: { code, captures } });
        }
      });
    }),
  };
}

// ===========================================================================
// 17. OBFUSCATED — reverse characters
// ===========================================================================
function createObfuscated(container, code, step) {
  const reversed = code.split("").reverse().join("");

  const obfText = h("p", { className: "mt-2 font-mono text-lg bg-white p-2 rounded border border-gray-300 text-center" }, [reversed]);
  const hint = h("p", { className: "mt-1 text-sm text-gray-500" }, ["Hint: The characters are reversed."]);
  const input = h("input", { type: "text", className: "mt-2 px-3 py-2 border border-gray-300 rounded font-mono text-center text-lg uppercase", placeholder: "Enter decoded code", id: "obfuscated-input", maxLength: "6" });
  const btn = h("button", {
    className: "mt-2 ml-2 px-6 py-2 bg-red-500 text-white font-semibold rounded hover:bg-red-600",
    id: "obfuscated-submit",
  }, ["Submit"]);
  const display = codeDisplay(code, false);

  const box = challengeBox("obfuscated",
    "The code is obfuscated below:",
    [obfText, hint, input, btn, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      btn.addEventListener("click", () => {
        if (input.value.toUpperCase() === code) {
          display.style.display = "block";
          resolve({ type: "obfuscated", timestamp: Date.now(), data: { code } });
        }
      });
    }),
  };
}

// ===========================================================================
// 18. MULTI_TAB — visit all tabs
// ===========================================================================
function createMultiTab(container, code, step) {
  const tabCount = 5;
  const tabPieces = [];
  for (let i = 0; i < tabCount; i++) {
    tabPieces.push(code.substring(i * 1, i * 1 + 2) || code.substring(0, 2));
  }
  let visited = new Set();

  const btnContainer = h("div", { className: "flex flex-wrap gap-2 mt-3", id: "tab-buttons" });
  for (let i = 0; i < tabCount; i++) {
    btnContainer.appendChild(h("button", {
      className: "px-4 py-2 bg-gray-200 border border-gray-300 rounded",
      "data-tab": `${i}`,
    }, [`Tab ${i + 1}`]));
  }

  const tabContent = h("div", { className: "mt-2 p-3 bg-white border border-gray-300 rounded text-sm", id: "tab-content" }, ["Click a tab to see its content."]);
  const progress = h("p", { className: "mt-1 text-sm text-gray-600", id: "tab-progress" }, [`Visited: 0/${tabCount}`]);
  const display = codeDisplay(code, false);

  const box = challengeBox("multi_tab",
    "Visit all 5 tabs by clicking each button.",
    [btnContainer, tabContent, progress, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      btnContainer.querySelectorAll("button").forEach(btn => {
        btn.addEventListener("click", () => {
          const idx = parseInt(btn.dataset.tab);
          visited.add(idx);
          btn.className = "px-4 py-2 bg-teal-500 text-white border border-teal-600 rounded";
          tabContent.textContent = `Tab ${idx + 1} piece: ${tabPieces[idx] || "??"}`;
          progress.textContent = `Visited: ${visited.size}/${tabCount}`;
          if (visited.size >= tabCount) {
            display.style.display = "block";
            resolve({ type: "multi_tab", timestamp: Date.now(), data: { code, tabs: tabCount } });
          }
        });
      });
    }),
  };
}

// ===========================================================================
// 19. GESTURE — draw specific shape
// ===========================================================================
function createGesture(container, code, step) {
  const canvas = document.createElement("canvas");
  canvas.width = 400; canvas.height = 300;
  canvas.className = "mt-3 border-2 border-gray-400 rounded-lg cursor-crosshair block mx-auto";
  const ctx = canvas.getContext("2d");
  ctx.strokeStyle = "#3b82f6"; ctx.lineWidth = 3;
  let drawing = false, attempts = 0;

  canvas.addEventListener("mousedown", (e) => {
    drawing = true; ctx.beginPath();
    const r = canvas.getBoundingClientRect();
    ctx.moveTo(e.clientX - r.left, e.clientY - r.top);
  });
  canvas.addEventListener("mousemove", (e) => {
    if (!drawing) return;
    const r = canvas.getBoundingClientRect();
    ctx.lineTo(e.clientX - r.left, e.clientY - r.top); ctx.stroke();
  });

  const attemptText = h("p", { className: "mt-1 text-sm text-gray-600", id: "gesture-attempts" }, ["Attempts: 0"]);
  const btn = h("button", {
    className: "mt-2 px-6 py-2 bg-orange-500 text-white font-semibold rounded opacity-50 cursor-not-allowed",
    id: "gesture-complete", disabled: "true",
  }, ["Draw something first"]);
  const display = codeDisplay(code, false);

  const box = challengeBox("gesture",
    "Draw a square on the canvas below, then click complete.",
    [canvas, attemptText, btn, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      canvas.addEventListener("mouseup", () => {
        if (drawing) {
          drawing = false; attempts++;
          attemptText.textContent = `Attempts: ${attempts}`;
          if (attempts >= 1) {
            btn.disabled = false;
            btn.className = "mt-2 px-6 py-2 bg-orange-500 text-white font-semibold rounded hover:bg-orange-600 cursor-pointer";
            btn.textContent = "Complete";
            btn.addEventListener("click", () => {
              display.style.display = "block";
              resolve({ type: "gesture", timestamp: Date.now(), data: { code, attempts } });
            }, { once: true });
          }
        }
      });
    }),
  };
}

// ===========================================================================
// 20. SEQUENCE — complete 4 actions
// ===========================================================================
function createSequence(container, code, step) {
  const actions = { "click button": false, "hover area": false, "type text": false, "scroll box": false };

  function checkComplete() { return Object.values(actions).every(v => v); }

  const progress = h("p", { className: "mt-1 text-sm text-gray-600", id: "seq-progress" }, ["Progress: 0/4"]);

  // Action 1: Click button
  const a1 = h("div", { className: "flex items-center gap-3 mt-2 p-2 rounded bg-white border border-gray-200" }, [
    h("span", { id: "seq-click-status" }, ["\u25CB click button \u2014 "]),
    h("button", { id: "seq-click-btn", className: "px-3 py-1 bg-violet-500 text-white rounded text-sm" }, ["Click Me"]),
  ]);

  // Action 2: Hover area
  const a2 = h("div", { className: "flex items-center gap-3 mt-2 p-2 rounded bg-white border border-gray-200" }, [
    h("span", { id: "seq-hover-status" }, ["\u25CB hover area \u2014 "]),
    h("div", {
      id: "seq-hover-area", "data-hover-area": "true",
      className: "w-28 h-14 bg-green-400 rounded flex items-center justify-center cursor-pointer text-sm",
    }, ["Hover here"]),
  ]);

  // Action 3: Type text
  const a3 = h("div", { className: "flex items-center gap-3 mt-2 p-2 rounded bg-white border border-gray-200" }, [
    h("span", { id: "seq-type-status" }, ["\u25CB type text \u2014 "]),
    h("input", { type: "text", id: "seq-type-input", className: "px-2 py-1 border border-gray-300 rounded text-sm", placeholder: "Type here" }),
  ]);

  // Action 4: Scroll box
  const scrollInner = h("div", { style: { height: "300px", padding: "8px" } }, ["Scroll inside this box"]);
  const a4 = h("div", { className: "flex items-center gap-3 mt-2 p-2 rounded bg-white border border-gray-200" }, [
    h("span", { id: "seq-scroll-status" }, ["\u25CB scroll box \u2014 "]),
    h("div", {
      id: "seq-scroll-box", "data-scroll-box": "true",
      className: "w-48 h-20 overflow-auto border border-gray-300 rounded",
    }, [scrollInner]),
  ]);

  const completeBtn = h("button", {
    className: "mt-3 px-6 py-2 bg-violet-500 text-white font-semibold rounded opacity-50 cursor-not-allowed",
    id: "seq-complete-btn", disabled: "true",
  }, ["Complete (0/4)"]);
  const display = codeDisplay(code, false);

  const box = challengeBox("sequence",
    "Complete all 4 actions to reveal the code.",
    [progress, a1, a2, a3, a4, completeBtn, display]);
  container.appendChild(box);

  function update(key) {
    actions[key] = true;
    const count = Object.values(actions).filter(v => v).length;
    progress.textContent = `Progress: ${count}/4`;
    const statusEl = box.querySelector(`#seq-${key.split(" ")[0]}-status`);
    if (statusEl) statusEl.textContent = `\u2713 ${key} \u2014 `;
    completeBtn.textContent = `Complete (${count}/4)`;
    if (checkComplete()) {
      completeBtn.disabled = false;
      completeBtn.className = "mt-3 px-6 py-2 bg-violet-500 text-white font-semibold rounded hover:bg-violet-600 cursor-pointer";
    }
  }

  return {
    onComplete: new Promise(resolve => {
      box.querySelector("#seq-click-btn").addEventListener("click", () => update("click button"));
      let ht = null;
      const hoverArea = box.querySelector("#seq-hover-area");
      hoverArea.addEventListener("mouseenter", () => { ht = setTimeout(() => update("hover area"), 800); });
      hoverArea.addEventListener("mouseleave", () => clearTimeout(ht));
      box.querySelector("#seq-type-input").addEventListener("input", (e) => { if (e.target.value.length > 0) update("type text"); });
      box.querySelector("#seq-scroll-box").addEventListener("scroll", () => update("scroll box"));
      completeBtn.addEventListener("click", () => {
        if (checkComplete()) {
          display.style.display = "block";
          resolve({ type: "sequence", timestamp: Date.now(), data: { code } });
        }
      });
    }),
  };
}

// ===========================================================================
// 21. PUZZLE_SOLVE — math puzzle
// ===========================================================================
function createPuzzleSolve(container, code, step) {
  const a = 10 + (step % 20), b = 5 + (step % 15), answer = a + b;

  const puzzleText = h("p", { className: "mt-2 text-2xl font-mono font-bold text-center" }, [`${a} + ${b} = ?`]);
  const input = h("input", { type: "text", className: "mt-2 px-3 py-2 border border-gray-300 rounded font-mono text-center text-lg", id: "puzzle-input", placeholder: "Enter answer" });
  const btn = h("button", {
    className: "mt-2 ml-2 px-6 py-2 bg-pink-500 text-white font-semibold rounded hover:bg-pink-600",
    id: "puzzle-solve",
  }, ["Solve"]);
  const display = codeDisplay(code, false);

  const box = challengeBox("puzzle_solve",
    "Solve this puzzle to reveal the code.",
    [puzzleText, input, btn, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      btn.addEventListener("click", () => {
        if (parseInt(input.value) === answer) {
          display.style.display = "block";
          resolve({ type: "puzzle_solve", timestamp: Date.now(), data: { code, answer } });
        }
      });
    }),
  };
}

// ===========================================================================
// 22. CALCULATED — calculation challenge
// ===========================================================================
function createCalculated(container, code, step) {
  const multiplier = 7919 + (step % 100);
  const offset = 12345 + (step % 1000);
  const result = step * multiplier + offset;

  const calcText = h("p", { className: "mt-2 text-lg font-mono text-center" }, [`Calculate: ${step} \u00D7 ${multiplier} + ${offset} = ?`]);
  const input = h("input", { type: "text", className: "mt-2 px-3 py-2 border border-gray-300 rounded font-mono text-center text-lg", id: "calc-input", placeholder: "Enter answer" });
  const btn = h("button", {
    className: "mt-2 ml-2 px-6 py-2 bg-pink-500 text-white font-semibold rounded hover:bg-pink-600",
    id: "calc-solve",
  }, ["Solve"]);
  const display = codeDisplay(code, false);

  const box = challengeBox("calculated",
    `Calculate the expression to reveal the code.`,
    [calcText, input, btn, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      btn.addEventListener("click", () => {
        if (parseInt(input.value) === result) {
          display.style.display = "block";
          resolve({ type: "calculated", timestamp: Date.now(), data: { code, result } });
        }
      });
    }),
  };
}

// ===========================================================================
// 23. SHADOW_DOM — navigate shadow layers
// ===========================================================================
function createShadowDom(container, code, step) {
  const levels = 3;
  let revealed = 0;

  const progress = h("p", { className: "mt-1 text-sm text-gray-600", id: "shadow-progress" }, [`Levels revealed: 0/${levels}`]);
  const shadowContainer = h("div", { id: "shadow-container", className: "mt-3" });
  const display = codeDisplay(code, false);

  const box = challengeBox("shadow_dom",
    `Navigate through ${levels} nested shadow DOM layers to reveal the code. Click each layer in order.`,
    [progress, shadowContainer, display]);
  container.appendChild(box);

  const resolvers = [];

  function buildLevel(parent, level) {
    const host = document.createElement("div");
    host.className = "shadow-level-host";
    parent.appendChild(host);
    const shadow = host.attachShadow({ mode: "open" });
    const wrapper = document.createElement("div");
    wrapper.style.cssText = "padding:16px;margin:8px;border:2px solid #94a3b8;border-radius:8px;cursor:pointer;background:#f8fafc;";
    const label = document.createElement("span");
    label.textContent = `Shadow Level ${level}${level === levels ? " - Deepest level!" : ""}`;
    wrapper.appendChild(label);
    wrapper.addEventListener("click", (e) => {
      e.stopPropagation();
      if (level === revealed + 1) {
        revealed++;
        label.textContent = `Shadow Level ${level} \u2713${level === levels ? " - Deepest level!" : ""}`;
        wrapper.style.background = "#e2e8f0"; wrapper.style.borderColor = "#64748b";
        progress.textContent = `Levels revealed: ${revealed}/${levels}`;
        if (revealed >= levels) {
          display.style.display = "block";
          resolvers.forEach(r => r());
        }
      }
    });
    shadow.appendChild(wrapper);
    if (level < levels) buildLevel(wrapper, level + 1);
  }

  buildLevel(shadowContainer, 1);

  return {
    onComplete: new Promise(resolve => {
      resolvers.push(() => resolve({ type: "shadow_dom", timestamp: Date.now(), data: { code, levels } }));
    }),
  };
}

// ===========================================================================
// 24. WEBSOCKET — simulated connection
// ===========================================================================
function createWebsocket(container, code, step) {
  const btn = h("button", {
    className: "mt-2 px-6 py-2 bg-cyan-500 text-white font-semibold rounded hover:bg-cyan-600",
    id: "ws-connect",
  }, ["Connect"]);
  const terminal = h("pre", {
    className: "mt-3 bg-gray-900 text-green-400 p-4 rounded-lg font-mono text-sm max-h-48 overflow-y-auto whitespace-pre-wrap",
    id: "ws-terminal",
  }, ["$ Waiting..."]);
  const display = codeDisplay(code, false);

  const box = challengeBox("websocket",
    "Connect to the simulated WebSocket server and receive the code.",
    [btn, terminal, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      btn.addEventListener("click", () => {
        btn.disabled = true; btn.textContent = "Connecting...";
        btn.className = "mt-2 px-6 py-2 bg-cyan-300 text-white font-semibold rounded cursor-not-allowed";
        const messages = [
          { delay: 500, text: "$ Connecting..." },
          { delay: 1000, text: "$ Connection established" },
          { delay: 1500, text: "$ Receiving data..." },
          { delay: 2000, text: "$ Data received: [encrypted]" },
          { delay: 2500, text: "$ Decrypting..." },
          { delay: 3000, text: "$ Ready to reveal code" },
          { delay: 3500, text: `$ Code: ${code}` },
        ];
        messages.forEach(({ delay, text }) => {
          setTimeout(() => {
            terminal.textContent += "\n" + text;
            if (text.includes("Code:")) {
              btn.textContent = "Connected";
              display.style.display = "block";
              resolve({ type: "websocket", timestamp: Date.now(), data: { code } });
            }
          }, delay);
        });
      });
    }),
  };
}

// ===========================================================================
// 25. SERVICE_WORKER — register and cache
// ===========================================================================
function createServiceWorker(container, code, step) {
  const swStatus = h("span", { id: "sw-status", className: "text-gray-500" }, ["\u25CB Not registered"]);
  const cacheStatus = h("span", { id: "cache-status", className: "text-gray-500" }, ["\u25CB Empty"]);

  const statusDiv = h("div", { className: "mt-2 text-sm space-y-1" }, [
    h("p", {}, ["Service Worker: ", swStatus]),
    h("p", {}, ["Cache: ", cacheStatus]),
  ]);

  const btnRegister = h("button", {
    className: "mt-2 px-6 py-2 bg-amber-500 text-white font-semibold rounded hover:bg-amber-600",
    id: "sw-register",
  }, ["1. Register Service Worker"]);
  const btnRetrieve = h("button", {
    className: "mt-2 ml-2 px-6 py-2 bg-amber-500 text-white font-semibold rounded opacity-50 cursor-not-allowed",
    id: "sw-retrieve", disabled: "true",
  }, ["2. Retrieve from Cache"]);
  const display = codeDisplay(code, false);

  const box = challengeBox("service_worker",
    "Register a service worker, wait for cache, then retrieve the code.",
    [statusDiv, btnRegister, btnRetrieve, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      btnRegister.addEventListener("click", () => {
        swStatus.textContent = "\u25CF Registered";
        swStatus.className = "text-green-500 font-semibold";
        setTimeout(() => {
          cacheStatus.textContent = "\u25CF Cached";
          cacheStatus.className = "text-green-500 font-semibold";
          btnRetrieve.disabled = false;
          btnRetrieve.className = "mt-2 ml-2 px-6 py-2 bg-amber-500 text-white font-semibold rounded hover:bg-amber-600 cursor-pointer";
        }, 1500);
      });
      btnRetrieve.addEventListener("click", () => {
        display.style.display = "block";
        resolve({ type: "service_worker", timestamp: Date.now(), data: { code } });
      });
    }),
  };
}

// ===========================================================================
// 26. MUTATION — trigger DOM mutations
// ===========================================================================
function createMutation(container, code, step) {
  const target = 5;
  let count = 0;

  const btn = h("button", {
    className: "mt-2 px-6 py-2 bg-rose-500 text-white font-semibold rounded hover:bg-rose-600",
    id: "mutate-btn",
  }, ["Trigger Mutation"]);
  const progress = h("p", { className: "mt-1 text-sm text-gray-600", id: "mutation-progress" }, [`Mutations triggered: 0 / ${target}`]);
  const mutationContainer = h("div", { id: "mutation-container", className: "mt-2 space-y-1" });
  const revealBtn = h("button", {
    className: "mt-2 px-6 py-2 bg-rose-500 text-white font-semibold rounded opacity-50 cursor-not-allowed",
    id: "mutation-reveal", disabled: "true",
  }, ["Reveal Code"]);
  const display = codeDisplay(code, false);

  const box = challengeBox("mutation",
    `Trigger ${target} DOM mutations to reveal the code.`,
    [btn, progress, mutationContainer, revealBtn, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      btn.addEventListener("click", () => {
        count++;
        mutationContainer.appendChild(h("div", { className: "text-xs text-gray-500 pl-2 border-l-2 border-rose-300" }, [`Mutation ${count}`]));
        progress.textContent = `Mutations triggered: ${count} / ${target}`;
        if (count >= target) {
          revealBtn.disabled = false;
          revealBtn.className = "mt-2 px-6 py-2 bg-rose-500 text-white font-semibold rounded hover:bg-rose-600 cursor-pointer";
        }
      });
      revealBtn.addEventListener("click", () => {
        if (count >= target) {
          display.style.display = "block";
          resolve({ type: "mutation", timestamp: Date.now(), data: { code, mutations: count } });
        }
      });
    }),
  };
}

// ===========================================================================
// 27. RECURSIVE_IFRAME — nested levels
// ===========================================================================
function createRecursiveIframe(container, code, step) {
  const levels = 3;

  const iframeContainer = h("div", { id: "iframe-container", className: "mt-3" });

  let parent = iframeContainer;
  for (let i = 1; i <= levels; i++) {
    const level = h("div", {
      className: "iframe-level p-3 m-2 border-2 border-emerald-400 rounded-lg",
      style: { background: `rgba(16,185,129,${0.05 * i})` },
    }, [
      h("p", { className: "font-bold text-sm" }, [`Level ${i}${i === levels ? " - You've reached the deepest level!" : ""}`]),
    ]);
    parent.appendChild(level);
    parent = level;
  }

  const btn = h("button", {
    className: "mt-2 px-6 py-2 bg-emerald-500 text-white font-semibold rounded hover:bg-emerald-600",
    id: "iframe-extract",
  }, ["Extract Code"]);
  const display = codeDisplay(code, false);

  const box = challengeBox("recursive_iframe",
    `Navigate through ${levels} nested levels to find the code at the deepest level.`,
    [iframeContainer, btn, display]);
  container.appendChild(box);

  return {
    onComplete: new Promise(resolve => {
      btn.addEventListener("click", () => {
        display.style.display = "block";
        resolve({ type: "recursive_iframe", timestamp: Date.now(), data: { code, levels } });
      });
    }),
  };
}

// ===========================================================================
// 28. CONDITIONAL_REVEAL — delayed with condition
// ===========================================================================
function createConditionalReveal(container, code, step) {
  return createDelayedReveal(container, code, step);
}

// ===========================================================================
// Factory registry
// ===========================================================================
const CHALLENGE_FACTORIES = {
  visible: createVisible,
  hidden_dom: createHiddenDom,
  click_reveal: createClickReveal,
  scroll_reveal: createScrollReveal,
  delayed_reveal: createDelayedReveal,
  drag_drop: createDragDrop,
  keyboard_sequence: createKeyboardSequence,
  memory: createMemory,
  hover_reveal: createHoverReveal,
  timing: createTiming,
  canvas: createCanvas,
  audio: createAudio,
  video: createVideo,
  split_parts: createSplitParts,
  encoded_base64: createEncodedBase64,
  rotating: createRotating,
  obfuscated: createObfuscated,
  multi_tab: createMultiTab,
  gesture: createGesture,
  sequence: createSequence,
  puzzle_solve: createPuzzleSolve,
  calculated: createCalculated,
  shadow_dom: createShadowDom,
  websocket: createWebsocket,
  service_worker: createServiceWorker,
  mutation: createMutation,
  recursive_iframe: createRecursiveIframe,
  conditional_reveal: createConditionalReveal,
};

window.Challenges = {
  create: (type, container, code, step) => {
    const factory = CHALLENGE_FACTORIES[type];
    if (!factory) {
      console.error(`Unknown challenge type: ${type}`);
      return createVisible(container, code, step);
    }
    return factory(container, code, step);
  },
  TYPES: Object.keys(CHALLENGE_FACTORIES),
  COLORS: CHALLENGE_COLORS,
};

export default window.Challenges;
