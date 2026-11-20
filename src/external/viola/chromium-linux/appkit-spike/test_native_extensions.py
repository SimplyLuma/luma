# SPDX-License-Identifier: GPL-3.0-only
import unittest
from types import SimpleNamespace
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from native_extension_prompt import ExtensionPromptPanel
from native_extensions import (ExtensionSurface, NativeExtensions, display_size, extension_rows,
                               frame_size, page_point, rows_signature, surface_target, tab_urls,
                               valid_id)

UBOL = 'ddkjiahejlhfcafbddmgiahcphecmpfh'
DARK = 'eimadpbcbfnmbkopoojfekhnkhdbieeh'
STATE = {'extensions': [
    {'id': UBOL, 'name': 'uBlock Origin Lite', 'icon': f'chrome://extension-icon/{UBOL}/24/1', 'pinned': True},
    {'id': DARK, 'name': 'Dark Reader', 'icon': f'chrome://extension-icon/{DARK}/24/1', 'pinned': False},
    {'id': 'not-an-id', 'name': 'Broken'}, 'junk'],
    'today': [{'id': 't1', 'url': f'chrome-extension://{DARK}/ui/devtools/index.html'}]}


class RowsTest(unittest.TestCase):
    def test_only_well_formed_rows(self):
        rows = extension_rows(STATE)
        self.assertEqual([row['id'] for row in rows], [UBOL, DARK])
        self.assertEqual(rows_signature(rows), ((UBOL, 'uBlock Origin Lite', True), (DARK, 'Dark Reader', False)))
        self.assertEqual(extension_rows(None), [])
        self.assertEqual(extension_rows({'extensions': 'x'}), [])

    def test_ids_are_chromium_extension_ids(self):
        self.assertTrue(valid_id(UBOL))
        self.assertFalse(valid_id(UBOL.upper()))
        self.assertFalse(valid_id(UBOL[:-1]))
        self.assertFalse(valid_id(None))


class SurfaceTargetTest(unittest.TestCase):
    TARGETS = [
        {'targetId': 'sw', 'type': 'service_worker', 'url': f'chrome-extension://{DARK}/background/index.js'},
        {'targetId': 'old', 'type': 'page', 'url': f'chrome-extension://{DARK}/ui/popup/index.html'},
        {'targetId': 'tab', 'type': 'page', 'url': f'chrome-extension://{DARK}/ui/devtools/index.html'},
        {'targetId': 'other', 'type': 'page', 'url': f'chrome-extension://{UBOL}/popup.html'},
        {'targetId': 'new', 'type': 'page', 'url': f'chrome-extension://{DARK}/ui/popup/index.html'},
    ]

    def test_the_new_page_of_that_extension_that_is_not_a_tab(self):
        target = surface_target(self.TARGETS, DARK, {'sw', 'old'}, tab_urls(STATE))
        self.assertEqual(target['targetId'], 'new')

    def test_nothing_new(self):
        self.assertIsNone(surface_target(self.TARGETS[:3], DARK, {'sw', 'old'}, tab_urls(STATE)))


class GeometryTest(unittest.TestCase):
    def test_frame_size_is_bounded(self):
        self.assertEqual(frame_size({'deviceWidth': 357.4, 'deviceHeight': 600}), (357, 600))
        self.assertIsNone(frame_size({'deviceWidth': 0, 'deviceHeight': 600}))
        self.assertIsNone(frame_size({'deviceWidth': 9000, 'deviceHeight': 600}))
        self.assertIsNone(frame_size({}))

    def test_display_size_fits_inside_the_bar(self):
        self.assertEqual(display_size((360, 500)), (360, 500))
        self.assertEqual(display_size((1600, 900)), (800, 450))

    def test_points_map_to_page_pixels(self):
        # A 360x500 page drawn at its own size.
        self.assertEqual(page_point(10, 20, (360, 500), (360, 500)), {'x': 10, 'y': 20})
        # The same page in a wider widget keeps its aspect, centred.
        self.assertEqual(page_point(130, 250, (500, 500), (360, 500)), {'x': 60, 'y': 250})
        # Drawn at half size on a phone.
        self.assertEqual(page_point(90, 125, (180, 250), (360, 500)), {'x': 180, 'y': 250})
        self.assertIsNone(page_point(10, 10, (500, 500), (360, 500)))
        self.assertIsNone(page_point(10, 10, (0, 0), (360, 500)))
        self.assertIsNone(page_point(10, 10, (360, 500), None))


class FakeServices:
    def __init__(self):
        self.sent = []
        self.requests = []
        self.engine = SimpleNamespace(request=lambda *args: self.requests.append(args))

    def send(self, surface, channel, payload=None):
        self.sent.append((surface, channel, payload))


def host(state=STATE):
    sent = []
    return SimpleNamespace(state=state, services=FakeServices(), favicons=None,
                           send=lambda channel, payload=None: sent.append((channel, payload)),
                           submit=lambda work, done=None: done(work()) if done else work(),
                           sent=sent)


class PanelTest(unittest.TestCase):
    def test_one_row_per_extension_then_manage(self):
        owner = host()
        panel = NativeExtensions(owner).panel()
        labels = []
        child = panel.get_first_child()
        while child is not None:
            if hasattr(child, 'label'):
                labels.append(child.label)
            child = child.get_next_sibling()
        self.assertEqual(labels, ['uBlock Origin Lite', 'Dark Reader', 'Manage extensions'])

    def test_manage_opens_the_extension_settings(self):
        owner = host()
        panel = NativeExtensions(owner).panel()
        panel.get_last_child().on_activate()
        self.assertEqual(owner.sent, [('ext:openControl', {'extId': None})])

    def test_run_executes_the_action(self):
        owner = host()
        extensions = NativeExtensions(owner)
        extensions.extension_targets = lambda *_: set()
        extensions.run(DARK)
        self.assertEqual(owner.services.sent, [('sidebar', 'ext:openControl', {'extId': DARK, 'action': 'execute'})])
        self.assertEqual(extensions.pending['id'], DARK)
        extensions.run('bad')
        self.assertEqual(len(owner.services.sent), 1)


class SurfaceTest(unittest.TestCase):
    def test_input_goes_to_the_page_target_only(self):
        owner = host()
        extensions = NativeExtensions(owner)
        surface = ExtensionSurface(extensions, DARK, 'Dark Reader', 'target', 'session-1')
        surface.size = (360, 500)
        surface.dispatch('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': 1, 'y': 1})
        self.assertEqual(owner.services.requests[-1][2], 'session-1')
        surface.closed = True
        surface.dispatch('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': 1, 'y': 1})
        self.assertEqual(len(owner.services.requests), 1)

    def test_a_closing_page_folds_and_is_not_closed_twice(self):
        owner = host()
        extensions = NativeExtensions(owner)
        surface = extensions.surface = ExtensionSurface(extensions, DARK, 'Dark Reader', 'target', 'session-1')
        folded = []
        owner.responsive = SimpleNamespace(panel='extension', center=SimpleNamespace(
            grown='extension', fold=lambda: folded.append(True)))
        extensions.target_gone(surface)
        self.assertIsNone(extensions.surface)
        self.assertEqual(folded, [True])
        # It closed itself: no window.close() is sent to it.
        self.assertEqual(owner.services.requests, [])

    def test_folding_the_bar_closes_the_page(self):
        owner = host()
        extensions = NativeExtensions(owner)
        surface = extensions.surface = ExtensionSurface(extensions, DARK, 'Dark Reader', 'target', 'session-1')
        owner.responsive = SimpleNamespace(panel='extension', center=SimpleNamespace(grown=None))
        extensions.surface_folded(surface)
        self.assertIsNone(extensions.surface)
        self.assertEqual(owner.services.requests,
                         [('Runtime.evaluate', {'expression': 'window.close()'}, 'session-1')])
        extensions.surface_folded(surface)
        self.assertEqual(len(owner.services.requests), 1)


class RemovePromptTest(unittest.TestCase):
    def test_a_removal_is_the_red_action(self):
        panel = ExtensionPromptPanel({'id': 'r1', 'type': 'extension', 'title': 'Remove "Dark Reader"?',
                                      'accept': 'Remove', 'cancel': 'Cancel', 'danger': True}, lambda *_: None)
        self.assertTrue(panel.accept_button.has_css_class('lumaui-panel-confirm-action'))
        self.assertFalse(panel.accept_button.has_css_class('viola-signin-action'))


class PageDropTest(unittest.TestCase):
    def test_a_drop_is_enter_over_drop_with_the_files(self):
        from page_drop import drag_events, local_paths
        files = [SimpleNamespace(get_path=lambda: '/run/user/1000/doc/ab12/ext.crx'),
                 SimpleNamespace(get_path=lambda: None)]
        paths = local_paths(files)
        self.assertEqual(paths, ['/run/user/1000/doc/ab12/ext.crx'])
        events = drag_events({'x': 10, 'y': 20}, paths)
        self.assertEqual([params['type'] for _method, params in events], ['dragEnter', 'dragOver', 'drop'])
        for method, params in events:
            self.assertEqual(method, 'Input.dispatchDragEvent')
            self.assertEqual((params['x'], params['y']), (10, 20))
            self.assertEqual(params['data']['files'], paths)

    def test_files_are_bounded(self):
        from page_drop import MAX_FILES, local_paths
        files = [SimpleNamespace(get_path=lambda: '/tmp/a')] * (MAX_FILES + 5)
        self.assertEqual(len(local_paths(files)), MAX_FILES)


if __name__ == '__main__':
    unittest.main()
