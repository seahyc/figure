(function(options) {
    var opts = Object.assign({
        pattern: null,
        maxResults: 20,
        searchAreas: null
    }, options || {});

    var matches = [];
    var re = opts.pattern ? new RegExp(opts.pattern, 'g') : null;
    var areas = opts.searchAreas ? new Set(opts.searchAreas) : null;

    function shouldSearch(area) {
        return !areas || areas.has(area);
    }

    function addMatch(text, source, selector, visible) {
        if (matches.length >= opts.maxResults) return;
        text = (text || '').trim();
        if (!text || text.length < 2 || text.length > 500) return;
        // Deduplicate
        for (var i = 0; i < matches.length; i++) {
            if (matches[i].text === text && matches[i].source === source) return;
        }
        if (re) {
            var found = text.match(re);
            if (!found) return;
            // Add each regex match separately
            for (var j = 0; j < found.length && matches.length < opts.maxResults; j++) {
                var dup = false;
                for (var k = 0; k < matches.length; k++) {
                    if (matches[k].text === found[j]) { dup = true; break; }
                }
                if (!dup) {
                    matches.push({text: found[j], source: source, selector: selector, visible: visible});
                }
            }
        } else {
            matches.push({text: text.substring(0, 200), source: source, selector: selector, visible: visible});
        }
    }

    function isHidden(el) {
        if (!el || !el.getBoundingClientRect) return true;
        var style = window.getComputedStyle(el);
        return style.display === 'none' || style.visibility === 'hidden' ||
               style.opacity === '0' || el.offsetWidth === 0 || el.offsetHeight === 0;
    }

    function getSelector(el) {
        if (!el || !el.tagName) return '';
        var tag = el.tagName.toLowerCase();
        if (el.id) return tag + '#' + el.id;
        if (el.className && typeof el.className === 'string') {
            var cls = el.className.trim().split(/\s+/).slice(0, 2).join('.');
            if (cls) return tag + '.' + cls;
        }
        return tag;
    }

    // 1. Data attributes
    if (shouldSearch('data_attrs')) {
        var allEls = document.querySelectorAll('*');
        for (var i = 0; i < allEls.length && matches.length < opts.maxResults; i++) {
            var el = allEls[i];
            var attrs = el.attributes;
            for (var j = 0; j < attrs.length; j++) {
                var attr = attrs[j];
                if (attr.name.indexOf('data-') === 0 && attr.name !== 'data-filtered' &&
                    attr.name !== 'data-aria-ref' && attr.name !== 'data-testid') {
                    addMatch(attr.value, 'data-attr:' + attr.name, getSelector(el), !isHidden(el));
                }
            }
        }
    }

    // 2. Hidden elements
    if (shouldSearch('hidden_elements')) {
        var hidden = document.querySelectorAll('[style*="display:none"],[style*="display: none"],[style*="visibility:hidden"],[style*="visibility: hidden"],[style*="opacity:0"],[style*="opacity: 0"],.hidden,[hidden]');
        for (var i = 0; i < hidden.length && matches.length < opts.maxResults; i++) {
            var text = hidden[i].textContent;
            if (text) addMatch(text, 'hidden-element', getSelector(hidden[i]), false);
        }
        // Also check computed styles for elements not caught by attribute selectors
        var allVisible = document.querySelectorAll('body *');
        for (var i = 0; i < allVisible.length && matches.length < opts.maxResults; i++) {
            var el = allVisible[i];
            if (el.children.length > 3) continue; // skip containers
            if (isHidden(el) && el.textContent.trim().length > 0 && el.textContent.trim().length < 200) {
                addMatch(el.textContent, 'computed-hidden', getSelector(el), false);
            }
        }
    }

    // 3. CSS pseudo-element content
    if (shouldSearch('css_content')) {
        var styled = document.querySelectorAll('body *');
        for (var i = 0; i < styled.length && matches.length < opts.maxResults; i++) {
            try {
                var before = window.getComputedStyle(styled[i], '::before').content;
                if (before && before !== 'none' && before !== 'normal' && before !== '""') {
                    var clean = before.replace(/^["']|["']$/g, '');
                    addMatch(clean, 'css-before', getSelector(styled[i]), true);
                }
                var after = window.getComputedStyle(styled[i], '::after').content;
                if (after && after !== 'none' && after !== 'normal' && after !== '""') {
                    var clean = after.replace(/^["']|["']$/g, '');
                    addMatch(clean, 'css-after', getSelector(styled[i]), true);
                }
            } catch(e) {}
        }
    }

    // 4. HTML comments
    if (shouldSearch('comments')) {
        var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_COMMENT, null, false);
        var node;
        while ((node = walker.nextNode()) && matches.length < opts.maxResults) {
            addMatch(node.textContent, 'html-comment', 'comment', false);
        }
    }

    // 5. Meta / title / aria
    if (shouldSearch('meta')) {
        // Meta tags
        var metas = document.querySelectorAll('meta[content]');
        for (var i = 0; i < metas.length && matches.length < opts.maxResults; i++) {
            var name = metas[i].getAttribute('name') || metas[i].getAttribute('property') || '';
            addMatch(metas[i].content, 'meta:' + name, 'meta', true);
        }
        // Title attributes
        var titled = document.querySelectorAll('[title]');
        for (var i = 0; i < titled.length && matches.length < opts.maxResults; i++) {
            addMatch(titled[i].title, 'title-attr', getSelector(titled[i]), !isHidden(titled[i]));
        }
        // Aria labels that might contain hidden info
        var ariaEls = document.querySelectorAll('[aria-label],[aria-describedby],[aria-description]');
        for (var i = 0; i < ariaEls.length && matches.length < opts.maxResults; i++) {
            var label = ariaEls[i].getAttribute('aria-label');
            if (label) addMatch(label, 'aria-label', getSelector(ariaEls[i]), !isHidden(ariaEls[i]));
            var desc = ariaEls[i].getAttribute('aria-description');
            if (desc) addMatch(desc, 'aria-description', getSelector(ariaEls[i]), !isHidden(ariaEls[i]));
        }
        // Alt text
        var imgs = document.querySelectorAll('[alt]');
        for (var i = 0; i < imgs.length && matches.length < opts.maxResults; i++) {
            addMatch(imgs[i].alt, 'alt-text', getSelector(imgs[i]), !isHidden(imgs[i]));
        }
    }

    // 6. Script data
    if (shouldSearch('scripts')) {
        var scripts = document.querySelectorAll('script[type="application/json"],script[type="application/ld+json"],script:not([src])');
        for (var i = 0; i < scripts.length && matches.length < opts.maxResults; i++) {
            var content = scripts[i].textContent;
            if (content && content.length < 5000 && content.length > 5) {
                if (re) {
                    addMatch(content, 'script-data', 'script', false);
                }
            }
        }
    }

    // 7. Noscript content
    if (shouldSearch('noscript')) {
        var noscripts = document.querySelectorAll('noscript');
        for (var i = 0; i < noscripts.length && matches.length < opts.maxResults; i++) {
            addMatch(noscripts[i].textContent, 'noscript', 'noscript', false);
        }
    }

    // 8. Input values (hidden inputs, readonly inputs)
    var inputs = document.querySelectorAll('input[type="hidden"],input[readonly],input[disabled]');
    for (var i = 0; i < inputs.length && matches.length < opts.maxResults; i++) {
        if (inputs[i].value) {
            addMatch(inputs[i].value, 'hidden-input', getSelector(inputs[i]), false);
        }
    }

    // 9. Shadow DOM content
    try {
        var allShadow = document.querySelectorAll('*');
        for (var i = 0; i < allShadow.length && matches.length < opts.maxResults; i++) {
            if (allShadow[i].shadowRoot) {
                var shadowText = allShadow[i].shadowRoot.textContent;
                if (shadowText) addMatch(shadowText, 'shadow-dom', getSelector(allShadow[i]), true);
            }
        }
    } catch(e) {}

    // 10. Iframe content (same-origin only)
    try {
        var iframes = document.querySelectorAll('iframe');
        for (var i = 0; i < iframes.length && matches.length < opts.maxResults; i++) {
            try {
                var iframeDoc = iframes[i].contentDocument || iframes[i].contentWindow.document;
                if (iframeDoc && iframeDoc.body) {
                    var iframeText = iframeDoc.body.textContent;
                    if (iframeText) addMatch(iframeText, 'iframe', 'iframe', true);
                }
            } catch(e) {} // cross-origin will throw
        }
    } catch(e) {}

    // 11. Visible text content (scan leaf elements for pattern matches)
    if (re && shouldSearch('visible_text') !== false) {
        // Search text nodes in leaf-ish elements (few or no children)
        var leafEls = document.querySelectorAll('span, p, div, td, th, li, label, h1, h2, h3, h4, h5, h6, strong, em, b, i, code, pre, a, dt, dd');
        for (var i = 0; i < leafEls.length && matches.length < opts.maxResults; i++) {
            var el = leafEls[i];
            if (el.children.length > 3) continue; // skip containers
            var style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            var text = el.textContent.trim();
            if (text.length >= 3 && text.length <= 200) {
                addMatch(text, 'visible-text', getSelector(el), true);
            }
        }
    }

    // 12. Full body text scan (last resort — scans entire body text)
    if (re && matches.length === 0) {
        var bodyText = document.body ? document.body.innerText : '';
        if (bodyText) {
            var bodyMatches = bodyText.match(re);
            if (bodyMatches) {
                for (var i = 0; i < bodyMatches.length && matches.length < opts.maxResults; i++) {
                    addMatch(bodyMatches[i], 'body-text-scan', 'body', true);
                }
            }
        }
    }

    var summary = 'Found ' + matches.length + ' matches';
    if (re) summary += ' for pattern /' + opts.pattern + '/';
    summary += ' across ' + (areas ? Array.from(areas).join(', ') : 'all areas');

    return JSON.stringify({
        matches: matches,
        summary: summary,
        total: matches.length
    });
})
