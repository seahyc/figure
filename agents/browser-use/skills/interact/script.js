(function(options) {
    var opts = Object.assign({probeOnly: false}, options || {});

    var result = {
        ok: false,
        clickTargets: [],
        hoverCoords: [],
        keySequence: [],
        dragPairs: [],
        canvasStrokes: [],
        scrollTarget: 0,
        typeActions: [],
        actions: [],
        detected: [],
        pageInfo: {}
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
    // PAGE STRUCTURAL INFO — generic page feature scanner
    // ═══════════════════════════════════════════════════════════════
    var bodyText = document.body ? document.body.innerText.substring(0, 3000) : '';

    result.pageInfo = {
        url: window.location.href,
        title: document.title,
        hasCanvas: !!document.querySelector('canvas'),
        hasForm: !!document.querySelector('form'),
        hasDraggables: !!document.querySelector('[draggable="true"]'),
        hasShadowRoots: false,
        hasIframes: document.querySelectorAll('iframe').length > 0,
        inputCount: document.querySelectorAll('input').length,
        buttonCount: document.querySelectorAll('button').length,
        buttonTexts: [],
        visibleTextPreview: bodyText.substring(0, 500)
    };

    // Collect visible button texts
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
    // EXECUTE RUNTIME SKILLS — stored on window.__runtimeSkills
    // ═══════════════════════════════════════════════════════════════
    if (!opts.probeOnly && window.__runtimeSkills) {
        var skills = window.__runtimeSkills;
        for (var sName in skills) {
            var sk = skills[sName];
            if (!sk || !sk.fn) continue;
            try {
                if (sk.match && !sk.match()) continue;
            } catch(e) { continue; }
            try {
                var skResult = sk.fn(opts);
                if (skResult) {
                    var parsed = typeof skResult === 'string' ? JSON.parse(skResult) : skResult;
                    if (parsed.clickTargets) result.clickTargets = result.clickTargets.concat(parsed.clickTargets);
                    if (parsed.hoverCoords) result.hoverCoords = result.hoverCoords.concat(parsed.hoverCoords);
                    if (parsed.keySequence) result.keySequence = result.keySequence.concat(parsed.keySequence);
                    if (parsed.dragPairs) result.dragPairs = result.dragPairs.concat(parsed.dragPairs);
                    if (parsed.canvasStrokes) result.canvasStrokes = result.canvasStrokes.concat(parsed.canvasStrokes);
                    if (parsed.actions) result.actions = result.actions.concat(parsed.actions);
                    result.detected.push('runtime:' + sName);
                }
            } catch(e) {
                result.detected.push('runtime-error:' + sName + ':' + e.message);
            }
        }
    }

    result.ok = result.clickTargets.length > 0 || result.hoverCoords.length > 0 ||
                result.keySequence.length > 0 || result.dragPairs.length > 0 ||
                result.canvasStrokes.length > 0;
    return JSON.stringify(result);
})
