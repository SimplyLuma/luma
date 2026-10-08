# SPDX-License-Identifier: GPL-3.0-only
"""Close a real batch while one renderer geometry acknowledgement is withheld."""
import time
from concurrent.futures import Future
from urllib.parse import urlsplit
from gi.repository import GLib
from qualify_native_windows import WindowQualification
from private_input import PrivateInput


class CloseBurstQualification(WindowQualification):
    def __init__(self, manager, report, complete):
        self.manager, self.report, self.complete = manager, report, complete
        self.report['checks'] = []
        self.primary = manager.primary['window']
        self.device = PrivateInput()
        self.device.start()
        self.wait(lambda: self.primary.page_input.valid_identity(), self.populate)

    def populate(self):
        self.request = self.manager.engine.request
        self.stalled = None
        def request(method, params=None, session=None):
            if method == 'Emulation.setDeviceMetricsOverride' and self.stalled is None:
                self.stalled = Future()
                self.report['stalled_at'] = time.monotonic()
                return self.stalled
            return self.request(method, params, session)
        self.manager.engine.request = request
        url = urlsplit(self.primary.services.state['activeUrl'])
        self.fixture_url = f'{url.scheme}://{url.netloc}/layout?title=After-burst'
        def create():
            for index in range(24):
                self.primary.services.navigate(f'{url.scheme}://{url.netloc}/layout?title=Batch-{index}', new_tab=True)
        self.primary.submit(create)
        self.wait(lambda: len(self.primary.services.state.get('today', [])) >= 25, self.begin)

    def begin(self):
        self.check('twenty_five_tabs_created_with_stalled_geometry', self.stalled is not None)
        self.primary.present()
        self.primary.page.grab_focus()
        self.presses = 0
        self.started = time.monotonic()
        def press():
            if len(self.primary.services.state.get('today', [])) <= 1:
                self.report['keypresses'] = self.presses
                self.check('burst_closed_batch_without_window_loss', len(self.manager.contexts) == 1 and
                           self.manager.engine.process.poll() is None)
                self.manager.engine.request = self.request
                self.stalled.set_result({})
                self.wait(lambda: self.primary.page_input.valid_identity() and
                          self.primary.page.texture is not None, self.responsive)
                return False
            self.device.chord(0xffe3, ord('w'))
            self.presses += 1
            if self.presses >= 80:
                self.finish('Close burst stopped making progress')
                return False
            return True
        GLib.timeout_add(200, press)

    def responsive(self):
        self.check('remaining_page_has_live_input_after_burst', self.primary.page_input.valid_identity())
        self.primary.submit(lambda: self.primary.services.navigate(self.fixture_url, new_tab=True))
        self.wait(lambda: len(self.primary.services.state.get('today', [])) == 2 and
                  self.primary.page_input.valid_identity(), self.finished)

    def finished(self):
        self.check('browser_commands_work_after_burst', True)
        self.finish()

    def finish(self, error=None):
        if hasattr(self, 'request'):
            self.manager.engine.request = self.request
        self.report['completed'] = True
        if error:
            self.report['error'] = error
            self.report['state'] = self.primary.services.state
            self.report['input_target'] = self.primary.page_input.target
            self.report['input_session'] = self.primary.page_input.session
            self.report['viewport'] = self.primary.page_input.viewport
        self.device.close()
        self.complete()
