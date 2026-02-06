/**
 * Obstacle/distraction layer — popups, modals, fake buttons, floating elements.
 * Reproduces the original site's chaos layer exactly.
 */

const FAKE_NAV_LABELS = [
  "Next", "Continue", "Proceed", "Go Forward", "Next Page",
  "Click Here", "Continue Reading", "Next Step", "Move On", "Advance",
  "Keep Going", "Next Section", "Proceed Forward", "Continue Journey",
];

const RADIO_OPTIONS = [
  "Option A", "Option B", "Option C", "Option D",
  "Select this one", "Choose me", "This is correct",
  "Pick this option", "The right choice", "Correct answer",
  "Wrong option 1", "Wrong option 2", "Wrong option 3",
  "Incorrect choice", "Not this one",
];

const FLOATING_TEXTS = ["Click Me!", "Try This!", "Here!", "Button!", "Link!", "Click Here!"];

// ---------------------------------------------------------------------------
// Config per difficulty
// ---------------------------------------------------------------------------
const DEFAULT_DIFFICULTY = {
  cookieConsent: { probability: 0.8 },
  popups: { min: 3, max: 6, delays: [0, 500, 1000, 2000, 3000, 4000] },
  overlays: { probability: 0.7 },
  scrollableModals: { probability: 0.7, blockingProbability: 0.6 },
  fakeNavButtons: { min: 8, max: 15 },
  randomElements: { min: 5, max: 12 },
  movingElements: { probability: 0.5 },
  scrollableContent: { probability: 0.7 },
  dynamicContent: { probability: 0.9 },
};

function rand(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
function chance(p) { return Math.random() < p; }
function shuffle(arr) { const a = [...arr]; for (let i = a.length - 1; i > 0; i--) { const j = rand(0, i); [a[i], a[j]] = [a[j], a[i]]; } return a; }

// ---------------------------------------------------------------------------
// Cookie consent banner
// ---------------------------------------------------------------------------
function createCookieConsent(container) {
  const el = document.createElement("div");
  el.className = "obstacle-cookie";
  el.innerHTML = `
    <div class="cookie-banner">
      <p>🍪 This website uses cookies to enhance your experience.</p>
      <div class="cookie-buttons">
        <button class="btn-accept" onclick="this.closest('.obstacle-cookie').remove()">Accept</button>
        <button class="btn-decline" onclick="this.closest('.obstacle-cookie').remove()">Decline</button>
      </div>
    </div>`;
  container.appendChild(el);
}

// ---------------------------------------------------------------------------
// Popup modals (with fake/real close buttons)
// ---------------------------------------------------------------------------
const POPUP_CONFIGS = [
  { title: "Click here for amazing deals!", hasRealX: true, hasFakeClose: false, hasDismiss: false },
  { title: "Important Notice!", hasRealX: false, hasFakeClose: true, hasDismiss: true, fakeCloseNote: "The close button is fake! Look for another way to close." },
  { title: "Limited time offer!", hasRealX: false, hasFakeClose: true, hasDismiss: true, fakeCloseNote: "The X button is fake!" },
  { title: "Warning!", hasRealX: true, hasFakeClose: false, hasDismiss: false },
  { title: "Newsletter Signup", hasRealX: false, hasFakeClose: false, hasDismiss: false, hasClose: true },
  { title: "Special Announcement!", hasRealX: true, hasFakeClose: false, hasDismiss: true },
];

function createPopup(container, config, delay) {
  setTimeout(() => {
    const el = document.createElement("div");
    el.className = "obstacle-popup";
    el.style.zIndex = 10000 + rand(0, 100);

    let buttons = "";
    if (config.hasRealX) {
      buttons += `<button class="popup-x real-x" onclick="this.closest('.obstacle-popup').remove()" aria-label="close">&times;</button>`;
    }
    if (config.hasFakeClose) {
      buttons += `<button class="popup-x fake-x" onclick="/* fake */">&times;</button>`;
    }
    if (config.hasDismiss) {
      buttons += `<button class="btn-dismiss" onclick="this.closest('.obstacle-popup').remove()">Dismiss</button>`;
    }
    if (config.hasClose) {
      buttons += `<button class="btn-close" onclick="this.closest('.obstacle-popup').remove()">Close</button>`;
    }
    // If no dismiss method, add a generic close
    if (!config.hasRealX && !config.hasDismiss && !config.hasClose) {
      buttons += `<button class="btn-close" onclick="this.closest('.obstacle-popup').remove()">Close</button>`;
    }

    const note = config.fakeCloseNote ? `<p class="popup-note">${config.fakeCloseNote}</p>` : "";

    el.innerHTML = `
      <div class="popup-content" style="top:${rand(5,30)}%;left:${rand(10,60)}%">
        ${buttons}
        <h3>${config.title}</h3>
        ${note}
        <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit.</p>
      </div>`;
    container.appendChild(el);
  }, delay);
}

// ---------------------------------------------------------------------------
// Scrollable blocking modal with radio selection
// ---------------------------------------------------------------------------
function createBlockingModal(container, stepNum, onComplete) {
  const el = document.createElement("div");
  el.className = "obstacle-blocking-modal";
  el.style.zIndex = 20000;

  // Correct option cycles A-D based on step
  const correctLetter = String.fromCharCode(65 + (stepNum % 4));
  const correctOption = `Option ${correctLetter} - Correct Choice`;

  // Build option list: correct + shuffled decoys
  const decoys = shuffle(RADIO_OPTIONS).slice(0, 8);
  const options = shuffle([correctOption, ...decoys]);

  // Lorem filler before and after options
  const filler = Array.from({ length: 10 }, (_, i) =>
    `<p class="modal-filler">Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Section ${i + 1}.</p>`
  ).join("");

  const radioHTML = options.map((opt, i) => `
    <label class="radio-option" role="radio" aria-checked="false">
      <input type="radio" name="modal-radio" value="${opt}">
      <span>${opt}</span>
    </label>
  `).join("");

  el.innerHTML = `
    <div class="blocking-modal-overlay"></div>
    <div class="blocking-modal-content">
      <h3>Please Select an Option</h3>
      <div class="modal-scroll-area">
        ${filler}
        <div class="radio-group">${radioHTML}</div>
        ${filler}
      </div>
      <button class="btn-submit-modal" disabled>Submit &amp; Continue</button>
    </div>`;

  const submitBtn = el.querySelector(".btn-submit-modal");
  const radios = el.querySelectorAll('input[type="radio"]');

  radios.forEach(r => {
    r.addEventListener("change", () => {
      submitBtn.disabled = false;
      // Update aria-checked
      el.querySelectorAll('[role="radio"]').forEach(l => l.setAttribute("aria-checked", "false"));
      r.closest('[role="radio"]').setAttribute("aria-checked", "true");

      // Reshuffle options on selection (like live site) — selected stays selected
      const group = el.querySelector(".radio-group");
      const labels = [...group.querySelectorAll(".radio-option")];
      const selectedLabel = r.closest(".radio-option");
      const others = labels.filter(l => l !== selectedLabel);
      const shuffled = shuffle(others);
      // Re-append in random order — the selected one keeps its checked state
      shuffled.forEach(o => group.appendChild(o));
      group.appendChild(selectedLabel); // put selected at end (or random spot)
      // Actually randomise the selected position too
      const allLabels = [...group.querySelectorAll(".radio-option")];
      const insertIdx = rand(0, allLabels.length - 1);
      group.insertBefore(selectedLabel, allLabels[insertIdx]);
    });
  });

  submitBtn.addEventListener("click", () => {
    const selected = el.querySelector('input[name="modal-radio"]:checked');
    if (selected && selected.value === correctOption) {
      el.remove();
      onComplete?.();
    }
    // Wrong answer — no extra reshuffle needed, selection already reshuffles
  });

  container.appendChild(el);
}

// ---------------------------------------------------------------------------
// Fake navigation buttons
// ---------------------------------------------------------------------------
function createFakeNavButtons(container, count) {
  const wrapper = document.createElement("div");
  wrapper.className = "fake-nav-buttons";

  const labels = shuffle(FAKE_NAV_LABELS).slice(0, count);
  labels.forEach(label => {
    const btn = document.createElement("button");
    btn.className = "btn-fake-nav";
    btn.textContent = label;
    btn.addEventListener("click", () => {
      // Scroll randomly — just a distraction
      window.scrollBy(0, rand(-200, 200));
    });
    // Random positioning
    btn.style.position = "relative";
    wrapper.appendChild(btn);
  });

  container.appendChild(wrapper);
}

// ---------------------------------------------------------------------------
// Floating distractions
// ---------------------------------------------------------------------------
function createFloatingDistractions(container, count) {
  for (let i = 0; i < count; i++) {
    const el = document.createElement("div");
    el.className = "floating-distraction";
    el.textContent = FLOATING_TEXTS[i % FLOATING_TEXTS.length];
    el.style.top = `${rand(5, 85)}%`;
    el.style.left = `${rand(5, 85)}%`;
    el.style.animationDelay = `${rand(0, 5)}s`;
    el.style.animationDuration = `${rand(3, 8)}s`;
    container.appendChild(el);
  }
}

// ---------------------------------------------------------------------------
// Filler content (100 sections)
// ---------------------------------------------------------------------------
function createFillerContent(container) {
  const wrapper = document.createElement("div");
  wrapper.className = "filler-content";
  for (let i = 1; i <= 100; i++) {
    const section = document.createElement("div");
    section.className = "filler-section";
    section.innerHTML = `<h4>Section ${i}</h4><p>This is filler content. Keep scrolling to find the navigation button.</p>`;
    wrapper.appendChild(section);
  }
  container.appendChild(wrapper);
}

// ---------------------------------------------------------------------------
// Dynamic content loading
// ---------------------------------------------------------------------------
function createDynamicContent(container) {
  const delays = [500, 1500, 2500, 3500];
  delays.forEach(delay => {
    setTimeout(() => {
      const el = document.createElement("div");
      el.className = "dynamic-content-item";
      el.textContent = `This content appeared ${delay}ms after page load`;
      container.appendChild(el);
    }, delay);
  });
}

// ---------------------------------------------------------------------------
// Master function: spawn all obstacles for a step
// ---------------------------------------------------------------------------
function spawnObstacles(container, stepNum, opts = {}) {
  const config = { ...DEFAULT_DIFFICULTY, ...opts };

  // Always create floating distractions
  createFloatingDistractions(container, rand(config.randomElements.min, config.randomElements.max));

  // Cookie consent
  if (chance(config.cookieConsent.probability)) {
    createCookieConsent(container);
  }

  // Popups with staggered delays
  const popupCount = rand(config.popups.min, config.popups.max);
  const popupConfigs = shuffle(POPUP_CONFIGS).slice(0, popupCount);
  popupConfigs.forEach((pc, i) => {
    const delay = config.popups.delays[i] || 0;
    createPopup(container, pc, delay);
  });

  // Filler content
  if (chance(config.scrollableContent.probability)) {
    createFillerContent(container);
  }

  // Fake nav buttons
  createFakeNavButtons(container, rand(config.fakeNavButtons.min, config.fakeNavButtons.max));

  // Dynamic content
  if (chance(config.dynamicContent.probability)) {
    createDynamicContent(container);
  }

  // Return a promise that resolves when blocking modal is dismissed
  return new Promise(resolve => {
    if (chance(config.scrollableModals.probability)) {
      createBlockingModal(container, stepNum, resolve);
    } else {
      resolve();
    }
  });
}

window.Obstacles = {
  spawnObstacles,
  createCookieConsent,
  createPopup,
  createBlockingModal,
  createFakeNavButtons,
  createFloatingDistractions,
  createFillerContent,
  createDynamicContent,
};

export default window.Obstacles;
