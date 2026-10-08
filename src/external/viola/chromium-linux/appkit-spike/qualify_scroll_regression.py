# SPDX-License-Identifier: GPL-3.0-only
"""Scroll regression with geometry refreshed while scrollbars are present."""
from gi.repository import GLib
from qualify_scrolling import ScrollQualification


class ScrollRegression(ScrollQualification):
    def scrollbar_drag(self, geometry):
        adapter = self.window.page_input
        def ready(result):
            self.controls.report['layout_with_scrollbars'] = result
            adapter.viewport = dict(result['cssLayoutViewport'],
                                    input_zoom=result['cssVisualViewport'].get('zoom', 1) *
                                    result['cssVisualViewport'].get('scale', 1))
            super(ScrollRegression, self).scrollbar_drag(geometry)
        adapter.page_requests.call('Page.getLayoutMetrics', {}, adapter.session, ready)
        return False

    def scrollbar_result(self, distance):
        self.controls.report['scrollbar_drag'] = dict(geometry=self.scrollbar_geometry, distance=distance)
        self.controls.report['checks'].append({'refreshed_root_scrollbar_drag': distance > 500})
        if distance <= 500:
            self.controls.finish('Native scrollbar thumb did not track the held pointer')
            return
        self.gutter_case = 0
        self.setup_gutter_case()

    def setup_gutter_case(self):
        fixtures = [
            "document.body.innerHTML='<div style=\"width:8000px;height:20px\">Horizontal</div>';document.documentElement.style.cssText='overflow-x:scroll;overflow-y:hidden';scrollTo(0,0);return {x:25,y:(innerHeight+document.documentElement.clientHeight)/2,axis:'x'}",
            "document.documentElement.style.cssText='overflow:hidden';document.body.innerHTML='<div id=nested style=\"position:absolute;left:60px;top:60px;width:400px;height:400px;overflow:scroll\"><div style=\"height:8000px\">Nested</div></div>';return {x:60+(400+nested.clientWidth)/2,y:80,axis:'y'}"
        ]
        self.controls.evaluate('(() => {' + fixtures[self.gutter_case] + '})()',
                               lambda geometry: GLib.timeout_add(250, self.drag_gutter_case, geometry))

    def drag_gutter_case(self, geometry):
        adapter = self.window.page_input
        def ready(result):
            adapter.viewport = dict(result['cssLayoutViewport'], input_zoom=result['cssVisualViewport']['zoom'])
            page = self.window.page
            self.controls.point(page, (geometry['x']/page.get_width(),geometry['y']/page.get_height()), click=False)
            device = self.controls.device
            def button(pressed):
                device.call(device.session, device.service + '.Session', 'NotifyPointerButton', GLib.Variant('(ib)', (272, pressed)))
            def move():
                device.call(device.session, device.service + '.Session', 'NotifyPointerMotionRelative', GLib.Variant('(dd)', (120.0,0.0) if geometry['axis']=='x' else (0.0,120.0)))
            GLib.timeout_add(100, lambda: button(True) or False)
            GLib.timeout_add(200, lambda: move() or False)
            GLib.timeout_add(350, lambda: button(False) or False)
            expression = 'scrollX' if self.gutter_case == 0 else 'nested.scrollTop'
            GLib.timeout_add(550, lambda: self.controls.evaluate(expression, self.gutter_case_result) or False)
        adapter.page_requests.call('Page.getLayoutMetrics', {}, adapter.session, ready)
        return False

    def gutter_case_result(self, distance):
        name = ('horizontal_root_thumb_drag','nested_thumb_drag')[self.gutter_case]
        self.controls.report['checks'].append({name: distance > 500, 'distance': distance})
        if distance <= 500:
            self.controls.finish(name + ' failed')
            return
        self.gutter_case += 1
        if self.gutter_case < 2:
            self.setup_gutter_case()
        else:
            self.controls.evaluate("document.documentElement.style.cssText='overflow-y:scroll';true", lambda _: self.setup_scroll())

    def measured(self, state):
        # Pixel readback is covered by the visual fixture; this regression
        # measures real compositor input and distinct presented frames.
        self.controls.report['scroll_state'] = state
        if not state['y'] or state['top'] != 0:
            self.controls.finish('Scrolling displaced the viewport or did not move')
            return
        self.start_stream()

    def transitions_result(self, position):
        passed = self.window.page_input.pending == 0 and not self.window.errors
        self.controls.report['checks'].append({'scroll_latching_transitions_survive': passed})
        self.controls.finish(None if passed else 'Pending input after scroll transitions')
