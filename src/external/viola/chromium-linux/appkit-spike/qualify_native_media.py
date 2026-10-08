# SPDX-License-Identifier: GPL-3.0-only
"""Sustained preview presentation and foreground/background lifecycle checks."""
import os
import time
from gi.repository import GLib
from qualify_media_stats import sample


class MediaQualification:
    def __init__(self, window, media, services, submit, layout, complete):
        self.window, self.media, self.services = window, media, services
        self.submit, self.layout, self.complete = submit, layout, complete
        self.report = layout['media_qualification'] = {'checks': []}
        GLib.timeout_add_seconds(16, self.measure)

    def measure(self):
        self.media.update_metrics()
        self.report['steady_preview'] = dict(self.media.metrics)
        cadence = self.media.metrics.get('presented_cadence', {})
        passed = cadence.get('fps', 0) >= 45 and cadence.get('max_gap_ms', 10000) < 150
        self.report['checks'].append({'sustained_preview_presentation': passed})
        self.submit(lambda: sample(self.services.engine, self.layout['media_target']), self.source_sampled)
        return False

    def source_sampled(self, result):
        self.report['source'] = result
        self.submit(lambda: self.services.send('sidebar', 'tab:activate',
            {'tabId': self.layout['tabs']['Music fixture']}))
        GLib.timeout_add_seconds(2, self.foreground)

    def foreground(self):
        passed = (self.window.state.get('activeTabId') == self.layout['tabs']['Music fixture']
                  and not self.window.sidebar.footer.player.get_visible() and self.media.generation is None)
        self.report['checks'].append({'foreground_source_hides_and_stops_preview': passed})
        if os.environ.get('VIOLA_QA_PIP') == '1':
            self.open_pip_menu()
            return False
        return self.leave_source()

    def leave_source(self):
        self.submit(lambda: self.services.send('sidebar', 'tab:activate',
            {'tabId': self.layout['tabs']['Main page — layout fixture']}))
        GLib.timeout_add_seconds(10, self.background)
        return False

    def open_pip_menu(self):
        self.previous_menu = self.services.menu
        self.deadline = time.monotonic() + 6
        def right_click():
            engine, session = self.services.engine, self.window.page_input.session
            point = engine.call('Runtime.evaluate', {'expression': """(() => {
                const f=document.querySelector('iframe');
                const d=f ? f.contentDocument : document;
                const r=d.querySelector('video').getBoundingClientRect();
                const o=f ? f.getBoundingClientRect() : {x:0,y:0};
                return {x:o.x+r.x+r.width/2,y:o.y+r.y+r.height/2};})()""", 'returnByValue': True},
                session=session)['result']['value']
            for event in ('mousePressed', 'mouseReleased'):
                engine.call('Input.dispatchMouseEvent', {'type': event, **point,
                    'button': 'right', 'clickCount': 1}, session=session)
        self.submit(right_click)
        GLib.timeout_add(100, self.pip_menu)

    def pip_menu(self):
        menu, popup = self.services.menu, self.window.open_popover
        if menu is self.previous_menu or not popup:
            if time.monotonic() < self.deadline:
                return True
            self.report['error'] = 'Video context menu did not open'
            self.complete()
            return False
        def find(items, parent=()):
            for row in items:
                path = parent + (row['index'],)
                if 'picture in picture' in row.get('label', '').lower().replace('-', ' '):
                    return path
                result = find(row.get('children', []), path)
                if result:
                    return result
        path = find(menu['items'])
        if not path:
            self.report['error'] = 'Video context menu has no PiP action'
            self.complete()
            return False
        popup._invoke(path)
        GLib.timeout_add_seconds(5, self.pip_visible)
        return False

    def pip_visible(self):
        pip = self.window.pip_window
        passed = bool(pip and pip.get_mapped() and pip.picture.get_paintable() and self.media.frames > 30)
        self.report['checks'].append({'context_pip_stays_visible_with_video': passed})
        if not passed:
            self.report['error'] = 'Context PiP did not retain its native video surface'
            self.complete()
            return False
        pip.close()
        GLib.timeout_add_seconds(2, self.pip_closed)
        return False

    def pip_closed(self):
        self.report['checks'].append({'closing_pip_preserves_browser': bool(
            self.window.pip_window is None and self.window.get_mapped() and self.media.generation is None)})
        return self.leave_source()

    def background(self):
        self.media.update_metrics()
        self.report['resumed_preview'] = dict(self.media.metrics)
        passed = (self.window.sidebar.footer.player.get_visible() and self.media.generation is not None
                  and self.media.metrics.get('presented_cadence', {}).get('fps', 0) >= 45)
        self.report['checks'].append({'leaving_source_resumes_preview': passed})
        self.complete()
        return False
