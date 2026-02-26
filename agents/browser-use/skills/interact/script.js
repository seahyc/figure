(function(options) {
    var opts = Object.assign({probeOnly: false}, options || {});

    var result = {
        ok: false,
        codes: [],
        clickTargets: [],    // [{x, y, label, waitAfter}] for Playwright
        hoverCoords: [],     // [{x, y, durationMs}] for Playwright hover
        keySequence: [],     // ['ArrowUp', 'ArrowDown'] for Playwright keyboard
        dragPairs: [],       // [{srcX, srcY, tgtX, tgtY}] for Playwright drag
        canvasStrokes: [],   // [{startX, startY, endX, endY}] for Playwright
        scrollTarget: 0,     // px to scroll window
        typeActions: [],     // [{selector, value}] for typing
        actions: [],
        detected: [],
        pageInfo: {}         // structural info for planner
    };

    // ── Helpers ──────────────────────────────────────────────────────
    function isVis(el) {
        if (!el || !el.getBoundingClientRect) return false;
        var r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
        var s = window.getComputedStyle(el);
        return s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity) > 0;
    }

    function xy(el) {
        if (!el || !isVis(el)) return null;
        el.scrollIntoView({block: 'center', behavior: 'instant'});
        var r = el.getBoundingClientRect();
        return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
    }

    // ═══════════════════════════════════════════════════════════════
    // PAGE STRUCTURAL INFO — feed to planner for skill generation
    // ═══════════════════════════════════════════════════════════════
    var bodyText = document.body ? document.body.innerText.substring(0, 3000) : '';

    result.pageInfo = {
        url: window.location.href,
        title: document.title,
        hasCanvas: !!document.querySelector('canvas'),
        hasForm: !!document.querySelector('form'),
        hasDraggables: !!document.querySelector('[draggable="true"]'),
        hasShadowRoots: false,
        hasIframes: document.querySelectorAll('iframe').length,
        inputCount: document.querySelectorAll('input').length,
        buttonCount: document.querySelectorAll('button').length,
        buttonTexts: [],
        visibleTextPreview: (function() {
            // Try to get challenge-specific text from high z-index containers
            var challengeText = '';
            document.querySelectorAll('div').forEach(function(el) {
                var cls = el.className || '';
                if (typeof cls !== 'string') return;
                // Match z-[10000+] containers (challenge UIs use z-10002 to z-10005)
                if (/z-\[100\d\d\]/.test(cls) || /relative/.test(cls) && /z-/.test(cls)) {
                    var t = el.innerText || '';
                    if (t.length > 20 && t.length < 1000 && !/^Section \d+/.test(t.substring(0, 50))) {
                        challengeText += t.substring(0, 300) + ' ';
                    }
                }
            });
            if (challengeText.length > 50) return challengeText.substring(0, 600);
            return bodyText.substring(0, 500);
        })()
    };

    // Collect visible button texts (skip decoys — too many)
    var btns = document.querySelectorAll('button');
    var uniqueBtnTexts = new Set();
    for (var i = 0; i < btns.length && uniqueBtnTexts.size < 15; i++) {
        var t = btns[i].textContent.trim();
        if (t && isVis(btns[i]) && !btns[i].disabled && t.length < 30) uniqueBtnTexts.add(t);
    }
    result.pageInfo.buttonTexts = Array.from(uniqueBtnTexts);

    // Check for shadow roots
    var allEls = document.querySelectorAll('*');
    for (var i = 0; i < allEls.length; i++) {
        if (allEls[i].shadowRoot) { result.pageInfo.hasShadowRoots = true; break; }
    }

    // ═══════════════════════════════════════════════════════════════
    // EXECUTE RUNTIME SKILLS — planner-generated, stored on window
    // ═══════════════════════════════════════════════════════════════
    if (!opts.probeOnly && window.__runtimeSkills) {
        var skills = window.__runtimeSkills;
        for (var sName in skills) {
            var sk = skills[sName];
            if (!sk || !sk.fn) continue;
            // Check match predicate
            try {
                if (sk.match && !sk.match()) continue;
            } catch(e) { continue; }
            // Execute skill
            try {
                var skResult = sk.fn(opts);
                if (skResult) {
                    var parsed = typeof skResult === 'string' ? JSON.parse(skResult) : skResult;
                    // Merge results
                    if (parsed.clickTargets) result.clickTargets = result.clickTargets.concat(parsed.clickTargets);
                    if (parsed.hoverCoords) result.hoverCoords = result.hoverCoords.concat(parsed.hoverCoords);
                    if (parsed.keySequence) result.keySequence = result.keySequence.concat(parsed.keySequence);
                    if (parsed.dragPairs) result.dragPairs = result.dragPairs.concat(parsed.dragPairs);
                    if (parsed.canvasStrokes) result.canvasStrokes = result.canvasStrokes.concat(parsed.canvasStrokes);
                    if (parsed.codes) {
                        for (var c = 0; c < parsed.codes.length; c++) {
                            result.codes.push(parsed.codes[c]);
                        }
                    }
                    if (parsed.actions) result.actions = result.actions.concat(parsed.actions);
                    result.detected.push('runtime:' + sName);
                }
            } catch(e) {
                result.detected.push('runtime-error:' + sName + ':' + e.message);
            }
        }
    }

    // ═══════════════════════════════════════════════════════════════
    // CODE EXTRACTION — scan all common hiding spots (generic)
    // ═══════════════════════════════════════════════════════════════
    var codeRE = /[A-HJ-NP-Z2-9]{6}/g;
    var seenCodes = new Set();
    var FALSE_POS = new Set(['SUBMIT','BUTTON','HIDDEN','SCROLL','CANVAS','COOKIE','ACCEPT','REJECT',
        'OPTION','SELECT','SEARCH','FILTER','NUMBER','REVEAL','SHADOW','WORKER','RENDER','CHANGE',
        'PARENT','RETURN','ESCAPE','CURSOR','SCREEN','CHROME','PLEASE','VERIFY','ANSWER','RESULT',
        'ABCDEF','DECODE']);

    function addCode(text, source) {
        if (!text) return;
        var m = String(text).match(codeRE);
        if (!m) return;
        for (var i = 0; i < m.length; i++) {
            if (seenCodes.has(m[i]) || FALSE_POS.has(m[i]) || !/[A-Z]/.test(m[i])) continue;
            seenCodes.add(m[i]);
            result.codes.push({text: m[i], source: source});
        }
    }

    // High-priority: data-* attributes
    for (var i = 0; i < allEls.length; i++) {
        var attrs = allEls[i].attributes;
        for (var j = 0; j < attrs.length; j++) {
            if (attrs[j].name.indexOf('data-') === 0) addCode(attrs[j].value, 'data-attr');
        }
    }
    // aria labels
    var ariaEls = document.querySelectorAll('[aria-label],[aria-description]');
    for (var i = 0; i < ariaEls.length; i++) {
        addCode(ariaEls[i].getAttribute('aria-label'), 'aria');
        addCode(ariaEls[i].getAttribute('aria-description'), 'aria');
    }
    // meta tags
    var metas = document.querySelectorAll('meta[content]');
    for (var i = 0; i < metas.length; i++) addCode(metas[i].content, 'meta');
    // HTML comments
    try {
        var walker = document.createTreeWalker(document.body || document, NodeFilter.SHOW_COMMENT);
        var node; while (node = walker.nextNode()) addCode(node.textContent, 'comment');
    } catch(e) {}
    // Hidden elements
    var hidden = document.querySelectorAll('[style*="display:none"],[style*="display: none"],[hidden],.hidden,[style*="opacity:0"],[style*="opacity: 0"]');
    for (var i = 0; i < hidden.length; i++) addCode(hidden[i].textContent, 'hidden');
    // CSS pseudo-elements
    var styled = document.querySelectorAll('body *');
    for (var i = 0; i < styled.length && i < 300; i++) {
        try {
            var before = window.getComputedStyle(styled[i], '::before').content;
            if (before && before !== 'none' && before !== 'normal') addCode(before.replace(/['"]/g,''), 'css');
            var after = window.getComputedStyle(styled[i], '::after').content;
            if (after && after !== 'none' && after !== 'normal') addCode(after.replace(/['"]/g,''), 'css');
        } catch(e) {}
    }
    // Shadow DOM
    for (var i = 0; i < allEls.length; i++) {
        if (allEls[i].shadowRoot) {
            addCode(allEls[i].shadowRoot.textContent, 'shadow');
            var nested = allEls[i].shadowRoot.querySelectorAll('*');
            for (var j = 0; j < nested.length; j++) {
                if (nested[j].shadowRoot) addCode(nested[j].shadowRoot.textContent, 'shadow-nested');
            }
        }
    }
    // Visible text (low priority — may contain false positives)
    addCode(document.body ? document.body.innerText : '', 'visible');

    result.ok = result.codes.length > 0 || result.clickTargets.length > 0 ||
                result.hoverCoords.length > 0 || result.keySequence.length > 0 ||
                result.dragPairs.length > 0 || result.canvasStrokes.length > 0;
    return JSON.stringify(result);
})
