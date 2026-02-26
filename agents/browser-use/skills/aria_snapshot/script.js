(function(options) {
    var opts = Object.assign({
        maxDepth: 10,
        maxElements: 500,
        includeHidden: false,
        focusSelector: null
    }, options || {});

    var refCounter = 0;
    var refs = {};
    var elementCount = 0;
    var stats = { elements: 0, roles: 0, inputs: 0, buttons: 0 };

    // Roles that are semantically meaningful
    var SEMANTIC_ROLES = new Set([
        'alert', 'alertdialog', 'application', 'article', 'banner',
        'button', 'cell', 'checkbox', 'columnheader', 'combobox',
        'complementary', 'contentinfo', 'definition', 'dialog',
        'directory', 'document', 'feed', 'figure', 'form', 'grid',
        'gridcell', 'group', 'heading', 'img', 'link', 'list',
        'listbox', 'listitem', 'log', 'main', 'marquee', 'math',
        'menu', 'menubar', 'menuitem', 'menuitemcheckbox',
        'menuitemradio', 'navigation', 'none', 'note', 'option',
        'presentation', 'progressbar', 'radio', 'radiogroup',
        'region', 'row', 'rowgroup', 'rowheader', 'scrollbar',
        'search', 'searchbox', 'separator', 'slider', 'spinbutton',
        'status', 'switch', 'tab', 'table', 'tablist', 'tabpanel',
        'term', 'textbox', 'timer', 'toolbar', 'tooltip', 'tree',
        'treegrid', 'treeitem'
    ]);

    // Tag to implicit ARIA role mapping
    var TAG_ROLES = {
        'A': 'link', 'BUTTON': 'button', 'INPUT': 'textbox',
        'SELECT': 'combobox', 'TEXTAREA': 'textbox', 'IMG': 'img',
        'NAV': 'navigation', 'MAIN': 'main', 'HEADER': 'banner',
        'FOOTER': 'contentinfo', 'ASIDE': 'complementary',
        'FORM': 'form', 'TABLE': 'table', 'TR': 'row',
        'TH': 'columnheader', 'TD': 'cell', 'UL': 'list',
        'OL': 'list', 'LI': 'listitem', 'H1': 'heading',
        'H2': 'heading', 'H3': 'heading', 'H4': 'heading',
        'H5': 'heading', 'H6': 'heading', 'DIALOG': 'dialog',
        'DETAILS': 'group', 'SUMMARY': 'button',
        'PROGRESS': 'progressbar', 'METER': 'meter',
        'CANVAS': 'img', 'VIDEO': 'video', 'AUDIO': 'audio',
        'SECTION': 'region', 'ARTICLE': 'article',
        'FIGURE': 'figure', 'FIGCAPTION': 'caption',
        'SEARCH': 'search', 'MENU': 'menu'
    };

    // Input type to role mapping
    var INPUT_ROLES = {
        'checkbox': 'checkbox', 'radio': 'radio', 'range': 'slider',
        'number': 'spinbutton', 'search': 'searchbox',
        'submit': 'button', 'reset': 'button', 'button': 'button'
    };

    function isVisible(el) {
        if (opts.includeHidden) return true;
        if (el.offsetParent === null && el.tagName !== 'BODY' && el.tagName !== 'HTML') {
            var style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            if (style.position !== 'fixed' && style.position !== 'sticky') return false;
        }
        if (el.hasAttribute('aria-hidden') && el.getAttribute('aria-hidden') === 'true') return false;
        if (el.hasAttribute('data-filtered')) return false;
        return true;
    }

    function getRole(el) {
        // Explicit role takes priority
        var role = el.getAttribute('role');
        if (role && SEMANTIC_ROLES.has(role)) return role;

        // Input type mapping
        if (el.tagName === 'INPUT') {
            var type = (el.getAttribute('type') || 'text').toLowerCase();
            return INPUT_ROLES[type] || 'textbox';
        }

        // Tag to role
        return TAG_ROLES[el.tagName] || null;
    }

    function getAccessibleName(el) {
        // aria-label
        var label = el.getAttribute('aria-label');
        if (label) return label.trim();

        // aria-labelledby
        var labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy) {
            var parts = labelledBy.split(/\s+/).map(function(id) {
                var ref = document.getElementById(id);
                return ref ? ref.textContent.trim() : '';
            }).filter(Boolean);
            if (parts.length) return parts.join(' ');
        }

        // placeholder for inputs
        if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
            var ph = el.getAttribute('placeholder');
            if (ph) return ph.trim();
            // Associated label
            if (el.id) {
                var assocLabel = document.querySelector('label[for="' + el.id + '"]');
                if (assocLabel) return assocLabel.textContent.trim();
            }
        }

        // alt for images
        if (el.tagName === 'IMG') {
            return (el.getAttribute('alt') || '').trim();
        }

        // title attribute
        var title = el.getAttribute('title');
        if (title) return title.trim();

        // Direct text content (only first 100 chars, only for leaf-ish elements)
        var childElements = el.querySelectorAll('*');
        if (childElements.length < 5) {
            var text = el.textContent.trim().substring(0, 100);
            if (text) return text;
        }

        return '';
    }

    function getState(el) {
        var states = [];
        if (el.disabled || el.getAttribute('aria-disabled') === 'true') states.push('disabled');
        if (el.required || el.getAttribute('aria-required') === 'true') states.push('required');
        if (el.getAttribute('aria-expanded') === 'true') states.push('expanded');
        if (el.getAttribute('aria-expanded') === 'false') states.push('collapsed');
        if (el.getAttribute('aria-selected') === 'true') states.push('selected');
        if (el.getAttribute('aria-checked') === 'true') states.push('checked');
        if (el.getAttribute('aria-checked') === 'false') states.push('unchecked');
        if (el.getAttribute('aria-pressed') === 'true') states.push('pressed');
        if (el.readOnly) states.push('readonly');
        // Heading level
        var match = el.tagName.match(/^H(\d)$/);
        if (match) states.push('level=' + match[1]);
        // Value for inputs
        if ((el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') && el.value) {
            states.push('value="' + el.value.substring(0, 50) + '"');
        }
        if (el.tagName === 'SELECT' && el.selectedIndex >= 0) {
            var opt = el.options[el.selectedIndex];
            if (opt) states.push('value="' + opt.text.substring(0, 50) + '"');
        }
        // Progress
        if (el.tagName === 'PROGRESS' && el.value !== undefined) {
            states.push('value=' + el.value + '/' + (el.max || 100));
        }
        return states;
    }

    function getRect(el) {
        try {
            var r = el.getBoundingClientRect();
            return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
        } catch(e) {
            return null;
        }
    }

    function shouldInclude(el, role) {
        // Always include elements with explicit roles
        if (el.getAttribute('role')) return true;
        // Always include interactive elements
        if (role === 'button' || role === 'link' || role === 'textbox' ||
            role === 'checkbox' || role === 'radio' || role === 'combobox' ||
            role === 'slider' || role === 'searchbox' || role === 'spinbutton') return true;
        // Include semantic containers
        if (role === 'navigation' || role === 'main' || role === 'banner' ||
            role === 'contentinfo' || role === 'form' || role === 'region' ||
            role === 'complementary' || role === 'search' || role === 'dialog') return true;
        // Include headings
        if (role === 'heading') return true;
        // Include images with alt text
        if (role === 'img' && el.getAttribute('alt')) return true;
        // Include list structures
        if (role === 'list' || role === 'listitem') return true;
        // Include tables
        if (role === 'table' || role === 'row' || role === 'cell' || role === 'columnheader') return true;
        // Include elements with accessible names
        if (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')) return true;
        // Include canvas
        if (el.tagName === 'CANVAS') return true;
        // Include draggable elements
        if (el.draggable || el.getAttribute('draggable') === 'true') return true;
        return false;
    }

    function walk(el, depth, lines) {
        if (elementCount >= opts.maxElements) return;
        if (depth > opts.maxDepth) return;
        if (!el || el.nodeType !== 1) return;  // Element nodes only
        if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE' || el.tagName === 'NOSCRIPT') return;
        if (!isVisible(el)) return;

        var role = getRole(el);
        var include = role && shouldInclude(el, role);

        if (include) {
            elementCount++;
            refCounter++;
            var refId = 'ref_' + refCounter;
            var name = getAccessibleName(el);
            var states = getState(el);
            var rect = getRect(el);

            // Build line
            var indent = '';
            for (var i = 0; i < depth; i++) indent += '  ';
            var line = indent + '[' + refId + '] ' + role;
            if (name) line += ' "' + name.replace(/"/g, '\\"').replace(/\n/g, ' ') + '"';
            if (states.length) line += ' {' + states.join(', ') + '}';
            lines.push(line);

            // Store ref
            refs[refId] = {
                tag: el.tagName.toLowerCase(),
                role: role,
                text: name.substring(0, 200),
                rect: rect
            };

            // Track stats
            stats.elements++;
            if (role) stats.roles++;
            if (role === 'textbox' || role === 'searchbox' || role === 'spinbutton' ||
                role === 'combobox' || role === 'checkbox' || role === 'radio') stats.inputs++;
            if (role === 'button') stats.buttons++;

            // Mark element with ref for targeting
            el.setAttribute('data-aria-ref', refId);
        }

        // Recurse into children (even if this element wasn't included)
        var children = el.children;
        for (var c = 0; c < children.length; c++) {
            walk(children[c], include ? depth + 1 : depth, lines);
        }

        // Check shadow DOM
        if (el.shadowRoot) {
            var shadowChildren = el.shadowRoot.children;
            for (var s = 0; s < shadowChildren.length; s++) {
                walk(shadowChildren[s], include ? depth + 1 : depth, lines);
            }
        }
    }

    // Main execution
    var root = document.body;
    if (opts.focusSelector) {
        var focused = document.querySelector(opts.focusSelector);
        if (focused) root = focused;
    }

    var lines = [];
    walk(root, 0, lines);

    var result = {
        tree: lines.join('\n'),
        refs: refs,
        stats: stats,
        url: window.location.href,
        title: document.title
    };

    return JSON.stringify(result);
})
