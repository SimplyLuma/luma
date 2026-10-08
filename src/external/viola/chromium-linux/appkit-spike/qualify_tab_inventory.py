# SPDX-License-Identifier: GPL-3.0-only
"""Every live page must have a sidebar identity, including startup and popups."""
from gi.repository import GLib
from qualify_native_windows import WindowQualification


class TabInventoryQualification(WindowQualification):
    def __init__(self, manager, report, complete):
        self.manager, self.report, self.complete = manager, report, complete
        self.report['checks'] = []
        self.wait(lambda: len(manager.contexts) == 2 and all(
            c['window'].get_mapped() and c['window'].services.state and c['window'].page.frame
            for c in manager.contexts.values()), self.startup)

    def finish(self, error=None):
        self.report['completed'] = True
        if error:
            self.report['error'] = error
        self.complete()

    @staticmethod
    def rows(window):
        state = window.services.state or {}
        return [row for key in ('favorites', 'pinned', 'today') for row in state.get(key, [])]

    def inventory(self, stage, then):
        windows = [c['window'] for c in self.manager.contexts.values()]
        self.check(stage + '_active_pages_listed', all(
            w.services.state.get('activeTabId') in {r['id'] for r in self.rows(w)} for w in windows))
        live = {r['id'] for w in windows for r in self.rows(w) if r.get('alive')}
        def got(result):
            pages = [t for t in result['targetInfos'] if t['type'] == 'page']
            self.report.setdefault('counts', []).append(dict(stage=stage, pages=len(pages), listed_live=len(live)))
            self.check(stage + '_all_live_pages_listed', len(pages) == len(live))
            then()
        self.manager.submit(lambda: self.manager.engine.call('Target.getTargets'), got)

    def startup(self):
        self.first, self.second = [c['window'] for c in self.manager.contexts.values()]
        self.inventory('startup', self.close_remote)

    def close_remote(self):
        self.closed_id = self.first.services.state['activeTabId']
        self.closed_target = self.first.page_input.target
        self.second.send('tab:close', {'tabId': self.closed_id})
        self.wait(lambda: all(self.closed_id not in {r['id'] for r in self.rows(w)}
            for w in (self.first,self.second)) and self.first.page_input.valid_identity()
            and self.first.page_input.target != self.closed_target, self.remote_closed)

    def remote_closed(self):
        def got(result):
            self.check('remote_close_destroys_original_target', self.closed_target not in {t['targetId'] for t in result['targetInfos']})
            self.inventory('remote_close', self.takeover)
        self.manager.submit(lambda: self.manager.engine.call('Target.getTargets'), got)

    def takeover(self):
        self.take_id = self.first.services.state['activeTabId']
        self.second.send('tab:activate', {'tabId': self.take_id})
        self.wait(lambda: self.second.services.state['activeTabId'] == self.take_id and
            self.first.services.state['activeTabId'] != self.take_id and self.first.page_input.valid_identity(),
            lambda: self.inventory('takeover', self.popup))

    def popup(self):
        def create():
            return self.manager.engine.call('Runtime.evaluate', {'expression':
                "window.open('about:blank','_blank','width=440,height=320'); true", 'userGesture': True},
                session=self.second.page_input.session)
        self.second.submit(create, lambda _: self.wait(lambda: len(self.manager.contexts) == 3 and all(
            c['window'].page_input.valid_identity() for c in self.manager.contexts.values()),
            lambda: self.inventory('popup', self.close_popup_remote)))

    def close_popup_remote(self):
        popup = next(c['window'] for c in self.manager.contexts.values()
                     if c['window'] not in (self.first, self.second))
        identity, target = popup.services.state['activeTabId'], popup.page_input.target
        self.first.send('tab:close', {'tabId': identity})
        def closed():
            def got(result):
                self.check('remote_popup_close_destroys_target', target not in {t['targetId'] for t in result['targetInfos']})
                self.inventory('popup_close', self.finish)
            self.manager.submit(lambda: self.manager.engine.call('Target.getTargets'), got)
        self.wait(lambda: popup.services.state['activeTabId'] != identity and
            popup.page_input.valid_identity() and all(identity not in {r['id'] for r in self.rows(c['window'])}
            for c in self.manager.contexts.values()), closed)
