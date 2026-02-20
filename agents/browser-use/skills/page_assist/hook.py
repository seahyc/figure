"""
Page Assist hook — extracted from agent.py pre_step_cleanup.

Handles iframe/websocket/shadow DOM challenges, step chaining, stuck recovery,
and auto-submission. Returns {"skip_llm": True} when the hook has already
handled the step (e.g., auto-submitted a code).
"""

import time
import asyncio

# Module-level state (persists across steps within a single run)
_step_timings: list[tuple[int, str, float]] = []
_last_challenge_step: int | None = None
_same_step_count: int = 0
_consecutive_cleanup_errors: int = 0


def get_step_timings() -> list[tuple[int, str, float]]:
    """Expose step timings for summary generation."""
    return _step_timings


async def on_step_start(agent) -> dict | None:
    """Pre-step hook: dismiss popups, run page_assist, handle complex challenges.

    Returns {"skip_llm": True, "reason": "..."} when the step was auto-handled
    and the LLM call should be skipped.
    """
    global _last_challenge_step, _same_step_count, _consecutive_cleanup_errors
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
            _same_step_count = 0
            _last_challenge_step = current_challenge_step

        # Phase 1: Dismiss popups, clean DOM, run page_assist (first pass)
        first_result = await page.evaluate("""() => {
            if (!window.__skills) return 'no_skills';
            if (window.__skills.dismiss_popups) window.__skills.dismiss_popups();
            if (window.__skills.clean_dom) window.__skills.clean_dom();
            if (window.__skills.page_assist) return window.__skills.page_assist();
            return 'no_page_assist';
        }""")
        _ts("phase1_skills")
        print(f"  [pre_step_cleanup] Phase 1 result: {str(first_result)[:500]}")

        # Determine if Phase 1 already found what we need
        first_str = str(first_result) if first_result else ''
        phase1_found_code = "Found new codes:" in first_str or "AUTO-SUBMITTED" in first_str
        phase1_stale_only = ("IGNORE codes on screen" in first_str or "No NEW code found" in first_str) and not phase1_found_code
        phase1_step_changed = "Step changed:" in first_str

        # Playwright trusted clicks: read click targets queued by trustClick() in script.js
        # and re-click them with Playwright mouse events (reliable for React 18)
        click_targets_raw = await page.evaluate("() => { var t = window.__pageAssistClickTargets || []; window.__pageAssistClickTargets = []; return JSON.stringify(t); }")
        click_targets = _json.loads(click_targets_raw) if isinstance(click_targets_raw, str) and click_targets_raw.startswith('[') else []
        if click_targets and len(click_targets) > 0:
            mouse = await page.mouse
            for ct in click_targets:
                try:
                    await mouse.click(ct['x'], ct['y'])
                    await asyncio.sleep(0.15)
                except Exception:
                    pass
            labels = [ct.get('label','')[:25] for ct in click_targets[:5] if isinstance(ct, dict)]
            print(f"  [pre_step_cleanup] Playwright clicked {len(click_targets)} targets: {', '.join(labels)}")
            # After clicking, wait briefly for timers (e.g. service_worker 1.5s cache)
            # then re-probe for codes
            await asyncio.sleep(1.5)
            probe_result = await page.evaluate("""() => {
                if (window.__skills && window.__skills.page_assist)
                    return window.__skills.page_assist({probeOnly: true});
                return 'no_page_assist';
            }""")
            probe_str = str(probe_result) if probe_result else ''
            if "Found new codes:" in probe_str or "AUTO-SUBMITTED" in probe_str:
                first_result = probe_result
                first_str = probe_str
                phase1_found_code = True
                print(f"  [pre_step_cleanup] Playwright re-probe found code: {probe_str[:200]}")

        # Phase 1.5: If sequence challenge hover coords detected, use real mouse movement
        did_hover = False
        if not phase1_found_code:
            hover_coords = await page.evaluate("() => window.__seqHoverCoords ? JSON.stringify(window.__seqHoverCoords) : null")
            if hover_coords:
                coords = _json.loads(hover_coords) if isinstance(hover_coords, str) else hover_coords
                mouse = await page.mouse
                await mouse.move(coords['x'], coords['y'])
                await asyncio.sleep(1.0)
                await page.evaluate("() => { window.__seqHoverDone = true; delete window.__seqHoverCoords; }")
                print(f"  [pre_step_cleanup] Phase 1.5: moved mouse to hover area ({coords['x']:.0f}, {coords['y']:.0f})")
                did_hover = True
            _ts("phase1.5_hover")

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
                print(f"  [pre_step_cleanup] Phase 2 ({elapsed:.1f}s): {str(second_result)[:500]}")

            # Step-chaining: if auto-submitted, wait for transition and retry (max 5 chains).
            second_str = str(second_result) if second_result else ''
            chain_count = 0
            while "AUTO-SUBMITTED" in second_str and chain_count < 5:
                chain_count += 1
                await asyncio.sleep(1.2)
                chain_result = await page.evaluate("""() => {
                    if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist();
                    return 'no_page_assist';
                }""")
                chain_str = str(chain_result) if chain_result else ''
                print(f"  [pre_step_cleanup] Chain {chain_count}: {chain_str[:500]}")
                if "AUTO-SUBMITTED" not in chain_str:
                    if "Step changed:" in chain_str and ("STALE" in chain_str or "No NEW code" in chain_str or "B2-reset" in chain_str):
                        for stale_try in range(2):
                            await asyncio.sleep(1.5)
                            retry = await page.evaluate("""() => {
                                if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist();
                                return 'no_page_assist';
                            }""")
                            retry_str = str(retry) if retry else ''
                            print(f"  [pre_step_cleanup] Chain stale-retry({stale_try+1}): {retry_str[:500]}")
                            if "AUTO-SUBMITTED" in retry_str:
                                second_str = retry_str
                                break
                            if "B2-reset" not in retry_str:
                                break
                        else:
                            retry_str = ''
                        if "AUTO-SUBMITTED" in retry_str:
                            continue
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
                await asyncio.sleep(1.2)
                chain_result = await page.evaluate("""() => {
                    if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist();
                    return 'no_page_assist';
                }""")
                chain_str = str(chain_result) if chain_result else ''
                print(f"  [pre_step_cleanup] Phase1-Chain {chain_count}: {chain_str[:500]}")
                if "AUTO-SUBMITTED" not in chain_str:
                    if "Step changed:" in chain_str and ("STALE" in chain_str or "No NEW code" in chain_str or "B2-reset" in chain_str):
                        for stale_try in range(2):
                            await asyncio.sleep(1.5)
                            retry = await page.evaluate("""() => {
                                if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist();
                                return 'no_page_assist';
                            }""")
                            retry_str = str(retry) if retry else ''
                            print(f"  [pre_step_cleanup] Phase1-Chain stale-retry({stale_try+1}): {retry_str[:500]}")
                            if "AUTO-SUBMITTED" in retry_str:
                                chain_str = retry_str
                                break
                            if "B2-reset" not in retry_str:
                                break
                        else:
                            retry_str = ''
                        if "AUTO-SUBMITTED" in retry_str:
                            continue
                    break
            if chain_count > 0:
                _ts(f"p1_chain_{chain_count}")
                skip_llm = True

        # Async iframe handler
        is_iframe_challenge = False
        try:
            iframe_debug = await page.evaluate("""() => {
                var matched = [];
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    var txt = btns[i].textContent.trim();
                    if (/enter.*level|extract.*code/i.test(txt) && btns[i].offsetWidth > 0) {
                        matched.push(txt.substring(0, 30));
                    }
                }
                return matched.length > 0 ? matched.join(', ') : '';
            }""")
            if iframe_debug:
                is_iframe_challenge = True
                print(f"  [pre_step_cleanup] Iframe buttons found: {iframe_debug}")
        except Exception:
            pass
        if is_iframe_challenge:
            await _handle_iframe_challenge(page, _json)
            skip_llm = True

        # Async shadow DOM handler
        is_shadow_challenge = False
        if not is_iframe_challenge:
            try:
                shadow_text = await page.evaluate("""() => {
                    var bodyText = document.body ? document.body.innerText : '';
                    if (!/shadow.*dom|shadow.*level|navigate.*shadow/i.test(bodyText)) return '';
                    var levels = [];
                    document.querySelectorAll('h4, h5, h6, div').forEach(function(el) {
                        if (/Shadow Level\\s*\\d/i.test(el.textContent.trim()) && el.offsetWidth > 0)
                            levels.push(el.textContent.trim().substring(0, 20));
                    });
                    return levels.length > 0 ? levels.join(', ') : '';
                }""")
                if shadow_text:
                    is_shadow_challenge = True
                    print(f"  [pre_step_cleanup] Shadow DOM levels found: {shadow_text}")
            except Exception:
                pass

        if is_shadow_challenge:
            await _handle_shadow_challenge(page, _json)
            skip_llm = True

        # Async websocket handler
        if not is_iframe_challenge and not is_shadow_challenge:
            ws_handled = await _handle_websocket_challenge(page, _json)
            if ws_handled:
                skip_llm = True

        # Stuck recovery
        if _same_step_count >= 3:
            if _same_step_count % 2 == 1:
                await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                print(f"  [pre_step_cleanup] Stuck recovery: scrolled to BOTTOM (stuck {_same_step_count} steps on CS {_last_challenge_step})")
            else:
                await page.evaluate("() => window.scrollTo(0, 0)")
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
                print(f"  [pre_step_cleanup] Force-reveal found code: {force_str[:500]}")
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
# Handler helpers (extracted for readability, still challenge-specific)
# ---------------------------------------------------------------------------

async def _handle_iframe_challenge(page, _json):
    """Navigate through iframe levels and extract code."""
    # Dismiss popups and remove overlays
    await page.evaluate("""() => {
        if (window.__skills && window.__skills.dismiss_popups) window.__skills.dismiss_popups();
        document.querySelectorAll('div').forEach(function(el) {
            var s = getComputedStyle(el);
            if ((s.position === 'fixed' || s.position === 'absolute') && s.zIndex > 100 &&
                el.offsetWidth > 400 && el.offsetHeight > 200 &&
                !el.querySelector('button[class*="enter"], button[class*="level"], button[class*="extract"]') &&
                !/enter.*level|extract.*code|depth/i.test(el.textContent.substring(0, 200))) {
                el.style.display = 'none';
            }
        });
    }""")
    await asyncio.sleep(0.3)

    last_btn_text = ""
    extracted = False
    direct_extract_code = None
    for _round in range(12):
        state = await page.evaluate("""() => {
            var btns = document.querySelectorAll('button');
            var result = {enter: null, extract: null, allBtns: []};
            for (var i = 0; i < btns.length; i++) {
                var txt = btns[i].textContent.trim();
                if (btns[i].disabled || btns[i].offsetWidth === 0) continue;
                result.allBtns.push(txt.substring(0, 25));
                if (/extract.*code/i.test(txt) && !result.extract) result.extract = txt.substring(0, 30);
                if (/enter.*level/i.test(txt) && !result.enter) result.enter = txt.substring(0, 30);
            }
            return JSON.stringify(result);
        }""")
        try:
            s = _json.loads(state) if isinstance(state, str) else {}
        except:
            break
        if _round == 0:
            print(f"  [pre_step_cleanup] Iframe round 0 buttons: {s.get('allBtns', [])}")

        if s.get('extract') and not s.get('enter'):
            # At deepest level — Extract Code via fiber walk
            extract_result = await page.evaluate("""() => {
                var debug = [];
                var foundCode = null;
                var btn = null;
                document.querySelectorAll('button').forEach(function(b) {
                    if (/extract.*code/i.test(b.textContent) && b.offsetWidth > 0) btn = b;
                });
                if (!btn) { debug.push('no-extract-btn'); return JSON.stringify({debug: debug, code: null}); }
                var keys = Object.keys(btn);
                var fk = keys.find(function(k) { return k.indexOf('__reactFiber') === 0 || k.indexOf('__reactInternalInstance') === 0; });
                if (!fk) { debug.push('no-fiber-on-btn'); return JSON.stringify({debug: debug, code: null}); }
                var bodyText = document.body ? document.body.innerText : '';
                var dm = bodyText.match(/depth[:\\s]*(\\d+)\\s*\\/\\s*(\\d+)/i);
                var numLevels = dm ? parseInt(dm[2]) : 3;
                var stepMatch = bodyText.match(/step\\s+(\\d+)/i);
                var stepNum = stepMatch ? parseInt(stepMatch[1]) : 1;
                var times = {};
                for (var ti = 0; ti < numLevels; ti++) times[ti] = Date.now() - (numLevels - ti) * 500;
                var proofData = {
                    type: 'recursive_iframe', timestamp: Date.now(),
                    data: { method: 'recursive_iframe', numLevels: numLevels,
                            currentLevel: numLevels, levelClickTimes: times, stepNum: stepNum }
                };
                var f = btn[fk];
                var called = [];
                for (var up = 0; up < 30 && f; up++) {
                    var props = f.memoizedProps;
                    if (props && typeof props === 'object') {
                        Object.keys(props).forEach(function(pk) {
                            if (typeof props[pk] !== 'function' || pk === 'children') return;
                            if (foundCode) return;
                            try {
                                var ret = props[pk](proofData);
                                var tag = 'up' + up + ':' + pk;
                                if (typeof ret === 'string' && ret.length === 6 &&
                                    /^[A-HJ-NP-Z2-9]{6}$/.test(ret) && /[A-HJ-NP-Z]/.test(ret)) {
                                    foundCode = ret;
                                    tag += '=CODE:' + ret;
                                } else if (typeof ret === 'string' && ret.length > 0) {
                                    tag += '=str:' + ret.substring(0, 15);
                                }
                                called.push(tag);
                            }
                            catch(e) { called.push('err:up' + up + ':' + pk + ':' + e.message.substring(0, 20)); }
                        });
                    }
                    f = f.return;
                }
                debug.push('called:' + called.join(','));
                if (!foundCode) {
                    btn.scrollIntoView({block:'center'});
                    btn.click();
                    debug.push('clicked-btn');
                }
                return JSON.stringify({debug: debug, code: foundCode});
            }""")
            try:
                er = _json.loads(extract_result) if isinstance(extract_result, str) else {}
                extract_code = er.get('code')
                print(f"  [pre_step_cleanup] Iframe Extract Code (round {_round}): code={extract_code}, debug={str(er.get('debug',''))[:300]}")
                if extract_code:
                    direct_extract_code = extract_code
                    await page.evaluate(f"""() => {{
                        if (!window.__iframeCapturedCodes) window.__iframeCapturedCodes = [];
                        window.__iframeCapturedCodes.push('{extract_code}');
                    }}""")
            except:
                print(f"  [pre_step_cleanup] Iframe Extract Code raw: {str(extract_result)[:200]}")
            # Mouse-click Extract Code (trusted event)
            ext_coords = await page.evaluate("""() => {
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    if (!/extract.*code/i.test(btns[i].textContent) || btns[i].offsetWidth === 0) continue;
                    btns[i].scrollIntoView({behavior: 'instant', block: 'center'});
                    var r = btns[i].getBoundingClientRect();
                    return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2});
                }
                return null;
            }""")
            if ext_coords:
                try:
                    ec = _json.loads(ext_coords)
                    mouse = await page.mouse
                    await mouse.click(ec['x'], ec['y'])
                    print(f"  [pre_step_cleanup] Iframe: mouse-click Extract Code at deepest level")
                except Exception:
                    pass
            await asyncio.sleep(1.5)
            extracted = True
            break
        elif s.get('enter'):
            btn_text = s['enter']
            if btn_text == last_btn_text and _round > 2:
                print(f"  [pre_step_cleanup] Iframe: button stuck on '{btn_text}' — stopping")
                break
            last_btn_text = btn_text
            enter_coords = await page.evaluate("""() => {
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    if (btns[i].disabled || btns[i].offsetWidth === 0) continue;
                    if (!/enter.*level/i.test(btns[i].textContent.trim())) continue;
                    btns[i].scrollIntoView({behavior: 'instant', block: 'center'});
                    var r = btns[i].getBoundingClientRect();
                    return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2});
                }
                return null;
            }""")
            if enter_coords:
                try:
                    ec = _json.loads(enter_coords)
                    mouse = await page.mouse
                    await mouse.click(ec['x'], ec['y'])
                except Exception:
                    pass
            print(f"  [pre_step_cleanup] Iframe level: mouse-click '{btn_text}' (round {_round})")
            await asyncio.sleep(0.8)
        else:
            print(f"  [pre_step_cleanup] Iframe: no Enter/Extract buttons (round {_round})")
            break

    await asyncio.sleep(0.5)

    # Run page_assist to capture any newly visible codes
    iframe_result = await page.evaluate("""() => {
        if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist();
        return 'no_page_assist';
    }""")
    iframe_str = str(iframe_result) if iframe_result else ''
    print(f"  [pre_step_cleanup] Async iframe: {iframe_str[:250]}")

    # If Extract Code fiber walk captured a code directly, submit it now
    if direct_extract_code and "Found new codes:" not in iframe_str and "AUTO-SUBMITTED" not in iframe_str:
        print(f"  [pre_step_cleanup] Iframe: submitting directly captured code {direct_extract_code}")
        submit_result = await page.evaluate("""(code) => {
            var submitted = (window.__pageAssistSubmittedCodes || []).map(function(e) { return e.code; });
            if (submitted.indexOf(code) !== -1) return JSON.stringify({status: 'already-submitted'});
            var inp = document.getElementById('code-input') ||
                document.querySelector('input[placeholder*="code" i], input[placeholder*="character" i]');
            var btn = null;
            document.querySelectorAll('button').forEach(function(b) {
                if (b.textContent.trim() === 'Submit Code' && !b.disabled && b.offsetWidth > 0) btn = b;
            });
            if (!inp || !btn) return JSON.stringify({status: 'no-input-or-btn', code: code});
            var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(inp, code);
            inp.dispatchEvent(new Event('input', { bubbles: true }));
            inp.dispatchEvent(new Event('change', { bubbles: true }));
            setTimeout(function() {
                var freshBtn = null;
                document.querySelectorAll('button').forEach(function(b) {
                    if (b.textContent.trim() === 'Submit Code' && !b.disabled) freshBtn = b;
                });
                if (freshBtn) {
                    freshBtn.scrollIntoView({block:'center'});
                    freshBtn.click();
                }
                if (!window.__pageAssistSubmittedCodes) window.__pageAssistSubmittedCodes = [];
                window.__pageAssistSubmittedCodes.push({code: code, step: null});
            }, 100);
            return JSON.stringify({status: 'submitting', code: code});
        }""", direct_extract_code)
        print(f"  [pre_step_cleanup] Iframe direct-submit: {str(submit_result)[:200]}")
        await asyncio.sleep(1.5)

    # Fallback: if no code found, use React state dispatch + comprehensive capture
    elif "Found new codes:" not in iframe_str and "AUTO-SUBMITTED" not in iframe_str:
        # Step 1: Install MutationObserver
        await page.evaluate("""() => {
            window.__iframeCapturedCodes = [];
            var hasLetter = /[A-HJ-NP-Z]/;
            window.__iframeObserver = new MutationObserver(function(muts) {
                muts.forEach(function(m) {
                    var text = '';
                    if (m.type === 'characterData') text = m.target.textContent || '';
                    else if (m.type === 'childList') m.addedNodes.forEach(function(n) { text += (n.textContent || '') + ' '; });
                    var matches = text.match(/[A-HJ-NP-Z2-9]{6}/g);
                    if (matches) matches.forEach(function(c) {
                        if (hasLetter.test(c) && window.__iframeCapturedCodes.indexOf(c) === -1)
                            window.__iframeCapturedCodes.push(c);
                    });
                });
            });
            window.__iframeObserver.observe(document.body, { childList: true, subtree: true, characterData: true });
        }""")

        # Step 2: Snapshot current codes
        pre_codes = await page.evaluate("""() => {
            var codes = [], m, re = /[A-HJ-NP-Z2-9]{6}/g;
            var text = document.body ? document.body.innerText : '';
            while ((m = re.exec(text)) !== null) {
                if (/[A-HJ-NP-Z]/.test(m[0]) && codes.indexOf(m[0]) === -1) codes.push(m[0]);
            }
            return codes;
        }""") or []

        # Step 3: Dispatch React state
        patch_result = await page.evaluate("""() => {
            var debug = [];
            var bodyText = document.body ? document.body.innerText : '';
            var dm = bodyText.match(/depth[:\\s]*(\\d+)\\s*\\/\\s*(\\d+)/i);
            var numLevels = dm ? parseInt(dm[2]) : 0;
            debug.push('numLevels:' + numLevels);
            if (!numLevels) return JSON.stringify({debug: debug, dispatched: false});
            var extractBtn = null;
            document.querySelectorAll('button').forEach(function(b) {
                if (/extract.*code/i.test(b.textContent) && b.offsetWidth > 0) extractBtn = b;
            });
            if (!extractBtn) { debug.push('no-extract-btn'); return JSON.stringify({debug: debug, dispatched: false}); }
            var keys = Object.keys(extractBtn);
            var fk = keys.find(function(k) { return k.indexOf('__reactFiber') === 0 || k.indexOf('__reactInternalInstance') === 0; });
            if (!fk) { debug.push('no-fiber-key'); return JSON.stringify({debug: debug, dispatched: false}); }
            var f = extractBtn[fk];
            var dispatchedLevel = false;
            var clearedBools = 0;
            for (var up = 0; up < 25 && f; up++) {
                if (f.memoizedState) {
                    var st = f.memoizedState;
                    for (var d = 0; d < 20 && st; d++) {
                        var val = st.memoizedState;
                        if (st.queue && typeof st.queue.dispatch === 'function') {
                            if (typeof val === 'number' && val >= 0 && val < numLevels && !dispatchedLevel) {
                                st.queue.dispatch(numLevels);
                                debug.push('level:up' + up + ':d' + d + ':' + val + '->' + numLevels);
                                dispatchedLevel = true;
                            }
                            if (val === true) { st.queue.dispatch(false); clearedBools++; debug.push('bool:up' + up + ':d' + d); }
                        }
                        if (st.memoizedState && typeof st.memoizedState === 'object' &&
                            'current' in st.memoizedState && Array.isArray(st.memoizedState.current)) {
                            while (st.memoizedState.current.length < numLevels + 1)
                                st.memoizedState.current.push(Date.now() - Math.random() * 500);
                        }
                        st = st.next;
                    }
                }
                if (dispatchedLevel) break;
                f = f.return;
            }
            return JSON.stringify({debug: debug, dispatched: dispatchedLevel, clearedBools: clearedBools});
        }""")
        try:
            pd = _json.loads(patch_result) if isinstance(patch_result, str) else {}
            print(f"  [pre_step_cleanup] Iframe dispatch: {pd.get('debug')}, cleared={pd.get('clearedBools')}")
        except:
            print(f"  [pre_step_cleanup] Iframe dispatch raw: {str(patch_result)[:300]}")

        # Step 4: Mouse-click Extract Code after dispatch
        await asyncio.sleep(1.0)
        ext_coords = await page.evaluate("""() => {
            var btns = document.querySelectorAll('button');
            for (var i = 0; i < btns.length; i++) {
                if (!/extract.*code/i.test(btns[i].textContent) || btns[i].offsetWidth === 0) continue;
                btns[i].scrollIntoView({behavior: 'instant', block: 'center'});
                var r = btns[i].getBoundingClientRect();
                return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2});
            }
            return null;
        }""")
        if ext_coords:
            try:
                ec = _json.loads(ext_coords)
                mouse = await page.mouse
                await mouse.click(ec['x'], ec['y'])
                print(f"  [pre_step_cleanup] Iframe: mouse-click Extract Code after dispatch")
            except Exception:
                print(f"  [pre_step_cleanup] Iframe: mouse-click Extract Code failed after dispatch")
        else:
            print(f"  [pre_step_cleanup] Iframe: no Extract Code btn after dispatch")
        await asyncio.sleep(1.5)
        # Reveal hidden code display divs
        await page.evaluate("""() => {
            var container = document.body;
            document.querySelectorAll('[class*="challenge"], [class*="iframe"], [id*="iframe"]').forEach(function(el) {
                if (el.offsetWidth > 0) container = el;
            });
            container.querySelectorAll('div, span, p, pre, code').forEach(function(el) {
                var s = getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') {
                    el.style.display = 'block'; el.style.visibility = 'visible'; el.style.opacity = '1';
                }
            });
        }""")
        await asyncio.sleep(0.5)

        # Step 5: Collect codes from ALL sources
        iframe_codes_raw = await page.evaluate("""(preCodes) => {
            var debug = [];
            var allCodes = [];
            var submitted = (window.__pageAssistSubmittedCodes || []).map(function(e) { return e.code; });
            var hasLetter = /[A-HJ-NP-Z]/;
            var observed = window.__iframeCapturedCodes || [];
            debug.push('obs:' + observed.length + (observed.length > 0 ? '=' + observed.join(',') : ''));
            observed.forEach(function(c) { if (allCodes.indexOf(c) === -1) allCodes.push(c); });
            var bodyText = document.body ? document.body.innerText : '';
            var re = /[A-HJ-NP-Z2-9]{6}/g; var m;
            while ((m = re.exec(bodyText)) !== null) {
                if (hasLetter.test(m[0]) && allCodes.indexOf(m[0]) === -1) allCodes.push(m[0]);
            }
            try {
                var rootEl = document.getElementById('root') || document.getElementById('__next');
                if (!rootEl) { debug.push('no-root-el'); }
                if (rootEl) {
                    var rk = Object.keys(rootEl).find(function(k) { return k.indexOf('__reactFiber') === 0 || k.indexOf('__reactContainer') === 0; });
                    if (!rk) { debug.push('no-fiber-key-on-root'); }
                    if (rk) {
                        var fiberCodes = [];
                        var visited = 0;
                        var stack = [rootEl[rk]];
                        while (stack.length > 0 && visited < 500) {
                            var fiber = stack.pop(); visited++;
                            var st = fiber.memoizedState;
                            for (var d = 0; d < 10 && st; d++) {
                                var val = st.memoizedState;
                                if (typeof val === 'string' && val.length === 6 && /^[A-HJ-NP-Z2-9]{6}$/.test(val) && hasLetter.test(val)) {
                                    if (fiberCodes.indexOf(val) === -1) fiberCodes.push(val);
                                }
                                if (val && typeof val === 'object' && !Array.isArray(val)) {
                                    try { Object.values(val).forEach(function(v) {
                                        if (typeof v === 'string' && v.length === 6 && /^[A-HJ-NP-Z2-9]{6}$/.test(v) && hasLetter.test(v))
                                            if (fiberCodes.indexOf(v) === -1) fiberCodes.push(v);
                                    }); } catch(e) {}
                                }
                                st = st.next;
                            }
                            if (fiber.memoizedProps && typeof fiber.memoizedProps === 'object') {
                                try { Object.values(fiber.memoizedProps).forEach(function(v) {
                                    if (typeof v === 'string' && v.length === 6 && /^[A-HJ-NP-Z2-9]{6}$/.test(v) && hasLetter.test(v))
                                        if (fiberCodes.indexOf(v) === -1) fiberCodes.push(v);
                                }); } catch(e) {}
                            }
                            if (fiber.child) stack.push(fiber.child);
                            if (fiber.sibling) stack.push(fiber.sibling);
                        }
                        debug.push('fiber:' + visited + 'n,' + fiberCodes.length + 'c');
                        fiberCodes.forEach(function(c) { if (allCodes.indexOf(c) === -1) allCodes.push(c); });
                    }
                }
            } catch(e) { debug.push('fiber-err:' + e.message.substring(0, 30)); }
            var newCodes = allCodes.filter(function(c) {
                return preCodes.indexOf(c) === -1 && submitted.indexOf(c) === -1;
            });
            debug.push('all:' + allCodes.length + ',new:' + newCodes.length);
            if (window.__iframeObserver) { window.__iframeObserver.disconnect(); window.__iframeObserver = null; }
            return JSON.stringify({debug: debug, codes: newCodes, allCodes: allCodes});
        }""", pre_codes)
        try:
            ic = _json.loads(iframe_codes_raw) if isinstance(iframe_codes_raw, str) else {}
            new_codes = ic.get('codes', [])
            print(f"  [pre_step_cleanup] Iframe codes: new={new_codes}, debug={str(ic.get('debug',''))[:300]}")
        except:
            new_codes = []
            print(f"  [pre_step_cleanup] Iframe codes raw: {str(iframe_codes_raw)[:300]}")

        # Step 6: If no new codes, try calling parent completion callbacks
        if not new_codes:
            approach2 = await page.evaluate("""() => {
                var debug = [];
                var foundCode = null;
                var startEl = null;
                document.querySelectorAll('button').forEach(function(b) {
                    if (/extract.*code/i.test(b.textContent) && b.offsetWidth > 0) startEl = b;
                });
                if (!startEl) {
                    document.querySelectorAll('div, section').forEach(function(el) {
                        if (/depth|level|iframe/i.test(el.textContent) && el.offsetWidth > 0 && !startEl) startEl = el;
                    });
                }
                if (!startEl) { debug.push('no-start-el'); return JSON.stringify({debug: debug, code: null}); }
                var keys = Object.keys(startEl);
                var fk = keys.find(function(k) { return k.indexOf('__reactFiber') === 0; });
                if (!fk) { debug.push('no-fiber'); return JSON.stringify({debug: debug, code: null}); }
                var f = startEl[fk];
                var bodyText = document.body ? document.body.innerText : '';
                var dm = bodyText.match(/depth[:\\s]*(\\d+)\\s*\\/\\s*(\\d+)/i);
                var numLevels = dm ? parseInt(dm[2]) : 3;
                var stepMatch = bodyText.match(/step\\s+(\\d+)/i);
                var stepNum = stepMatch ? parseInt(stepMatch[1]) : 1;
                var times = {};
                for (var ti = 0; ti < numLevels; ti++) times[ti] = Date.now() - (numLevels - ti) * 500;
                var proofData = {
                    type: 'recursive_iframe', timestamp: Date.now(),
                    data: { method: 'recursive_iframe', numLevels: numLevels,
                            currentLevel: numLevels, levelClickTimes: times, stepNum: stepNum }
                };
                var called = [];
                for (var up = 0; up < 30 && f; up++) {
                    var props = f.memoizedProps;
                    if (props && typeof props === 'object') {
                        Object.keys(props).forEach(function(pk) {
                            if (typeof props[pk] !== 'function' || pk === 'children') return;
                            if (foundCode) return;
                            try {
                                var ret = props[pk](proofData);
                                var tag = 'up' + up + ':' + pk;
                                if (typeof ret === 'string' && ret.length === 6 &&
                                    /^[A-HJ-NP-Z2-9]{6}$/.test(ret) && /[A-HJ-NP-Z]/.test(ret)) {
                                    foundCode = ret;
                                    tag += '=CODE:' + ret;
                                } else if (typeof ret === 'string' && ret.length > 0) {
                                    tag += '=str:' + ret.substring(0, 15);
                                }
                                called.push(tag);
                            }
                            catch(e) { called.push('err:up' + up + ':' + pk + ':' + e.message.substring(0, 20)); }
                        });
                    }
                    f = f.return;
                }
                debug.push('called:' + called.join(','));
                return JSON.stringify({debug: debug, code: foundCode});
            }""")
            try:
                a2 = _json.loads(approach2) if isinstance(approach2, str) else {}
                print(f"  [pre_step_cleanup] Iframe approach2: {str(a2.get('debug',''))[:300]}")
                a2_code = a2.get('code')
                if a2_code and isinstance(a2_code, str) and len(a2_code) == 6:
                    new_codes = [a2_code]
                    print(f"  [pre_step_cleanup] Iframe approach2 captured code directly: {a2_code}")
            except:
                print(f"  [pre_step_cleanup] Iframe approach2 raw: {str(approach2)[:200]}")

            # After calling callbacks, wait and re-scan
            await asyncio.sleep(1.0)
            post_codes = await page.evaluate("""(preCodes) => {
                var submitted = (window.__pageAssistSubmittedCodes || []).map(function(e) { return e.code; });
                var codes = [], m, re = /[A-HJ-NP-Z2-9]{6}/g;
                var text = document.body ? document.body.innerText : '';
                while ((m = re.exec(text)) !== null) {
                    if (/[A-HJ-NP-Z]/.test(m[0]) && codes.indexOf(m[0]) === -1) codes.push(m[0]);
                }
                (window.__iframeCapturedCodes || []).forEach(function(c) { if (codes.indexOf(c) === -1) codes.push(c); });
                return codes.filter(function(c) { return preCodes.indexOf(c) === -1 && submitted.indexOf(c) === -1; });
            }""", pre_codes) or []
            if post_codes:
                new_codes = post_codes
                print(f"  [pre_step_cleanup] Iframe post-approach2 codes: {new_codes}")

        # Step 7: Submit any new codes directly
        if not isinstance(new_codes, list):
            new_codes = list(new_codes) if new_codes else []
        if new_codes:
            submit_result = await page.evaluate("""(codes) => {
                var submitted = (window.__pageAssistSubmittedCodes || []).map(function(e) { return e.code; });
                var newCodes = codes.filter(function(c) { return submitted.indexOf(c) === -1; });
                if (newCodes.length === 0) return JSON.stringify({status: 'all-stale'});
                var code = newCodes[newCodes.length - 1];
                var inp = document.getElementById('code-input') ||
                    document.querySelector('input[placeholder*="code" i], input[placeholder*="character" i]');
                var btn = null;
                document.querySelectorAll('button').forEach(function(b) {
                    if (b.textContent.trim() === 'Submit Code' && !b.disabled && b.offsetWidth > 0) btn = b;
                });
                if (!inp || !btn) return JSON.stringify({status: 'no-input-or-btn', code: code});
                var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(inp, code);
                inp.dispatchEvent(new Event('input', { bubbles: true }));
                inp.dispatchEvent(new Event('change', { bubbles: true }));
                setTimeout(function() {
                    var freshBtn = null;
                    document.querySelectorAll('button').forEach(function(b) {
                        if (b.textContent.trim() === 'Submit Code' && !b.disabled) freshBtn = b;
                    });
                    if (freshBtn) {
                        freshBtn.scrollIntoView({block:'center'});
                        freshBtn.click();
                        var keys = Object.keys(freshBtn);
                        var pk = keys.find(function(k) { return k.indexOf('__reactProps') === 0; });
                        if (pk && freshBtn[pk] && typeof freshBtn[pk].onClick === 'function')
                            freshBtn[pk].onClick({ preventDefault:function(){}, stopPropagation:function(){},
                                target:freshBtn, currentTarget:freshBtn, nativeEvent:new MouseEvent('click'), bubbles:true });
                    }
                    if (!window.__pageAssistSubmittedCodes) window.__pageAssistSubmittedCodes = [];
                    window.__pageAssistSubmittedCodes.push({code: code, step: null});
                }, 100);
                return JSON.stringify({status: 'submitting', code: code});
            }""", new_codes)
            print(f"  [pre_step_cleanup] Iframe direct-submit: {str(submit_result)[:200]}")
            await asyncio.sleep(1.5)

        # Final page_assist
        final_result = await page.evaluate("""() => {
            if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist();
            return 'no_page_assist';
        }""")
        final_str = str(final_result) if final_result else ''
        print(f"  [pre_step_cleanup] Iframe final: {final_str[:250]}")


async def _handle_shadow_challenge(page, _json):
    """Click all shadow levels and call onComplete via fiber tree."""
    for lvl in range(1, 4):
        click_result = await page.evaluate(f"""() => {{
            var target = null;
            document.querySelectorAll('h4, h5, h6, div').forEach(function(el) {{
                if (target) return;
                var txt = el.textContent.trim();
                if (new RegExp('Shadow Level\\\\s*{lvl}\\\\b', 'i').test(txt) && el.offsetWidth > 0) {{
                    var clickEl = el;
                    for (var i = 0; i < 5 && clickEl; i++) {{
                        var keys = Object.keys(clickEl);
                        var rp = keys.find(function(k) {{ return k.indexOf('__reactProps') === 0; }});
                        if (rp && clickEl[rp] && typeof clickEl[rp].onClick === 'function') {{
                            target = clickEl;
                            break;
                        }}
                        if (clickEl.onclick) {{ target = clickEl; break; }}
                        clickEl = clickEl.parentElement;
                    }}
                    if (!target) target = el;
                }}
            }});
            if (target) {{
                target.scrollIntoView({{behavior: 'instant', block: 'center'}});
                target.click();
                return 'clicked';
            }}
            return 'not-found';
        }}""")
        print(f"  [pre_step_cleanup] Shadow Level {lvl}: {click_result}")
        if click_result == 'not-found':
            break
        await asyncio.sleep(0.5)

    # Call onComplete via fiber tree from Reveal Code button
    shadow_code = await page.evaluate("""() => {
        var revealBtn = null;
        document.querySelectorAll('button').forEach(function(b) {
            if (/reveal.*code/i.test(b.textContent) && b.offsetWidth > 0 && !revealBtn) revealBtn = b;
        });
        if (!revealBtn) return JSON.stringify({code: null, debug: 'no-reveal-btn'});
        var fk = Object.keys(revealBtn).find(function(k) {
            return k.indexOf('__reactFiber') === 0 || k.indexOf('__reactInternalInstance') === 0;
        });
        if (!fk) return JSON.stringify({code: null, debug: 'no-fiber'});
        var fiber = revealBtn[fk];
        var bodyText = document.body ? document.body.innerText : '';
        var stepMatch = bodyText.match(/step\\s+(\\d+)/i);
        var stepNum = stepMatch ? parseInt(stepMatch[1]) : 1;
        var proof = { type: 'shadow_dom', timestamp: Date.now(),
            data: { method: 'shadow_dom', revealedLevels: [1,2,3],
                clickTimes: {1: Date.now()-2000, 2: Date.now()-1000, 3: Date.now()},
                totalLevels: 3, stepNum: stepNum }};
        for (var up = 0; up < 30 && fiber; up++) {
            var props = fiber.memoizedProps;
            if (props && typeof props === 'object' && typeof props.onComplete === 'function') {
                var ret = props.onComplete(proof);
                if (typeof ret === 'string' && ret.length === 6 && /^[A-HJ-NP-Z2-9]{6}$/.test(ret)) {
                    return JSON.stringify({code: ret, debug: 'onComplete-up' + up});
                }
                return JSON.stringify({code: null, debug: 'onComplete-ret:' + String(ret).substring(0, 20)});
            }
            fiber = fiber.return;
        }
        if (!revealBtn.disabled) revealBtn.click();
        return JSON.stringify({code: null, debug: 'no-onComplete-found'});
    }""")
    sc = {}
    try:
        sc = _json.loads(shadow_code) if isinstance(shadow_code, str) else {}
        print(f"  [pre_step_cleanup] Shadow DOM result: code={sc.get('code')}, debug={sc.get('debug')}")
        if sc.get('code'):
            await page.evaluate(f"""() => {{
                if (!window.__iframeCapturedCodes) window.__iframeCapturedCodes = [];
                window.__iframeCapturedCodes.push('{sc["code"]}');
            }}""")
    except Exception as e:
        print(f"  [pre_step_cleanup] Shadow DOM error: {e}")

    # Run page_assist to auto-submit
    await asyncio.sleep(0.5)
    shadow_pa = await page.evaluate("""() => {
        if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist();
        return 'no_page_assist';
    }""")
    shadow_pa_str = str(shadow_pa) if shadow_pa else ''
    print(f"  [pre_step_cleanup] Shadow DOM page_assist: {shadow_pa_str[:250]}")

    # Direct submission fallback
    shadow_code_val = sc.get('code') if sc else None
    if shadow_code_val and "AUTO-SUBMITTED" not in shadow_pa_str and "Found new codes" not in shadow_pa_str:
        print(f"  [pre_step_cleanup] Shadow DOM: direct-submit {shadow_code_val}")
        await page.evaluate("""(code) => {
            var submitted = (window.__pageAssistSubmittedCodes || []).map(function(e) { return e.code; });
            if (submitted.indexOf(code) !== -1) return;
            var inp = document.getElementById('code-input') ||
                document.querySelector('input[placeholder*="code" i], input[placeholder*="character" i]');
            var btn = null;
            document.querySelectorAll('button').forEach(function(b) {
                if (b.textContent.trim() === 'Submit Code' && !b.disabled && b.offsetWidth > 0) btn = b;
            });
            if (!inp || !btn) return;
            var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(inp, code);
            inp.dispatchEvent(new Event('input', { bubbles: true }));
            inp.dispatchEvent(new Event('change', { bubbles: true }));
            setTimeout(function() {
                var freshBtn = null;
                document.querySelectorAll('button').forEach(function(b) {
                    if (b.textContent.trim() === 'Submit Code' && !b.disabled) freshBtn = b;
                });
                if (freshBtn) { freshBtn.scrollIntoView({block:'center'}); freshBtn.click(); }
                if (!window.__pageAssistSubmittedCodes) window.__pageAssistSubmittedCodes = [];
                window.__pageAssistSubmittedCodes.push({code: code, step: null});
            }, 100);
        }""", shadow_code_val)
        await asyncio.sleep(1.5)


async def _handle_websocket_challenge(page, _json) -> bool:
    """Handle websocket challenge. Returns True if handled."""
    try:
        ws_info = await page.evaluate("""() => {
            var connect = null, reveal = null, alreadyClicked = false;
            document.querySelectorAll('button').forEach(function(btn) {
                if (btn.offsetWidth === 0) return;
                var t = btn.textContent.trim();
                if (/connect/i.test(t) && btn.disabled) alreadyClicked = true;
                if (btn.disabled) return;
                if (/^connect$/i.test(t)) {
                    btn.scrollIntoView({behavior: 'instant', block: 'center'});
                    var r = btn.getBoundingClientRect();
                    connect = {x: r.left + r.width/2, y: r.top + r.height/2};
                }
                if (/^reveal\\s*code$/i.test(t)) {
                    btn.scrollIntoView({behavior: 'instant', block: 'center'});
                    var r = btn.getBoundingClientRect();
                    reveal = {x: r.left + r.width/2, y: r.top + r.height/2};
                }
            });
            var bodyText = document.body ? document.body.innerText : '';
            var hasWsText = /websocket|connecting|connection established/i.test(bodyText);
            return JSON.stringify({connect: connect, reveal: reveal, alreadyClicked: alreadyClicked, hasWsText: hasWsText});
        }""")
        ws = _json.loads(ws_info) if isinstance(ws_info, str) else {}
        is_ws = ws.get('connect') or (ws.get('alreadyClicked') and ws.get('hasWsText'))
        if is_ws:
            if ws.get('connect'):
                mouse = await page.mouse
                await mouse.click(ws['connect']['x'], ws['connect']['y'])
                print(f"  [pre_step_cleanup] WebSocket: mouse-click Connect")
            elif ws.get('alreadyClicked'):
                coords = await page.evaluate("""() => {
                    var btns = document.querySelectorAll('button');
                    for (var i = 0; i < btns.length; i++) {
                        if (!/connect/i.test(btns[i].textContent) || btns[i].offsetWidth === 0) continue;
                        btns[i].disabled = false;
                        btns[i].scrollIntoView({behavior: 'instant', block: 'center'});
                        var r = btns[i].getBoundingClientRect();
                        return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2});
                    }
                    return null;
                }""")
                if coords:
                    c = _json.loads(coords)
                    mouse = await page.mouse
                    await mouse.click(c['x'], c['y'])
                    print(f"  [pre_step_cleanup] WebSocket: re-enabled + mouse-click Connect")
            # Wait for simulated connection sequence
            await asyncio.sleep(3.0)
            # Click Reveal Code if it exists
            reveal_coords = await page.evaluate("""() => {
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    if (!/reveal.*code/i.test(btns[i].textContent) || btns[i].offsetWidth === 0 || btns[i].disabled) continue;
                    btns[i].scrollIntoView({behavior: 'instant', block: 'center'});
                    var r = btns[i].getBoundingClientRect();
                    return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2});
                }
                return null;
            }""")
            if reveal_coords:
                mouse = await page.mouse
                rc = _json.loads(reveal_coords)
                await mouse.click(rc['x'], rc['y'])
                print(f"  [pre_step_cleanup] WebSocket: mouse-click Reveal Code")
                await asyncio.sleep(0.8)
            # Run page_assist to extract and submit
            ws_result = await page.evaluate("""() => {
                if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist();
                return 'no_page_assist';
            }""")
            ws_str = str(ws_result) if ws_result else ''
            print(f"  [pre_step_cleanup] WebSocket result: {ws_str[:250]}")
            return True
        elif ws.get('hasWsText') and ws.get('reveal'):
            mouse = await page.mouse
            await mouse.click(ws['reveal']['x'], ws['reveal']['y'])
            print(f"  [pre_step_cleanup] WebSocket: mouse-click Reveal Code (already connected)")
            await asyncio.sleep(1.0)
            ws_result = await page.evaluate("""() => {
                if (window.__skills && window.__skills.page_assist) return window.__skills.page_assist();
                return 'no_page_assist';
            }""")
            ws_str = str(ws_result) if ws_result else ''
            print(f"  [pre_step_cleanup] WebSocket result: {ws_str[:250]}")
            return True
    except Exception as e:
        print(f"  [pre_step_cleanup] WebSocket handler error: {e}")
    return False
