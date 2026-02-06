/**
 * Main app — routing, step lifecycle, code submission.
 *
 * URL params:
 *   ?step=N         Jump to step N (1-30)
 *   ?type=X         Test a specific challenge type
 *   ?obstacles=0    Disable obstacle layer
 *   ?version=N      Challenge version (changes type mapping)
 *   ?debug=1        Show debug panel with all codes
 */

const root = document.getElementById("root");

function getParams() {
  const p = new URLSearchParams(window.location.search);
  return {
    step: parseInt(p.get("step")) || 0,
    type: p.get("type") || null,
    obstacles: p.get("obstacles") !== "0",
    version: parseInt(p.get("version")) || 1,
    debug: p.get("debug") === "1",
  };
}

// ---------------------------------------------------------------------------
// Landing page
// ---------------------------------------------------------------------------
function renderLanding() {
  root.innerHTML = "";
  root.className = "landing";
  root.innerHTML = `
    <div class="landing-content">
      <h1>🌐 Browser Navigation Challenge 🌐</h1>
      <p class="subtitle">The Ultimate Test for Browser Automation</p>
      <button class="btn-start" id="start-btn">START</button>
      <div class="landing-options">
        <h3>Quick Jump</h3>
        <div class="step-grid" id="step-grid"></div>
        <h3>Test by Type</h3>
        <div class="type-grid" id="type-grid"></div>
      </div>
    </div>`;

  // Step grid
  const stepGrid = document.getElementById("step-grid");
  for (let i = 1; i <= window.ChallengeEngine.TOTAL_STEPS; i++) {
    const type = window.ChallengeEngine.getChallengeType(i, getParams().version);
    const btn = document.createElement("a");
    btn.href = `?step=${i}`;
    btn.className = "step-link";
    btn.title = type;
    btn.textContent = `${i}`;
    stepGrid.appendChild(btn);
  }

  // Type grid
  const typeGrid = document.getElementById("type-grid");
  window.Challenges.TYPES.forEach(type => {
    const btn = document.createElement("a");
    btn.href = `?type=${type}`;
    btn.className = "type-link";
    btn.textContent = type;
    typeGrid.appendChild(btn);
  });

  document.getElementById("start-btn").addEventListener("click", () => {
    window.location.href = "?step=1";
  });
}

// ---------------------------------------------------------------------------
// Step page
// ---------------------------------------------------------------------------
async function renderStep(stepNum, challengeType = null) {
  const params = getParams();
  const engine = window.ChallengeEngine;

  // Init session
  engine.init();
  engine.startChallenge(stepNum);

  // Get step config
  const config = engine.getStepConfig(stepNum, params.version);
  const type = challengeType || config.type;
  // Live site's Gv wrapper calls markChallengeComplete(step, proof) which returns
  // codes.get(step+1). So challenges display the NEXT step's code, which is what
  // validateCode(step, code) checks. Replicate that here.
  const code = engine.session.codes.get(stepNum + 1) || config.code;

  root.innerHTML = "";
  root.className = "step-page";

  // Header
  const header = document.createElement("div");
  header.className = "step-header";
  header.innerHTML = `
    <div class="step-info">
      <span class="step-badge">Step ${stepNum} / ${engine.TOTAL_STEPS}</span>
      <span class="step-type">${type}</span>
    </div>
    <div class="step-timer" id="step-timer">0:00</div>`;
  root.appendChild(header);

  // Timer
  const timerStart = Date.now();
  const timerEl = header.querySelector("#step-timer");
  const timerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - timerStart) / 1000);
    const min = Math.floor(elapsed / 60);
    const sec = elapsed % 60;
    timerEl.textContent = `${min}:${sec.toString().padStart(2, "0")}`;
  }, 1000);

  // Main content area — matches live site z-index layering
  const content = document.createElement("div");
  content.className = "step-content max-w-6xl mx-auto p-10 bg-white/90 rounded-2xl shadow-2xl min-h-[500px] relative z-[100]";
  root.appendChild(content);

  // Obstacle layer (unless disabled)
  const obstacleContainer = document.createElement("div");
  obstacleContainer.className = "obstacle-layer";
  root.appendChild(obstacleContainer);

  if (params.obstacles) {
    await window.Obstacles.spawnObstacles(obstacleContainer, stepNum);
  }

  // Challenge
  const challengeContainer = document.createElement("div");
  challengeContainer.className = "challenge-container";
  content.appendChild(challengeContainer);

  const challenge = window.Challenges.create(type, challengeContainer, code, stepNum);

  // Variable-height spacer between challenge and code entry (like live site)
  const spacer = document.createElement("div");
  const spacerHeight = Math.floor(Math.random() * 300) + 50; // 50–350px
  spacer.style.height = `${spacerHeight}px`;
  content.appendChild(spacer);

  // Code entry section — matches live site z-index and structure
  const codeEntry = document.createElement("div");
  codeEntry.className = "code-entry-section mt-6 p-6 bg-white rounded-xl shadow-lg text-center relative z-[10002]";
  codeEntry.innerHTML = `
    <h3 class="text-lg font-semibold mb-3">Enter Code to Proceed to Step ${stepNum + 1}:</h3>
    <input type="text" id="code-input" placeholder="Enter 6-character code" maxlength="6" autocomplete="off"
           class="px-4 py-2 border border-gray-300 rounded font-mono text-xl text-center uppercase tracking-widest w-48">
    <button class="btn-submit-code ml-3 px-6 py-2 bg-blue-500 text-white font-semibold rounded hover:bg-blue-600" id="submit-code">Submit Code</button>
    <p class="code-feedback mt-2 font-semibold min-h-[20px]" id="code-feedback"></p>`;
  content.appendChild(codeEntry);

  // Code submission
  document.getElementById("submit-code").addEventListener("click", () => {
    const userCode = document.getElementById("code-input").value.trim().toUpperCase();
    const feedback = document.getElementById("code-feedback");

    if (userCode.length !== 6) {
      feedback.textContent = "Code must be 6 characters.";
      feedback.style.color = "#ef4444";
      return;
    }

    // Mark challenge complete with proof
    const proof = { type, timestamp: Date.now(), data: { userCode } };
    engine.markComplete(stepNum, proof);

    // Step 30: codes.get(31) doesn't exist, so validation always fails.
    // Live site has same bug — navigating to /finish is the workaround.
    const isLastStep = stepNum >= engine.TOTAL_STEPS;
    if (isLastStep || engine.validateCode(stepNum, userCode)) {
      clearInterval(timerInterval);
      feedback.textContent = "Correct! Advancing...";
      feedback.style.color = "#22c55e";

      // Beacon to server for test harness tracking
      try {
        fetch("/api/complete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ step: stepNum, type, code: userCode }),
        }).catch(() => {});
      } catch (_) {}

      const nextStep = stepNum + 1;
      if (nextStep > engine.TOTAL_STEPS) {
        setTimeout(() => renderFinish(), 500);
      } else {
        setTimeout(() => {
          window.history.pushState({}, "", `?step=${nextStep}${params.obstacles ? "" : "&obstacles=0"}${params.debug ? "&debug=1" : ""}&version=${params.version}`);
          renderStep(nextStep);
        }, 500);
      }
    } else {
      feedback.textContent = "Incorrect code. Try again.";
      feedback.style.color = "#ef4444";
    }
  });

  // Debug panel
  if (params.debug) {
    const debugPanel = document.createElement("div");
    debugPanel.className = "debug-panel";
    const allCodes = engine.getAllCodes();
    debugPanel.innerHTML = `
      <h4>Debug Panel</h4>
      <p>Step ${stepNum} type: ${type}</p>
      <p>Step ${stepNum} code: ${code}</p>
      <p>Validation expects (step+1): ${engine.session.codes.get(stepNum + 1) || "N/A"}</p>
      <details>
        <summary>All Codes</summary>
        <pre>${allCodes.map(c => `Step ${c.step}: ${c.code} (${engine.getChallengeType(c.step, params.version)})`).join("\n")}</pre>
      </details>`;
    root.appendChild(debugPanel);
  }

  // Nav links
  const nav = document.createElement("div");
  nav.className = "step-nav";
  nav.innerHTML = `
    <a href="?" class="nav-link">← Home</a>
    ${stepNum > 1 ? `<a href="?step=${stepNum - 1}${params.obstacles ? "" : "&obstacles=0"}${params.debug ? "&debug=1" : ""}" class="nav-link">← Prev</a>` : ""}
    ${stepNum < engine.TOTAL_STEPS ? `<a href="?step=${stepNum + 1}${params.obstacles ? "" : "&obstacles=0"}${params.debug ? "&debug=1" : ""}" class="nav-link">Next →</a>` : ""}`;
  root.appendChild(nav);
}

// ---------------------------------------------------------------------------
// Finish page
// ---------------------------------------------------------------------------
function renderFinish() {
  root.innerHTML = "";
  root.className = "finish-page";
  root.innerHTML = `
    <div class="finish-content">
      <h1>🎉 Congratulations! 🎉</h1>
      <h2>All Steps Completed!</h2>
      <p>You've successfully completed all ${window.ChallengeEngine.TOTAL_STEPS} steps of the Browser Navigation Challenge.</p>
      <p>Well done!</p>
      <a href="?" class="btn-start">Start Over</a>
    </div>`;
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------
function route() {
  const params = getParams();

  if (params.type) {
    // Test a specific challenge type (uses step 1 config with override)
    window.ChallengeEngine.init();
    renderStep(1, params.type);
  } else if (params.step > 0) {
    renderStep(params.step);
  } else {
    renderLanding();
  }
}

// Handle back/forward
window.addEventListener("popstate", route);

// Initial render
route();
