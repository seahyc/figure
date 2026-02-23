"""
Page Assist hook — pre-step automation for browser challenges.

Handles generic async operations (Playwright trusted clicks/hovers queued by script.js),
step chaining, stuck recovery, change observation, and reflection memory.
Returns {"skip_llm": True} when the hook has already handled the step.
"""

import time
import asyncio

# Module-level state (persists across steps within a single run)
_step_timings: list[tuple[int, str, float]] = []
_last_challenge_step: int | None = None
_same_step_count: int = 0
_consecutive_cleanup_errors: int = 0
_prev_dom_snapshot: str = ""


def get_step_timings() -> list[tuple[int, str, float]]:
    """Expose step timings for summary generation."""
    return _step_timings


async def _handle_playwright_actions(page, _json, label=""):
    """Handle Playwright click targets + hover coords queued by script.js.

    Returns (did_click, did_hover) tuple.
    """
    did_click = False
    did_hover = False

    # Click targets
    click_targets_raw = await page.evaluate(
        "() => { var t = window.__pageAssistClickTargets || []; "
        "window.__pageAssistClickTargets = []; return JSON.stringify(t); }"
    )
    click_targets = _json.loads(click_targets_raw) if isinstance(click_targets_raw, str) and click_targets_raw.startswith('[') else []
    if click_targets:
        mouse = await page.mouse
        for ct in click_targets:
            try:
                # For Submit Code clicks, fill the input via Playwright first
                # (JS setInputValue can be overridden by React controlled input re-render)
                code_val = ct.get('code', '')
                if code_val and 'Submit Code' in ct.get('label', ''):
                    await page.evaluate("""(code) => {
                        var inp = document.getElementById('code-input') ||
                            document.querySelector('input[placeholder*="code" i], input[placeholder*="character" i]');
                        if (inp) {
                            var tracker = inp._valueTracker;
                            if (tracker) tracker.setValue('');
                            Object.getOwnPropertyDescriptor(
                                Object.getPrototypeOf(inp), 'value'
                            ).set.call(inp, code);
                            inp.dispatchEvent(new Event('input', {bubbles:true}));
                            inp.dispatchEvent(new Event('change', {bubbles:true}));
                        }
                    }""", code_val)
                    await asyncio.sleep(0.05)
                await mouse.click(ct['x'], ct['y'])
                await asyncio.sleep(0.15)
            except Exception:
                pass
        labels = [ct.get('label','')[:25] for ct in click_targets[:5] if isinstance(ct, dict)]
        print(f"  [pre_step_cleanup] {label}Playwright clicked {len(click_targets)} targets: {', '.join(labels)}")
        did_click = True

    # Hover coords
    hover_coords = await page.evaluate("() => window.__seqHoverCoords ? JSON.stringify(window.__seqHoverCoords) : null")
    if hover_coords:
        coords = _json.loads(hover_coords) if isinstance(hover_coords, str) else hover_coords
        mouse = await page.mouse
        await mouse.move(coords['x'], coords['y'])
        await asyncio.sleep(1.0)
        await page.evaluate("() => { window.__seqHoverDone = true; delete window.__seqHoverCoords; }")
        print(f"  [pre_step_cleanup] {label}Playwright hover at ({coords['x']:.0f}, {coords['y']:.0f})")
        did_hover = True

    return did_click, did_hover


async def on_step_start(agent) -> dict | None:
    """Pre-step hook: dismiss popups, run page_assist, handle async ops.

    Returns {"skip_llm": True, "reason": "..."} when the step was auto-handled
    and the LLM call should be skipped.
    """
    global _last_challenge_step, _same_step_count, _consecutive_cleanup_errors, _prev_dom_snapshot
    import json as _json
    t0 = time.time()
    step_n = agent.state.n_steps
    _ts = lambda label: print(f"  [{time.strftime('%H:%M:%S')}][Step {step_n}][{label}] {(time.time()-t0)*1000:.0f}ms")
    skip_llm = False
    try:
        page = await agent.browser_session.get_current_page()
        _ts("get_page")

        # Stuck detection: track how many agent steps on the same CHALLENGE step
        current_challenge_step = await page.evaluate(
            r"() => { var t = document.body ? document.body.textContent : ''; var m = t.match(/step\s+(\d+)\s*(?:of|\/)\s*\d+/i); return m ? parseInt(m[1]) : null; }"
        )
        if current_challenge_step is not None and current_challenge_step == _last_challenge_step:
            _same_step_count += 1
        else:
            if _last_challenge_step is not None and current_challenge_step != _last_challenge_step:
                # Step just changed — wait for challenge DOM rebuild.
                # React SPA needs time to reconcile: the step indicator updates first,
                # but challenge content (math expressions, buttons) may lag behind.
                # 500ms for the challenge's setTimeout + 1000ms for React reconciliation.
                await asyncio.sleep(1.0)
                print(f"  [pre_step_cleanup] Step changed {_last_challenge_step} -> {current_challenge_step}, waited 1000ms for DOM rebuild")
                # Clear ALL G2 guards — chaining may have exhausted retries on this step
                # before the DOM was ready. Now that we've waited for DOM rebuild, give G2 a fresh start.
                await page.evaluate("""() => {
                    window.__g2SolvedOnStep = null;
                    window.__g2StalePuzzleStep = null;
                    window.__g2IsResolving = false;
                }""")
            _same_step_count = 0
            _last_challenge_step = current_challenge_step

        # Change observation: snapshot DOM before actions
        _prev_dom_snapshot = await page.evaluate("""() => {
            var text = document.body ? document.body.innerText.substring(0, 2000) : '';
            return text.length + ':' + text.substring(0, 100);
        }""") or ""

        # Phase 1: Dismiss popups, clean DOM, run page_assist (first pass)
        first_result = await page.evaluate("""() => {
            if (!window.__skills) return 'no_skills';
            if (window.__skills.dismiss_popups) window.__skills.dismiss_popups();
            if (window.__skills.clean_dom) window.__skills.clean_dom();
            if (window.__skills.page_assist) return window.__skills.page_assist();
            return 'no_page_assist';
        }""")
        _ts("phase1_skills")
        print(f"  [pre_step_cleanup] Phase 1 result: {str(first_result)[:800]}")

        first_str = str(first_result) if first_result else ''
        phase1_found_code = "Found new codes:" in first_str or "AUTO-SUBMITTED" in first_str
        phase1_stale_only = ("IGNORE codes on screen" in first_str or "No NEW code found" in first_str) and not phase1_found_code
        phase1_step_changed = "Step changed:" in first_str

        # Playwright trusted clicks + hover: handle queued actions from script.js
        did_click, did_hover = await _handle_playwright_actions(page, _json, label="")
        if did_click:
            await asyncio.sleep(0.8)
            # Use full page_assist (not probeOnly) so auto-submit fires if code found
            probe_result = await page.evaluate("""() => {
                if (window.__skills && window.__skills.page_assist)
                    return window.__skills.page_assist();
                return 'no_page_assist';
            }""")
            probe_str = str(probe_result) if probe_result else ''
            # Only trust re-probe results if they don't show a step change
            # (step change means codes may be stale from previous step's component)
            probe_has_step_change = "Step changed:" in probe_str
            if not probe_has_step_change and ("Found new codes:" in probe_str or "AUTO-SUBMITTED" in probe_str):
                first_result = probe_result
                first_str = probe_str
                phase1_found_code = True
                print(f"  [pre_step_cleanup] Playwright re-probe found code: {probe_str[:200]}")
                # Handle any Playwright clicks queued by the full page_assist run
                await _handle_playwright_actions(page, _json, label="P1Reprobe: ")
            elif probe_has_step_change:
                print(f"  [pre_step_cleanup] Playwright re-probe: step changed, deferring to DOM rebuild: {probe_str[:200]}")

        # Generic async operations handler: execute ops queued by script.js strategies
        await _handle_async_operations(page, _json)
        _ts("phase1.5_hover")

        second_str = ''  # Initialized here — may be set by Phase 2 or chaining below

        if not phase1_found_code:
            # Phase 2: Adaptive polling for delayed content.
            drag_drop_active = "Drag-drop:" in first_str
            if phase1_step_changed:
                max_poll = 1.0
            elif phase1_stale_only and _same_step_count >= 2:
                max_poll = 0.5
            elif drag_drop_active:
                max_poll = 0.5
            elif did_hover:
                max_poll = 3.5
            else:
                max_poll = 1.0

            poll_interval = 0.5
            elapsed = 0.0
            second_result = None
            while elapsed < max_poll:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval
                is_last_poll = elapsed + poll_interval > max_poll
                probe_opt = "false" if is_last_poll else "true"
                check = await page.evaluate(f"""() => {{
                    if (window.__skills && window.__skills.page_assist)
                        return window.__skills.page_assist({{probeOnly: {probe_opt}}});
                    return 'no_page_assist';
                }}""")
                check_str = str(check) if check else ''
                if "Found new codes:" in check_str or "AUTO-SUBMITTED" in check_str:
                    second_result = check
                    break
                second_result = check
            _ts("phase2_poll")
            if second_result and str(second_result) != 'no_page_assist':
                print(f"  [pre_step_cleanup] Phase 2 ({elapsed:.1f}s): {str(second_result)[:800]}")

            # Phase 2 strategies may have queued Playwright click targets (click-to-reveal, etc.)
            # Handle them + re-probe so codes revealed by trusted clicks get picked up.
            second_str = str(second_result) if second_result else ''
            p2_did_click, p2_did_hover = await _handle_playwright_actions(page, _json, label="P2: ")
            if p2_did_click or p2_did_hover:
                await asyncio.sleep(0.5)
                p2_probe = await page.evaluate("""() => {
                    if (window.__skills && window.__skills.page_assist)
                        return window.__skills.page_assist();
                    return 'no_page_assist';
                }""")
                p2_probe_str = str(p2_probe) if p2_probe else ''
                if "Found new codes:" in p2_probe_str or "AUTO-SUBMITTED" in p2_probe_str:
                    second_result = p2_probe
                    second_str = p2_probe_str
                    print(f"  [pre_step_cleanup] P2 post-Playwright: {p2_probe_str[:300]}")

            # Step-chaining: if auto-submitted, wait for transition and retry (max 5 chains).
            chain_count = 0
            while "AUTO-SUBMITTED" in second_str and chain_count < 5:
                chain_count += 1
                await asyncio.sleep(1.0)
                chain_result = await page.evaluate("""() => {
                    if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist();
                    return 'no_page_assist';
                }""")
                chain_str = str(chain_result) if chain_result else ''
                print(f"  [pre_step_cleanup] Chain {chain_count}: {chain_str[:800]}")
                # Handle Playwright actions queued by this chain call (clicks, hovers)
                chain_did_click, chain_did_hover = await _handle_playwright_actions(page, _json, label=f"Chain{chain_count}: ")
                # If hover was executed, re-probe for revealed code
                if chain_did_hover or chain_did_click:
                    await asyncio.sleep(0.5)
                    post_action = await page.evaluate("""() => {
                        if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist();
                        return 'no_page_assist';
                    }""")
                    post_str = str(post_action) if post_action else ''
                    if "AUTO-SUBMITTED" in post_str:
                        chain_str = post_str
                        second_str = post_str
                        print(f"  [pre_step_cleanup] Chain{chain_count} post-action: {post_str[:200]}")
                        continue
                    elif "Found new codes:" in post_str:
                        chain_str = post_str
                        print(f"  [pre_step_cleanup] Chain{chain_count} post-action found code: {post_str[:200]}")
                if "AUTO-SUBMITTED" not in chain_str:
                    # Check if chain result shows stale content (step changed or code from previous step)
                    has_stale = "is stale" in chain_str or "STALE" in chain_str or "No NEW code" in chain_str
                    if has_stale:
                        # Stale content — clear async ops (may have wrong answer) and G2 guards.
                        # Let stale-retry re-compute with updated DOM.
                        await page.evaluate("""() => {
                            window.__pageAssistAsyncOps = [];
                            window.__g2SolvedOnStep = null;
                            window.__g2StalePuzzleStep = null;
                            window.__g2IsResolving = true;
                        }""")
                        print(f"  [pre_step_cleanup] Chain: stale content — cleared async ops & G2 guards")
                        for stale_try in range(2):
                            await asyncio.sleep(0.8)
                            await _handle_async_operations(page, _json)
                            retry = await page.evaluate("""() => {
                                if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist();
                                return 'no_page_assist';
                            }""")
                            retry_str = str(retry) if retry else ''
                            print(f"  [pre_step_cleanup] Chain stale-retry({stale_try+1}): {retry_str[:800]}")
                            # Handle Playwright actions from stale-retry
                            await _handle_playwright_actions(page, _json, label=f"StaleRetry{stale_try+1}: ")
                            if "AUTO-SUBMITTED" in retry_str:
                                second_str = retry_str
                                break
                        else:
                            retry_str = ''
                        if "AUTO-SUBMITTED" in retry_str:
                            continue
                    else:
                        # No stale content — process async ops normally
                        await _handle_async_operations(page, _json)
                    break
                second_str = chain_str
            if chain_count > 0:
                _ts(f"chain_{chain_count}")
                skip_llm = True
        else:
            # Phase 1 found code — still chain to handle consecutive skill-solved steps
            print(f"  [pre_step_cleanup] Skipping Phase 2 (code already found in Phase 1)")
            chain_count = 0
            chain_str = first_str
            while "AUTO-SUBMITTED" in chain_str and chain_count < 5:
                chain_count += 1
                await asyncio.sleep(1.0)
                chain_result = await page.evaluate("""() => {
                    if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist();
                    return 'no_page_assist';
                }""")
                chain_str = str(chain_result) if chain_result else ''
                print(f"  [pre_step_cleanup] Phase1-Chain {chain_count}: {chain_str[:800]}")
                # Handle Playwright actions queued by this chain call (clicks, hovers)
                p1_did_click, p1_did_hover = await _handle_playwright_actions(page, _json, label=f"P1Chain{chain_count}: ")
                if p1_did_hover or p1_did_click:
                    await asyncio.sleep(0.5)
                    post_action = await page.evaluate("""() => {
                        if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist();
                        return 'no_page_assist';
                    }""")
                    post_str = str(post_action) if post_action else ''
                    if "AUTO-SUBMITTED" in post_str:
                        chain_str = post_str
                        print(f"  [pre_step_cleanup] P1Chain{chain_count} post-action: {post_str[:200]}")
                        continue
                    elif "Found new codes:" in post_str:
                        chain_str = post_str
                        print(f"  [pre_step_cleanup] P1Chain{chain_count} post-action found code: {post_str[:200]}")
                if "AUTO-SUBMITTED" not in chain_str:
                    has_stale = "is stale" in chain_str or "STALE" in chain_str or "No NEW code" in chain_str
                    if has_stale:
                        # Stale content — clear async ops (may have wrong answer) and G2 guards
                        await page.evaluate("""() => {
                            window.__pageAssistAsyncOps = [];
                            window.__g2SolvedOnStep = null;
                            window.__g2StalePuzzleStep = null;
                            window.__g2IsResolving = true;
                        }""")
                        print(f"  [pre_step_cleanup] Phase1-Chain: stale content — cleared async ops & G2 guards")
                        for stale_try in range(2):
                            await asyncio.sleep(0.8)
                            await _handle_async_operations(page, _json)
                            retry = await page.evaluate("""() => {
                                if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist();
                                return 'no_page_assist';
                            }""")
                            retry_str = str(retry) if retry else ''
                            print(f"  [pre_step_cleanup] Phase1-Chain stale-retry({stale_try+1}): {retry_str[:800]}")
                            # Handle Playwright actions from stale-retry
                            await _handle_playwright_actions(page, _json, label=f"P1StaleRetry{stale_try+1}: ")
                            if "AUTO-SUBMITTED" in retry_str:
                                chain_str = retry_str
                                break
                        else:
                            retry_str = ''
                        if "AUTO-SUBMITTED" in retry_str:
                            continue
                    else:
                        # No step change — process async ops normally
                        await _handle_async_operations(page, _json)
                    break
            if chain_count > 0:
                _ts(f"p1_chain_{chain_count}")
                skip_llm = True

        # G2 puzzle direct solver: when G2 finds math but can't find the puzzle input,
        # solve directly via evaluate + Playwright. No polling loop — act immediately.
        final_result_str = (first_str or '') + ' | ' + (second_str or '')
        if "G2 skipped (no input" in final_result_str and "PUZZLE ACTION" in final_result_str:
            import re as _re
            answer_match = _re.search(r'Type "(\d+)"', final_result_str)
            puzzle_answer = answer_match.group(1) if answer_match else None
            print(f"  [pre_step_cleanup] G2 puzzle direct solve: answer={puzzle_answer}")

            if puzzle_answer:
                # Dismiss any blocking modal first (broader selectors for live site)
                await page.evaluate("""(step) => {
                    // Try multiple modal selectors
                    var modals = document.querySelectorAll('[class*="modal"], [class*="overlay"], [role="dialog"]');
                    modals.forEach(function(modal) {
                        var radios = modal.querySelectorAll('input[type="radio"]');
                        if (radios.length === 0) return;
                        var correctLetter = String.fromCharCode(65 + (step % 4));
                        radios.forEach(function(r) {
                            if ((r.value || '').indexOf('Correct') !== -1) {
                                r.checked = true;
                                r.dispatchEvent(new Event('change', {bubbles: true}));
                            }
                        });
                        var btn = modal.querySelector('button');
                        if (btn) { btn.disabled = false; btn.click(); }
                    });
                }""", _last_challenge_step or 1)
                await asyncio.sleep(0.5)

                # Dump all inputs for diagnostics
                input_dump = await page.evaluate("""() => {
                    var r = [];
                    document.querySelectorAll('input').forEach(function(inp) {
                        var cs = inp.offsetWidth > 0 || inp.offsetHeight > 0;
                        r.push(inp.id + '|' + inp.type + '|' + inp.placeholder + '|vis=' + cs + '|name=' + inp.name);
                    });
                    return r.join(' ; ');
                }""")
                print(f"  [pre_step_cleanup] G2 inputs: {input_dump}")

                # Dump DOM around math element for diagnostics (3 levels up, not to BODY)
                math_context = await page.evaluate("""() => {
                    var mathEl = null;
                    document.querySelectorAll('p, span, div, h1, h2, h3, h4, label').forEach(function(el) {
                        if (mathEl) return;
                        var t = el.textContent.trim();
                        if (t.length < 80 && /\\d+\\s*[+\\-x×*]\\s*\\d+\\s*=\\s*\\?/.test(t)) mathEl = el;
                    });
                    if (!mathEl) return 'no math element found';
                    // Show the math element and its 3 closest parents
                    var info = 'MATH: <' + mathEl.tagName + ' class="' + (mathEl.className||'').substring(0,40) + '">' + mathEl.textContent.trim().substring(0,60);
                    var scope = mathEl;
                    for (var i = 0; i < 3 && scope.parentElement; i++) {
                        scope = scope.parentElement;
                        var tag = scope.tagName + '#' + (scope.id||'') + '.' + (scope.className||'').substring(0,30);
                        var kids = scope.children.length;
                        var hiddenKids = 0;
                        for (var j = 0; j < scope.children.length; j++) {
                            var cs = getComputedStyle(scope.children[j]);
                            if (cs.display === 'none') hiddenKids++;
                        }
                        info += ' | P' + (i+1) + ': <' + tag + '> children=' + kids + ' hidden=' + hiddenKids;
                    }
                    // List ALL inputs and buttons in the 3-level scope
                    var allInputs = [];
                    scope.querySelectorAll('input').forEach(function(inp) {
                        var vis = inp.offsetWidth > 0 || inp.offsetHeight > 0;
                        allInputs.push(inp.tagName + '#' + inp.id + ' type=' + inp.type + ' vis=' + vis + ' ph="' + (inp.placeholder||'').substring(0,20) + '"');
                    });
                    var allBtns = [];
                    scope.querySelectorAll('button').forEach(function(b) {
                        var vis = b.offsetWidth > 0 || b.offsetHeight > 0;
                        allBtns.push('"' + b.textContent.trim().substring(0,20) + '" vis=' + vis + ' dis=' + b.disabled);
                    });
                    info += ' | INPUTS: [' + allInputs.join('; ') + ']';
                    info += ' | BTNS: [' + allBtns.join('; ') + ']';
                    return info;
                }""")
                print(f"  [pre_step_cleanup] G2 math context: {str(math_context)[:500]}")

                # Strategy 1: Reset React puzzle component to force input re-render.
                # The puzzle component was solved on the previous step. React reuses the
                # component instance, so isSolved state persists. The input is not hidden —
                # it was never rendered because the component conditionally renders based on state.
                # Walk the fiber tree to find ALL useState hooks and dump their values,
                # then reset any that look like "solved" state.
                reset_result = await page.evaluate("""() => {
                    var r = {hooks: [], dispatched: 0, error: null};
                    try {
                        var mathEl = null;
                        document.querySelectorAll('p, span, div, h1, h2, h3, h4, label').forEach(function(el) {
                            if (mathEl) return;
                            var t = el.textContent.trim();
                            if (t.length < 80 && /\\d+\\s*[+\\-x×*]\\s*\\d+\\s*=\\s*\\?/.test(t)) mathEl = el;
                        });
                        if (!mathEl) { r.error = 'no math element'; return r; }
                        // Find fiber on the math element or its parents
                        var el = mathEl;
                        var fiber = null;
                        for (var i = 0; i < 5 && el; i++) {
                            var fk = Object.keys(el).find(function(k) { return k.indexOf('__reactFiber') === 0; });
                            if (fk) { fiber = el[fk]; break; }
                            el = el.parentElement;
                        }
                        if (!fiber) { r.error = 'no fiber found'; return r; }
                        // Walk UP the fiber tree (return) to find component fibers with hooks.
                        // Puzzle component has 3 hooks: [answer(str), code(str), attempts(num)].
                        // Reset all 3 to force "unsolved" state and re-render the input.
                        var CODE_RE = /^[A-HJ-NP-Z2-9]{5,6}$/;
                        var current = fiber;
                        var visited = 0;
                        while (current && visited < 30) {
                            visited++;
                            if (current.memoizedState && current.memoizedState.queue) {
                                var hook = current.memoizedState;
                                var hookIdx = 0;
                                var hooks = [];
                                while (hook && hookIdx < 15) {
                                    if (hook.queue && hook.queue.dispatch) {
                                        var val = hook.memoizedState;
                                        var valStr = typeof val === 'object' ? JSON.stringify(val) : String(val);
                                        hooks.push({idx: hookIdx, type: typeof val, val: valStr.substring(0, 80), hook: hook});
                                    }
                                    hook = hook.next;
                                    hookIdx++;
                                }
                                // Check if this looks like a puzzle component (3 hooks: str, str, num)
                                if (hooks.length >= 3 &&
                                    hooks[0].type === 'string' && hooks[1].type === 'string' && hooks[2].type === 'number' &&
                                    CODE_RE.test(hooks[1].val)) {
                                    r.hooks = hooks.map(function(h) { return {idx: h.idx, type: h.type, val: h.val}; });
                                    // Reset: answer='', code='', attempts=0
                                    try { hooks[0].hook.queue.dispatch(''); r.dispatched++; } catch(e) {}
                                    try { hooks[1].hook.queue.dispatch(''); r.dispatched++; } catch(e) {}
                                    try { hooks[2].hook.queue.dispatch(0); r.dispatched++; } catch(e) {}
                                    break;
                                }
                            }
                            current = current.return;
                        }
                    } catch(e) { r.error = e.message; }
                    return r;
                }""")
                print(f"  [pre_step_cleanup] G2 React reset: {reset_result}")

                if reset_result and isinstance(reset_result, dict) and reset_result.get('dispatched', 0) > 0:
                    await asyncio.sleep(0.5)

                # Strategy 2: Broad solve — find math, traverse parent tree, fill input, click Solve
                solve_result = await page.evaluate("""(answer) => {
                    var r = {math: false, input: false, filled: false, clicked: false, btn: null, inputCount: 0};
                    var mathEl = null;
                    document.querySelectorAll('p, span, div, h1, h2, h3, h4, label').forEach(function(el) {
                        if (mathEl) return;
                        var t = el.textContent.trim();
                        if (t.length < 80 && /\\d+\\s*[+\\-x×*]\\s*\\d+\\s*=\\s*\\?/.test(t)) mathEl = el;
                    });
                    if (!mathEl) return r;
                    r.math = true;
                    r.inputCount = document.querySelectorAll('input').length;
                    // Search parent tree for non-code inputs
                    var scope = mathEl;
                    for (var d = 0; d < 10 && scope && scope !== document.body; d++) {
                        var inputs = scope.querySelectorAll('input');
                        for (var i = 0; i < inputs.length; i++) {
                            var inp = inputs[i];
                            if (inp.type === 'hidden' || inp.type === 'radio' || inp.type === 'checkbox') continue;
                            var ph = (inp.placeholder || '').toLowerCase();
                            if (ph.indexOf('code') !== -1 || ph.indexOf('character') !== -1) continue;
                            if (ph.indexOf('enter the 6') !== -1 || ph.indexOf('6-char') !== -1) continue;
                            var parentText = (inp.parentElement ? inp.parentElement.textContent : '').toLowerCase();
                            if (parentText.indexOf('submit code') !== -1 || parentText.indexOf('enter code') !== -1) continue;
                            r.input = true;
                            var nativeSet = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                            if (nativeSet && nativeSet.set) nativeSet.set.call(inp, String(answer));
                            else inp.value = String(answer);
                            inp.dispatchEvent(new Event('input', {bubbles: true}));
                            inp.dispatchEvent(new Event('change', {bubbles: true}));
                            var rk = Object.keys(inp).find(function(k) { return k.indexOf('__reactProps') === 0; });
                            if (rk && inp[rk] && inp[rk].onChange) {
                                try { inp[rk].onChange({target: inp}); } catch(e) {}
                            }
                            r.filled = true;
                            break;
                        }
                        if (r.filled) break;
                        scope = scope.parentElement;
                    }
                    // Find and click Solve/Submit button near math
                    scope = mathEl;
                    for (var d = 0; d < 10 && scope && scope !== document.body; d++) {
                        var btns = scope.querySelectorAll('button');
                        for (var j = 0; j < btns.length; j++) {
                            var txt = btns[j].textContent.trim().toLowerCase();
                            if (txt === 'solve' || txt === 'submit' || txt === 'check' || txt === 'verify' || txt === 'calculate') {
                                var rect = btns[j].getBoundingClientRect();
                                r.btn = {x: rect.x + rect.width/2, y: rect.y + rect.height/2, label: btns[j].textContent.trim()};
                                btns[j].click();
                                r.clicked = true;
                                break;
                            }
                        }
                        if (r.clicked) break;
                        scope = scope.parentElement;
                    }
                    return r;
                }""", puzzle_answer)
                print(f"  [pre_step_cleanup] G2 broad solve: {solve_result}")

                # Playwright trusted click on Solve button if found
                if solve_result and isinstance(solve_result, dict) and solve_result.get('btn'):
                    coords = solve_result['btn']
                    await page.mouse.click(coords['x'], coords['y'])
                    print(f"  [pre_step_cleanup] G2 Playwright clicked: {coords.get('label')} at ({coords['x']:.0f}, {coords['y']:.0f})")

                await asyncio.sleep(1.0)

                # Re-run page_assist with cleared guards
                await page.evaluate("""() => {
                    window.__g2SolvedOnStep = null;
                    window.__g2StalePuzzleStep = null;
                }""")
                g2_result = await page.evaluate("""() => {
                    if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist();
                    return 'no_page_assist';
                }""")
                g2_str = str(g2_result) if g2_result else ''
                print(f"  [pre_step_cleanup] G2 after solve: {g2_str[:300]}")
                await _handle_playwright_actions(page, _json, label="G2Solve: ")
                if "AUTO-SUBMITTED" in g2_str or "Found new codes:" in g2_str:
                    skip_llm = True

        # Connect/Register handler: some challenges need sequential button clicks with waits.
        # E.g., WebSocket: "Connect" → wait 4s → "Retrieve" / "Reveal Code"
        # E.g., Service Worker: "Register" → wait 3s → "Retrieve from Cache"
        if not skip_llm:
            _has_connect = await page.evaluate("""() => {
                // Only detect multi-step sequential patterns when buttons are numbered (1./2.)
                // or page mentions service worker / websocket / cache
                var bodyText = document.body ? document.body.innerText.substring(0, 3000).toLowerCase() : '';
                var isServiceWorkerPage = /service.worker|websocket|cache.*retrieve|register.*retrieve|connect.*reveal/i.test(bodyText);
                var btns = [];
                document.querySelectorAll('button').forEach(function(b) {
                    if (b.offsetWidth === 0) return;
                    var t = b.textContent.trim().toLowerCase();
                    var isNumbered = /^[12][.)]/.test(t.trim());
                    // Active connect/register button (not yet clicked)
                    if (!b.disabled && (isNumbered || isServiceWorkerPage) && (/connect|register|1[.)]/.test(t)))
                        btns.push({type: 'connect', text: t.substring(0, 40), done: false});
                    // Already-completed connect/register (shows checkmark or "registered/connected")
                    if (/registered|connected|✓/.test(t) && /1[.)]|register|connect/.test(t))
                        btns.push({type: 'connect', text: t.substring(0, 40), done: true});
                    // Retrieve/reveal button (include disabled — it may enable after connect click)
                    if ((isNumbered || isServiceWorkerPage) && /retrieve|2[.)]|reveal.*code/i.test(t))
                        btns.push({type: 'retrieve', text: t.substring(0, 40), disabled: b.disabled});
                });
                var hasConnect = btns.some(function(b) { return b.type === 'connect'; });
                var hasRetrieve = btns.some(function(b) { return b.type === 'retrieve'; });
                return hasConnect && hasRetrieve ? JSON.stringify(btns) : 'false';
            }""")
            if _has_connect and _has_connect != 'false':
                import json as _cjson
                _connect_btns = _cjson.loads(_has_connect) if isinstance(_has_connect, str) else _has_connect
                # Check if connect step already done
                _connect_done = any(b.get('done') for b in _connect_btns if b.get('type') == 'connect')
                print(f"  [pre_step_cleanup] Connect handler: detected multi-step buttons (connect_done={_connect_done})")
                # Click Connect/Register button first (if not already done)
                steps_to_click = ['retrieve'] if _connect_done else ['connect', 'retrieve']
                for btn_type in steps_to_click:
                    btn_pos = await page.evaluate("""(btnType) => {
                        var found = null;
                        document.querySelectorAll('button').forEach(function(b) {
                            if (b.disabled || b.offsetWidth === 0 || found) return;
                            var t = b.textContent.trim().toLowerCase();
                            if (btnType === 'connect' && /connect|register|1[.)]/.test(t)) found = b;
                            if (btnType === 'retrieve' && /retrieve|2[.)]|reveal.*code/i.test(t)) found = b;
                        });
                        if (!found) return 'null';
                        found.scrollIntoView({block: 'center'});
                        var r = found.getBoundingClientRect();
                        return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), text: found.textContent.trim().substring(0, 40)});
                    }""", btn_type)
                    if btn_pos and btn_pos != 'null':
                        try:
                            pos = _cjson.loads(btn_pos) if isinstance(btn_pos, str) else btn_pos
                            _mouse = await page.mouse
                            await _mouse.click(pos['x'], pos['y'])
                            wait_time = 4.0 if btn_type == 'connect' else 2.0
                            print(f"  [pre_step_cleanup] Connect handler: clicked {btn_type} '{pos.get('text','')}', waiting {wait_time}s")
                            await asyncio.sleep(wait_time)
                        except Exception as _ce:
                            print(f"  [pre_step_cleanup] Connect handler: error {_ce}")
                # After clicks, run page_assist to find codes
                connect_result = await page.evaluate("() => window.__skills && window.__skills.page_assist ? window.__skills.page_assist() : 'no'")
                connect_str = str(connect_result) if connect_result else ''
                await _handle_playwright_actions(page, _cjson, label="Connect: ")
                print(f"  [pre_step_cleanup] Connect handler result: {connect_str[:200]}")
                if "AUTO-SUBMITTED" in connect_str or "Found new codes:" in connect_str:
                    skip_llm = True
                else:
                    # Try onComplete fiber bypass from Retrieve/Reveal button
                    # Step 1: Call onComplete (may return code or trigger state update)
                    _oncomplete_code = await page.evaluate("""() => {
                        var CODE_RE = /[A-HJ-NP-Z2-9]{6}/;
                        var btn = null;
                        document.querySelectorAll('button').forEach(function(b) {
                            if (b.offsetWidth === 0) return;
                            var t = b.textContent.trim().toLowerCase();
                            if (/retrieve|reveal.*code|2[.)]/.test(t) && !btn) btn = b;
                        });
                        if (!btn) return 'no_btn';
                        var el = btn;
                        var fiber = null;
                        for (var i = 0; i < 10 && el && !fiber; i++) {
                            var fk = Object.keys(el).find(function(k) { return k.indexOf('__reactFiber') === 0; });
                            if (fk) fiber = el[fk];
                            else el = el.parentElement;
                        }
                        if (!fiber) return 'no_fiber';
                        var f = fiber;
                        while (f) {
                            var p = f.memoizedProps;
                            if (p && typeof p === 'object' && typeof p.onComplete === 'function') {
                                var proof = { type: 'service_worker', timestamp: Date.now(),
                                    data: { method: 'cache_retrieve', registered: true, cached: true } };
                                var result = p.onComplete(proof);
                                if (typeof result === 'string' && CODE_RE.test(result)) {
                                    window.__iframeCapturedCodes = window.__iframeCapturedCodes || [];
                                    if (window.__iframeCapturedCodes.indexOf(result) === -1)
                                        window.__iframeCapturedCodes.push(result);
                                    return 'code:' + result;
                                }
                                return 'called_onComplete:' + String(result).substring(0, 80);
                            }
                            f = f.return;
                        }
                        return 'no_onComplete';
                    }""")
                    print(f"  [pre_step_cleanup] Connect handler fiber bypass: {_oncomplete_code}")
                    # Step 2: Wait for React to re-render, then scan for codes
                    await asyncio.sleep(0.8)
                    connect_result2 = await page.evaluate("() => window.__skills && window.__skills.page_assist ? window.__skills.page_assist() : 'no'")
                    connect_str2 = str(connect_result2) if connect_result2 else ''
                    await _handle_playwright_actions(page, _cjson, label="ConnectFiber: ")
                    print(f"  [pre_step_cleanup] Connect fiber result: {connect_str2[:200]}")
                    if "AUTO-SUBMITTED" in connect_str2 or "Found new codes:" in connect_str2:
                        skip_llm = True

        # Level completion: if iframe/level challenge detected, iterate through all levels
        # using Playwright trusted clicks (React needs isTrusted events for level buttons).
        # Detect level challenge: Level nav, Iframe active, depth pattern, or Extract Code button
        _has_level_challenge = ("Iframe active" in final_result_str or "Level nav:" in final_result_str
                                or "Deepest level" in final_result_str)
        if not _has_level_challenge and not skip_llm:
            # Also check Phase 1 output (which may have had "Level nav:" before Playwright consumed buttons)
            _has_level_challenge = "Level nav:" in first_str or "Iframe active" in first_str
        if not skip_llm and _has_level_challenge:
            print(f"  [pre_step_cleanup] Level completion: detected level challenge")
            # Strategy: click ONE Enter Level button at a time using Playwright trusted click,
            # wait for React to process, then repeat. When only Extract Code remains, click it.
            # G8 in script.js ONLY reports level buttons — does NOT click them.
            # This ensures React state is properly advanced by trusted Playwright clicks.
            extract_click_count = 0
            for level_iter in range(15):  # Max 15 iterations (enough for depth 0→10)
                btn_info = await page.evaluate("""() => {
                    var r = {type: null, x: 0, y: 0, label: '', depth: ''};
                    var dm = document.body.innerText.match(/depth[: ]*([0-9]+) *[/] *([0-9]+)/i);
                    r.depth = dm ? dm[1] + '/' + dm[2] : 'unknown';
                    var btns = [];
                    document.querySelectorAll('button').forEach(function(b) {
                        if (b.disabled || b.offsetWidth === 0) return;
                        var t = b.textContent.trim();
                        if (/enter.*level/i.test(t)) btns.push({el: b, label: t, prio: 1});
                        else if (/extract.*code/i.test(t)) btns.push({el: b, label: t, prio: 2});
                    });
                    btns.sort(function(a,b) { return a.prio - b.prio; });
                    if (btns.length === 0) return r;
                    var btn = btns[0].el;
                    btn.scrollIntoView({block: 'center'});
                    var rect = btn.getBoundingClientRect();
                    r.type = btns[0].prio === 1 ? 'level' : 'extract';
                    r.x = Math.round(rect.x + rect.width / 2);
                    r.y = Math.round(rect.y + rect.height / 2);
                    r.label = btns[0].label.substring(0, 30);
                    return r;
                }""")
                if isinstance(btn_info, str):
                    try:
                        import json as _bjson
                        btn_info = _bjson.loads(btn_info)
                    except Exception:
                        btn_info = {}
                if not isinstance(btn_info, dict) or not btn_info.get('type'):
                    print(f"  [pre_step_cleanup] Level iter {level_iter}: no buttons found, depth={btn_info}")
                    break
                is_extract = btn_info.get('type') == 'extract'
                if is_extract:
                    extract_click_count += 1
                print(f"  [pre_step_cleanup] Level iter {level_iter}: {btn_info.get('type')} '{btn_info.get('label')}' at ({btn_info.get('x')},{btn_info.get('y')}) depth={btn_info.get('depth')} extract_clicks={extract_click_count}")
                # Playwright trusted click
                try:
                    await asyncio.sleep(0.2)
                    _mouse = await page.mouse
                    await _mouse.click(btn_info['x'], btn_info['y'])
                    await asyncio.sleep(1.0 if is_extract else 0.8)
                except Exception as _clickErr:
                    print(f"  [pre_step_cleanup] Level iter {level_iter}: click error: {_clickErr}")
                # Re-run page_assist probeOnly to check for codes
                level_result = await page.evaluate("""() => {
                    if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist({probeOnly: true});
                    return 'no_page_assist';
                }""")
                level_str = str(level_result) if level_result else ''
                print(f"  [pre_step_cleanup] Level iter {level_iter} result: {level_str[:200]}")
                if "AUTO-SUBMITTED" in level_str or "Found new codes:" in level_str:
                    skip_llm = True
                    print(f"  [pre_step_cleanup] Level completion: code found!")
                    break
                # If Extract Code clicked 2+ times without result, try onComplete fiber bypass
                if is_extract and extract_click_count >= 2:
                    print(f"  [pre_step_cleanup] Extract Code clicked {extract_click_count}x without result, trying onComplete bypass")
                    try:
                      _depth_parts = btn_info.get('depth', '0/0').split('/')
                      _num_levels = int(_depth_parts[1]) if len(_depth_parts) == 2 and _depth_parts[1].isdigit() else 5
                      # Walk UP from Extract Code button, find onComplete prop, call it directly
                      oncomplete_result = await page.evaluate("""(numLevels) => {
                          var r = {called: false, error: null, code: null};
                          try {
                              var btn = null;
                              document.querySelectorAll('button').forEach(function(b) {
                                  if (/extract.*code|reveal.*code/i.test(b.textContent.trim()) && b.offsetWidth > 0) btn = b;
                              });
                              if (!btn) { r.error = 'no Extract Code button'; return JSON.stringify(r); }
                              // Find fiber on button or ancestors
                              var el = btn;
                              var fiber = null;
                              for (var i = 0; i < 10 && el && !fiber; i++) {
                                  var fk = Object.keys(el).find(function(k) { return k.indexOf('__reactFiber') === 0; });
                                  if (fk) fiber = el[fk];
                                  else el = el.parentElement;
                              }
                              if (!fiber) { r.error = 'no fiber'; return JSON.stringify(r); }
                              // Walk UP to find onComplete prop
                              var f = fiber;
                              while (f) {
                                  var p = f.memoizedProps;
                                  if (p && typeof p === 'object' && typeof p.onComplete === 'function') {
                                      var proof = {
                                          type: 'recursive_iframe',
                                          timestamp: Date.now(),
                                          data: { method: 'recursive_iframe', numLevels: numLevels,
                                                  currentLevel: numLevels, stepNum: 0 }
                                      };
                                      var result = p.onComplete(proof);
                                      r.called = true;
                                      r.code = typeof result === 'string' ? result : null;
                                      break;
                                  }
                                  f = f.return;
                              }
                              if (!r.called) r.error = 'no onComplete found';
                              // Check for codes that may have appeared
                              var CODE_RE = /[A-HJ-NP-Z2-9]{6}/;
                              var bodyText = document.body ? document.body.innerText : '';
                              var m = bodyText.match(new RegExp('(?:code|revealed|extracted)[^A-Z0-9]*(' + CODE_RE.source + ')', 'i'));
                              if (m) r.code = m[1];
                          } catch(e) { r.error = e.message; }
                          return JSON.stringify(r);
                      }""", _num_levels)
                      if isinstance(oncomplete_result, str):
                          try:
                              import json as _ocjson
                              oncomplete_result = _ocjson.loads(oncomplete_result)
                          except Exception:
                              oncomplete_result = {'error': str(oncomplete_result)[:100]}
                      print(f"  [pre_step_cleanup] onComplete bypass: {oncomplete_result}")
                      if oncomplete_result.get('code'):
                          new_code = oncomplete_result['code']
                          await page.evaluate(f"""() => {{
                              window.__iframeCapturedCodes = window.__iframeCapturedCodes || [];
                              if (window.__iframeCapturedCodes.indexOf('{new_code}') === -1)
                                  window.__iframeCapturedCodes.push('{new_code}');
                          }}""")
                      # Re-run page_assist to pick up any new codes
                      await asyncio.sleep(1.0)
                      oc_result = await page.evaluate("() => window.__skills && window.__skills.page_assist ? window.__skills.page_assist() : 'no'")
                      oc_str = str(oc_result) if oc_result else ''
                      await _handle_playwright_actions(page, _json, label="LevelOC: ")
                      print(f"  [pre_step_cleanup] onComplete post: {oc_str[:200]}")
                      if "AUTO-SUBMITTED" in oc_str or "Found new codes:" in oc_str:
                          skip_llm = True
                          break
                    except Exception as _ocErr:
                      print(f"  [pre_step_cleanup] onComplete error: {_ocErr}")
                    break  # onComplete attempted, let LLM handle rest

        # Change observation: detect if page actually changed after actions
        if not skip_llm and _same_step_count >= 1:
            curr_dom_snapshot = await page.evaluate("""() => {
                var text = document.body ? document.body.innerText.substring(0, 2000) : '';
                return text.length + ':' + text.substring(0, 100);
            }""") or ""
            if curr_dom_snapshot == _prev_dom_snapshot and _prev_dom_snapshot:
                await page.evaluate("""() => {
                    var div = document.getElementById('__page_assist_results');
                    if (div) div.textContent += ' | CHANGE_OBSERVATION: No DOM change detected after actions. Try a DIFFERENT interaction strategy.';
                }""")
                print(f"  [pre_step_cleanup] Change observation: no DOM change detected")

        # Reflection: inject failure reflections when stuck
        if _same_step_count >= 2:
            await page.evaluate(f"""(count) => {{
                window.__reflections = window.__reflections || [];
                if (window.__reflections.length < 5) {{
                    window.__reflections.push('Stuck for ' + count + ' steps. Previous actions did not advance. Try completely different approach.');
                }}
            }}""", _same_step_count)

        # Stuck recovery
        if _same_step_count >= 3:
            if _same_step_count % 2 == 1:
                await page.evaluate("""() => {
                    window.scrollTo(0, document.body.scrollHeight);
                    // Also scroll overflow containers
                    document.querySelectorAll('div').forEach(function(el) {
                        if (el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 30) {
                            el.scrollTop = el.scrollHeight;
                            el.dispatchEvent(new Event('scroll', { bubbles: true }));
                        }
                    });
                }""")
                print(f"  [pre_step_cleanup] Stuck recovery: scrolled to BOTTOM (stuck {_same_step_count} steps on CS {_last_challenge_step})")
            else:
                await page.evaluate("""() => {
                    window.scrollTo(0, 0);
                    document.querySelectorAll('div').forEach(function(el) {
                        if (el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 30) {
                            el.scrollTop = 0;
                            el.dispatchEvent(new Event('scroll', { bubbles: true }));
                        }
                    });
                }""")
                print(f"  [pre_step_cleanup] Stuck recovery: scrolled to TOP (stuck {_same_step_count} steps on CS {_last_challenge_step})")

        if _same_step_count >= 5:
            force_result = await page.evaluate("""() => {
                var revealed = 0;
                var allEls = document.querySelectorAll('*');
                for (var i = 0; i < allEls.length; i++) {
                    var el = allEls[i];
                    if (el.offsetWidth > 0 && el.offsetHeight > 0) continue;
                    if (el.id && el.id.indexOf('__') === 0) continue;
                    var text = el.textContent ? el.textContent.trim() : '';
                    if (text.length >= 6 && text.length <= 30 && /[A-HJ-NP-Z2-9]{6}/.test(text) && /[A-HJ-NP-Z]/.test(text)) {
                        el.style.setProperty('display', 'block', 'important');
                        el.style.setProperty('visibility', 'visible', 'important');
                        el.style.setProperty('opacity', '1', 'important');
                        var p = el.parentElement;
                        while (p && p !== document.body) {
                            var cs = getComputedStyle(p);
                            if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') {
                                p.style.setProperty('display', 'block', 'important');
                                p.style.setProperty('visibility', 'visible', 'important');
                                p.style.setProperty('opacity', '1', 'important');
                            }
                            p = p.parentElement;
                        }
                        revealed++;
                    }
                }
                document.querySelectorAll('button').forEach(function(btn) {
                    var t = btn.textContent.trim();
                    if (btn.disabled || btn.offsetWidth === 0) return;
                    if (/reveal|complete|show.*code|code.*reveal/i.test(t) && !/trigger|submit/i.test(t)) {
                        btn.click();
                    }
                });
                if (window.__skills && window.__skills.page_assist) {
                    return 'force-reveal:' + revealed + ' | ' + window.__skills.page_assist();
                }
                return 'force-reveal:' + revealed;
            }""")
            force_str = str(force_result) if force_result else ''
            if "force-reveal:" in force_str and ("Found new codes:" in force_str or "AUTO-SUBMITTED" in force_str):
                print(f"  [pre_step_cleanup] Force-reveal found code: {force_str[:800]}")
            elif "force-reveal:" in force_str and "0" not in force_str[:20]:
                print(f"  [pre_step_cleanup] Force-revealed {force_str[:200]}")

        if _same_step_count >= 8:
            await page.evaluate("""() => {
                window.__lastAutoSubmitStep = null;
                window.__autoSubmitGuardCount = 0;
                if (window.__pageAssistSubmittedCodes && window.__pageAssistSubmittedCodes.length > 0) {
                    var step = null;
                    var m = (document.body ? document.body.textContent : '').match(/step\\s+(\\d+)/i);
                    if (m) step = parseInt(m[1]);
                    if (step !== null) {
                        window.__pageAssistSubmittedCodes = window.__pageAssistSubmittedCodes.filter(
                            function(e) { return e.step !== step; }
                        );
                    }
                }
            }""")
            print(f"  [pre_step_cleanup] Stuck recovery: cleared auto-submit guard + current step codes (stuck {_same_step_count} steps on CS {_last_challenge_step})")

        # Phase 3: DOM cleanup and metadata injection
        await page.evaluate("""() => {
            window.scrollTo(0, 0);

            var tsEl = document.getElementById('__agent-timestamp');
            if (!tsEl) {
                tsEl = document.createElement('div');
                tsEl.id = '__agent-timestamp';
                tsEl.style.cssText = 'font-size:11px;color:#888;';
                if (document.body.firstChild) document.body.insertBefore(tsEl, document.body.firstChild);
            }
            var now = new Date();
            tsEl.textContent = 'Agent time: ' + now.toISOString() + ' (epoch: ' + Date.now() + ')';

            var stuckCount = """ + str(_same_step_count) + """;
            var stuckEl = document.getElementById('__stuck-warning');
            if (stuckCount >= 3) {
                if (!stuckEl) {
                    stuckEl = document.createElement('div');
                    stuckEl.id = '__stuck-warning';
                    stuckEl.style.cssText = 'font-size:14px;color:red;font-weight:bold;padding:8px;border:2px solid red;margin:8px 0;background:#fff0f0;';
                    if (document.body.firstChild) document.body.insertBefore(stuckEl, document.body.firstChild);
                }
                stuckEl.textContent = 'WARNING: You have been stuck on this challenge step for ' + stuckCount + ' agent steps. STOP scrolling and clicking random buttons. Try: (1) Use evaluate to run window.__skills.page_assist() and read the result, (2) Look at the page_assist results div for any found codes, (3) If a code is shown, type it in the code input and click the Submit Code button, (4) DO NOT click any button except Submit Code — all other buttons are decoys.';
            } else if (stuckEl) {
                stuckEl.remove();
            }

            if (window.__skills && window.__skills.observe_changes) {
                var result = window.__skills.observe_changes();
                var existing = document.getElementById('__dom-changes-summary');
                if (existing) existing.remove();
                if (result && result.indexOf('changes') !== -1 && result.indexOf('No DOM') === -1) {
                    var el = document.createElement('div');
                    el.id = '__dom-changes-summary';
                    el.style.cssText = 'font-size:11px;color:#888;padding:4px;border:1px solid #ddd;margin:4px 0;';
                    el.textContent = result;
                    if (tsEl.nextSibling) {
                        document.body.insertBefore(el, tsEl.nextSibling);
                    }
                }
                if (window.__domChangeLog) window.__domChangeLog = [];
            }
        }""")
        _ts("phase3_inject")
        _consecutive_cleanup_errors = 0
    except Exception as e:
        _consecutive_cleanup_errors += 1
        is_crash = "close frame" in str(e) or "ConnectionClosed" in str(e) or "Target closed" in str(e)
        print(f"[pre_step_cleanup] ERROR ({_consecutive_cleanup_errors} consecutive): {e}")
        if _consecutive_cleanup_errors >= 5 and is_crash:
            print(f"[pre_step_cleanup] BROWSER CRASH DETECTED after {_consecutive_cleanup_errors} consecutive errors. Terminating agent.")
            try:
                page = await agent.browser_session.get_current_page()
                await page.evaluate("""() => {
                    document.body.innerHTML = '<h1>BROWSER CRASHED</h1><p>The browser connection died after multiple consecutive errors. The agent should call done() now.</p>';
                }""")
            except Exception:
                pass
    cleanup_ms = (time.time() - t0) * 1000
    _step_timings.append((step_n, 'cleanup', cleanup_ms))
    print(f"[{time.strftime('%H:%M:%S')}][Step {step_n}] pre_step_cleanup: {cleanup_ms:.0f}ms")

    if skip_llm:
        return {"skip_llm": True, "reason": "auto-handled by page_assist"}
    return None


# ---------------------------------------------------------------------------
# Generic async operations handler
# ---------------------------------------------------------------------------

async def _handle_async_operations(page, _json):
    """Execute async ops queued by script.js that need Playwright trusted events.

    script.js strategies queue operations in window.__pageAssistAsyncOps when
    they need real mouse events (e.g., level navigation, shadow DOM clicks).
    """
    ops_raw = await page.evaluate(
        "() => { var o = window.__pageAssistAsyncOps || []; "
        "window.__pageAssistAsyncOps = []; return JSON.stringify(o); }"
    )
    ops = _json.loads(ops_raw) if ops_raw and isinstance(ops_raw, str) and ops_raw.startswith('[') else []
    if not ops:
        return False

    mouse = await page.mouse
    for op in ops:
        try:
            if op.get('type') == 'click':
                await mouse.click(op['x'], op['y'])
                delay = op.get('delay', 0.3)
                await asyncio.sleep(delay)
                print(f"  [async_ops] Clicked ({op['x']}, {op['y']}): {op.get('label', '')[:30]}")
            elif op.get('type') == 'hover':
                await mouse.move(op['x'], op['y'])
                delay = op.get('delay', 1.0)
                await asyncio.sleep(delay)
                print(f"  [async_ops] Hovered ({op['x']}, {op['y']}): {op.get('label', '')[:30]}")
            elif op.get('type') == 'type_and_click':
                # Trusted mouse click on input + JS keyboard events + trusted click on button.
                # Used when JS setInputValue doesn't trigger React state updates properly.
                # The mouse click focuses the input (trusted event), then JS keyboard events
                # simulate typing character by character (triggers React synthetic events),
                # then mouse click on button fires the handler.
                inp = op.get('input', {})
                btn = op.get('button', {})
                val = str(op.get('value', ''))
                if inp.get('x') and inp.get('y') and btn.get('x') and btn.get('y'):
                    # Click to focus input
                    await mouse.click(inp['x'], inp['y'])
                    await asyncio.sleep(0.1)
                    # Type via JS keyboard events (React listens to these)
                    await page.evaluate("""(val) => {
                        var active = document.activeElement;
                        if (!active || active === document.body) return;
                        // Clear existing value
                        var nset = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;
                        var tracker = active._valueTracker;
                        if (tracker) tracker.setValue('');
                        nset.call(active, '');
                        active.dispatchEvent(new Event('input', {bubbles: true}));
                        // Type each character
                        for (var i = 0; i < val.length; i++) {
                            var ch = val[i];
                            active.dispatchEvent(new KeyboardEvent('keydown', {key: ch, bubbles: true}));
                            if (tracker) tracker.setValue(active.value);
                            nset.call(active, active.value + ch);
                            active.dispatchEvent(new Event('input', {bubbles: true}));
                            active.dispatchEvent(new KeyboardEvent('keyup', {key: ch, bubbles: true}));
                        }
                        active.dispatchEvent(new Event('change', {bubbles: true}));
                    }""", val)
                    await asyncio.sleep(0.2)
                    # Click solve button
                    await mouse.click(btn['x'], btn['y'])
                    await asyncio.sleep(0.5)
                    print(f"  [async_ops] Typed '{val}' + clicked: {op.get('label', '')[:40]}")
        except Exception as e:
            print(f"  [async_ops] Error: {e}")

    # After all ops, re-probe for codes and auto-submit if found
    if len(ops) > 0:
        await asyncio.sleep(0.5)
        # Use full page_assist (not probeOnly) so auto-submit fires if code found
        probe = await page.evaluate("""() => {
            if (window.__skills && window.__skills.page_assist)
                return window.__skills.page_assist();
            return 'no_page_assist';
        }""")
        probe_str = str(probe) if probe else ''
        if "Found new codes:" in probe_str or "AUTO-SUBMITTED" in probe_str:
            print(f"  [async_ops] Post-ops probe found code: {probe_str[:200]}")
            # Handle any Playwright clicks queued by the full page_assist run
            await _handle_playwright_actions(page, _json, label="AsyncPost: ")

    return len(ops) > 0
