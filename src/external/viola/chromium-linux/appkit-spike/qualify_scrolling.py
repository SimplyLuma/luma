# SPDX-License-Identifier: GPL-3.0-only
"""Compositor scroll input plus DOM and imported-frame evidence."""
from pathlib import Path
import time
import os
import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GLib, GdkPixbuf


class ScrollQualification:
    def __init__(self, controls):
        self.controls = controls
        self.window = controls.window
        self.selection_setup()

    def selection_setup(self):
        self.controls.evaluate("""(() => {
          document.body.innerHTML='<p id="selectable" style="position:absolute;left:20px;top:80px;margin:0;font:20px monospace;white-space:pre">Select this text with a mouse drag</p>';
          window._selectionEvents=[];
          for(const type of ['mousedown','mousemove','mouseup'])document.addEventListener(type,e=>_selectionEvents.push({type:e.type,button:e.button,buttons:e.buttons}));
          return true;
        })()""", lambda _: GLib.timeout_add(200, self.selection_drag))

    def selection_drag(self):
        page = self.window.page
        self.controls.point(page, (22 / page.get_width(), 92 / page.get_height()), click=False)
        device = self.controls.device
        def button(pressed):
            device.call(device.session, device.service + '.Session', 'NotifyPointerButton',
                        GLib.Variant('(ib)', (272, pressed)))
        def move():
            device.call(device.session, device.service + '.Session', 'NotifyPointerMotionRelative',
                        GLib.Variant('(dd)', (180.0, 0.0)))
        GLib.timeout_add(100, lambda: button(True) or False)
        GLib.timeout_add(200, lambda: move() or False)
        GLib.timeout_add(300, lambda: button(False) or False)
        GLib.timeout_add(500, lambda: self.controls.evaluate(
            '({text:getSelection().toString(),events:_selectionEvents})', self.selection_result) or False)
        return False

    def selection_result(self, result):
        passed = len(result['text']) > 5
        self.controls.report['page_selection'] = result
        self.controls.report['checks'].append({'native_mouse_drag_selects_text': passed})
        if not passed:
            self.controls.finish('Mouse drag did not select page text')
            return
        self.setup_scrollbar()

    def setup_scrollbar(self):
        self.controls.evaluate("""(() => {
          document.body.innerHTML='<div style="height:8000px">Scrollbar drag fixture</div>';
          const style=document.createElement('style');
          style.textContent='html{overflow-y:scroll;scrollbar-gutter:stable}html,body{margin:0}';
          document.head.append(style);scrollTo(0,0);
          return {width:innerWidth,clientWidth:document.documentElement.clientWidth,height:innerHeight};
        })()""", lambda geometry: GLib.timeout_add(300, self.scrollbar_drag, geometry))

    def scrollbar_drag(self, geometry):
        self.scrollbar_geometry = geometry
        page = self.window.page
        x = (geometry['width'] + geometry['clientWidth']) / 2
        self.controls.point(page, (x / geometry['width'], 25 / geometry['height']), click=False)
        device = self.controls.device
        def button(pressed):
            device.call(device.session, device.service + '.Session', 'NotifyPointerButton',
                        GLib.Variant('(ib)', (272, pressed)))
        def move():
            device.call(device.session, device.service + '.Session', 'NotifyPointerMotionRelative',
                        GLib.Variant('(dd)', (0.0, 160.0)))
        GLib.timeout_add(100, lambda: button(True) or False)
        GLib.timeout_add(200, lambda: move() or False)
        GLib.timeout_add(350, lambda: button(False) or False)
        GLib.timeout_add(550, lambda: self.controls.evaluate('scrollY', self.scrollbar_result) or False)
        return False

    def scrollbar_result(self, distance):
        passed = distance > 500
        self.controls.report['scrollbar_drag'] = dict(geometry=self.scrollbar_geometry, distance=distance)
        self.controls.report['checks'].append({'native_scrollbar_thumb_drag': passed})
        if not passed:
            self.controls.finish('Native scrollbar thumb did not track the held pointer')
            return
        self.setup_scroll()

    def setup_scroll(self):
        self.controls.evaluate("""(() => {
          document.body.innerHTML='<div id="fixed">Fixed header</div><div id="content">Scrollable document</div><div id="edge"></div>';
          const style=document.createElement('style');style.textContent='html,body{margin:0}#edge{position:fixed;right:0;bottom:0;width:40px;height:40px;background:rgb(0,255,0);z-index:6;pointer-events:none}#fixed{position:fixed;top:0;left:0;width:100%;height:60px;background:rgb(255,0,0);z-index:5}#content{cursor:pointer;height:5000px;background:repeating-linear-gradient(#fff 0 100px,#00f 100px 200px)}';document.head.append(style);
          scrollTo(0,0);return true;
        })()""", lambda _: GLib.timeout_add(250, self.scroll))

    def scroll(self):
        self.before_size = [self.window.page.get_width(), self.window.page.get_height()]
        self.controls.point(self.window.page, (.5, .5), click=False)
        GLib.timeout_add(200, self.check_cursor)
        return False

    def check_cursor(self):
        cursor = self.window.page.get_cursor()
        passed = cursor is not None and cursor.get_name() == 'pointer'
        self.controls.report['checks'].append({'chromium_link_cursor_uses_native_theme': passed})
        if not passed:
            self.controls.finish('Chromium pointer cursor did not reach GTK')
            return False
        page = self.window.page
        from gpu_page import buffer_fits_viewport
        sized = page.texture is not None and buffer_fits_viewport((page.texture.get_width(), page.texture.get_height()), page.expected_buffer_size)
        self.controls.report['checks'].append({'displayed_frame_matches_native_allocation': sized})
        self.controls.report['transitional_frames_suppressed'] = page.rejected_sizes
        if not sized:
            self.controls.finish('Displayed page buffer is not sized for the native allocation')
            return False
        self.wheel()
        return False

    def wheel(self):
        device = self.controls.device
        device.call(device.session, device.service + '.Session', 'NotifyPointerAxis',
                    GLib.Variant('(ddu)', (0.0, 350.0 / 12, 0)))
        GLib.timeout_add(450, self.inspect)
        return False

    def inspect(self):
        self.controls.evaluate("({browserIdentity:{userAgent:navigator.userAgent,webdriver:navigator.webdriver}, y:scrollY, height:innerHeight, top:document.querySelector('#fixed').getBoundingClientRect().top, visualY:visualViewport.offsetTop, pageTop:visualViewport.pageTop, bodyY:document.body.scrollTop, scrollingY:document.scrollingElement.scrollTop})", self.measured)
        return False

    def measured(self, state):
        page = self.window.page
        report = self.controls.report
        report['scroll_state'] = state
        manual = self.window.manual_identity
        marker_ok = state['browserIdentity']['webdriver'] is (not manual)
        report['checks'].append({'manual_or_qa_identity_matches_launch_mode': marker_ok})
        if not marker_ok:
            self.controls.finish('Browser automation marker does not match launch mode')
            return
        report['scroll_frame'] = page.frame
        report['scroll_widget_before'] = self.before_size
        report['scroll_widget_after'] = [page.get_width(), page.get_height()]
        path = Path('/var/home/nick/viola-dev/appkit-spike-results') / ('scroll-frame-' + str(__import__('os').getpid()) + '.png')
        page.texture.save_to_png(str(path))
        report['scroll_frame_capture'] = str(path)
        pixels = GdkPixbuf.Pixbuf.new_from_file(str(path))
        offset = 10 * pixels.get_rowstride() + (pixels.get_width() // 2) * pixels.get_n_channels()
        sample = list(pixels.get_pixels()[offset:offset+3])
        report['scroll_fixed_header_pixel'] = sample
        visual_passed = sample[0] > 240 and sample[1] < 15 and sample[2] < 15
        report['checks'].append({'scroll_frame_fixed_header_stays_at_top': visual_passed})
        edge_offset = (pixels.get_height()-20) * pixels.get_rowstride() + (pixels.get_width()-20) * pixels.get_n_channels()
        edge = list(pixels.get_pixels()[edge_offset:edge_offset+3])
        report['document_bottom_right_pixel'] = edge
        edge_passed = edge[0] < 15 and edge[1] > 240 and edge[2] < 15
        report['checks'].append({'document_paints_to_bottom_right_edge': edge_passed})
        if not edge_passed:
            self.controls.finish('Document paint does not reach the bottom/right viewport edge')
            return

        passed = visual_passed and state['y'] > 0 and state['top'] == 0 and self.before_size == report['scroll_widget_after']
        report['checks'].append({'native_scroll_moves_document_preserves_viewport': passed})
        if not passed:
            self.controls.finish('Native scroll did not move the document')
            return
        self.controls.capture_stage('scroll', self.start_stream)

    def start_stream(self):
        self.controls.evaluate("(() => {scrollTo(0,0);window._scrollTrace=[];addEventListener('scroll',()=>{if(_scrollTrace.length<512)_scrollTrace.push([performance.now(),scrollY])});return true})()",
            lambda _: GLib.timeout_add(200, self.stream_ready))

    def stream_ready(self):
        self.stream_count = 0
        self.stream_started = time.monotonic()
        self.window.page.presentation_samples.clear()
        self.window.page_input.metrics['queue_wait_ms_max'] = 0
        self.window.page_input.metrics['ack_ms_max'] = 0
        self.window.submit(lambda: time.sleep(0.5))
        GLib.timeout_add(8, self.stream_event)
        return False

    def stream_event(self):
        device = self.controls.device
        device.call(device.session, device.service + '.Session', 'NotifyPointerAxis',
                    GLib.Variant('(ddu)', (0.0, 5.0 / 12, 0)))
        self.stream_count += 1
        if self.stream_count < 120:
            return True
        self.stream_ended = time.monotonic()
        GLib.timeout_add(600, self.stream_done)
        return False

    def stream_done(self):
        self.controls.evaluate('({y:scrollY,trace:_scrollTrace})', self.stream_report)
        return False

    def stream_report(self, result):
        trace = [point for point in result['trace'] if point[1] > 0]
        gaps = [b[0]-a[0] for a,b in zip(trace,trace[1:])]
        samples = [(stamp, identity) for stamp, identity in self.window.page.presentation_samples
                   if self.stream_started <= stamp <= self.stream_ended]
        unique = [sample for index, sample in enumerate(samples)
                  if not index or sample[1] != samples[index-1][1]]
        presentation_rate = len(unique) / (self.stream_ended - self.stream_started)
        received = [sample for sample in self.window.page.received_samples
                    if self.stream_started <= sample[0] <= self.stream_ended]
        received_count = len({sample[1] for sample in received})
        self.controls.report['scroll_stream'] = dict(events=120, distance=result['y'],
            presented_frames=len(unique), presentation_fps=presentation_rate,
            received_frames=received_count, received_fps=received_count / (self.stream_ended-self.stream_started),
            updates=len(trace), max_gap_ms=max(gaps, default=0), trace=trace,
            input_metrics=dict(self.window.page_input.metrics))
        passed = (abs(result['y'] - 600) <= 5 and len(trace) >= 30
                  and presentation_rate >= 45
                  and max(gaps, default=0) < 100
                  and self.window.page_input.metrics.get('queue_wait_ms_max', 0) < 100)
        self.controls.report['checks'].append({'continuous_scroll_distance_and_cadence': passed})
        if not passed:
            if os.environ.get('VIOLA_QA_SCROLL_DIAGNOSTIC') == '1':
                self.controls.report['error'] = 'Continuous scroll distance or cadence regressed'
            else:
                self.controls.finish('Continuous scroll distance or cadence regressed')
                return
        self.controls.evaluate('scrollTo(0,0)', lambda _: GLib.timeout_add(200, self.discrete_wheel))

    def discrete_wheel(self):
        device = self.controls.device
        device.call(device.session, device.service + '.Session', 'NotifyPointerAxisDiscrete',
                    GLib.Variant('(ui)', (0, 1)))
        GLib.timeout_add(400, lambda: self.controls.evaluate('scrollY', self.discrete_result) or False)
        return False

    def discrete_result(self, distance):
        passed = abs(distance - 120) <= 1
        self.controls.report['checks'].append({'native_wheel_notch_distance': passed, 'pixels': distance})
        if not passed:
            self.controls.finish('Native wheel notch conversion regressed')
            return
        self.controls.evaluate('scrollTo(0,1000)', lambda _: GLib.timeout_add(100, self.start_transitions))

    def start_transitions(self):
        self.transitions = 0
        GLib.timeout_add(8, self.transition)
        return False

    def transition(self):
        device = self.controls.device
        direction = 1 if self.transitions % 2 else -1
        device.call(device.session, device.service + '.Session', 'NotifyPointerMotionRelative',
                    GLib.Variant('(dd)', (float(direction * 20), 0.0)))
        device.call(device.session, device.service + '.Session', 'NotifyPointerAxis',
                    GLib.Variant('(ddu)', (0.0, direction * 0.5, 0)))
        self.transitions += 1
        if self.transitions < 120:
            return True
        GLib.timeout_add(700, self.transitions_done)
        return False

    def transitions_done(self):
        self.controls.evaluate('scrollY', self.transitions_result)
        return False

    def transitions_result(self, position):
        passed = self.window.page_input.pending == 0 and not self.window.errors
        self.controls.report['checks'].append({'scroll_latching_transitions_survive': passed,
                                               'scroll_y': position})
        if not passed:
            self.controls.finish('Scroll transition left failed or pending input')
            return
        self.controls.evaluate("""(() => {
          scrollTo(0,0);document.body.innerHTML='<div id="animation"></div>';
          document.querySelector('#animation').style.cssText='position:fixed;inset:0;background:red';
          window._animationFrames=0;window._animate=true;
          function tick(){if(!_animate)return;_animationFrames++;document.querySelector('#animation').style.backgroundColor=`hsl(${_animationFrames*7%360} 90% 50%)`;requestAnimationFrame(tick)}
          requestAnimationFrame(tick);return true;
        })()""", lambda _: GLib.timeout_add(400, self.animation_ready))

    def animation_ready(self):
        self.animation_start = time.monotonic()
        self.window.page.presentation_samples.clear()
        GLib.timeout_add(3000, self.animation_done)
        return False

    def animation_done(self):
        samples = list(self.window.page.presentation_samples)
        unique = []
        for stamp, frame in samples:
            if not unique or frame != unique[-1][1]:
                unique.append((stamp,frame))
        elapsed = time.monotonic()-self.animation_start
        gaps = [(b[0]-a[0])*1000 for a,b in zip(unique,unique[1:])]
        rate = len(unique)/elapsed
        self.controls.report['presentation_cadence'] = dict(frames=len(unique),seconds=elapsed,
            fps=rate,max_gap_ms=max(gaps,default=0))
        passed = rate >= 45 and max(gaps,default=0) < 150
        self.controls.report['checks'].append({'sustained_native_page_presentation':passed})
        self.controls.evaluate('window._animate=false;_animationFrames',
            lambda frames: self.animation_result(frames,passed))
        return False

    def animation_result(self, frames, passed):
        self.controls.report['presentation_cadence']['dom_animation_frames'] = frames
        if not passed:
            self.controls.finish('Native page presentation is below sustained cadence threshold')
            return
        self.closed_tab = self.window.state['activeTabId']
        self.controls.evaluate("""(() => {
          document.body.innerHTML='<input id="close-tab-field" style="position:fixed;top:20px;left:20px" value="Close only this tab">';
          scrollTo(0,0);return true;
        })()""", lambda _: GLib.timeout_add(200, self.focus_close_field))

    def focus_close_field(self):
        page = self.window.page
        self.controls.point(page, (50/page.get_width(),30/page.get_height()))
        GLib.timeout_add(200, lambda: self.controls.evaluate(
            "document.activeElement.id==='close-tab-field'", self.close_from_field) or False)
        return False

    def close_from_field(self, focused):
        self.controls.report['checks'].append({'ctrl_w_fixture_has_text_field_focus':bool(focused)})
        if not focused:
            self.controls.finish('Close-tab fixture did not focus its text field')
            return
        self.controls.device.chord(0xffe3, ord('w'))
        GLib.timeout_add(900, self.closed_last_tab)

    def closed_last_tab(self):
        state = self.window.state
        ids = {row['id'] for section in ('today','pinned','favorites') for row in state.get(section,[])}
        passed = self.closed_tab not in ids and bool(ids) and self.window.get_mapped()
        self.controls.report['checks'].append({'ctrl_w_last_tab_preserves_window': passed})
        if not passed:
            self.controls.finish('Ctrl+W failed to retain a window with a blank replacement')
            return False
        self.retained_tab = state['activeTabId']
        def create():
            self.window.services.navigate('http://127.0.0.1:9/', new_tab=True)
            return self.window.services.wait_state(lambda s:s.get('activeTabId') not in (None,self.retained_tab))['activeTabId']
        self.window.submit(create, self.close_second_tab)
        return False

    def close_second_tab(self, tab):
        self.closed_tab = tab
        self.window.address.grab_focus()
        self.controls.device.chord(0xffe3, ord('w'))
        GLib.timeout_add(900, self.closed_second_tab)

    def closed_second_tab(self):
        ids = {row['id'] for section in ('today','pinned','favorites') for row in self.window.state.get(section,[])}
        passed = self.closed_tab not in ids and self.retained_tab in ids and self.window.get_mapped()
        self.controls.report['checks'].append({'ctrl_w_address_focus_closes_only_active_tab':passed})
        if passed:
            if os.environ.get('VIOLA_QA_SCROLL_ONLY') == '1':
                self.controls.finish()
            else:
                self.controls.tile_shortcut()
        else:
            self.controls.finish('Ctrl+W with address focus closed the wrong surface')
        return False
