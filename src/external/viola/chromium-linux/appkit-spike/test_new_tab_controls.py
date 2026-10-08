# SPDX-License-Identifier: GPL-3.0-only
"""GTK widget/action regressions; run on an owned private display."""
import unittest
from types import SimpleNamespace
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from integrated_window import IntegratedWindow
from native_footer import NativeFooter
from native_menu import NativeMenu


class NewTabControlsTest(unittest.TestCase):
    def test_new_tab_focus_arrives_with_real_state_not_completion_callback(self):
        work = []
        address = Gtk.Entry()
        window = Gtk.Window()
        window.set_child(address)
        host = SimpleNamespace(phone=False, responsive=SimpleNamespace(reveal_bar=lambda: None), services=SimpleNamespace(create_new_tab=lambda: None),
                               submit=lambda *args: work.append(args), address=address,
                               address_suggestions=SimpleNamespace(changed=lambda: None))
        IntegratedWindow._new_tab(host)
        self.assertEqual(len(work), 1)
        self.assertEqual(len(work[0]), 1, 'Late callback must not erase newly typed text')
        IntegratedWindow._edit_blank_tab(host)
        self.assertEqual(address.get_text(), '')
        self.assertTrue(host.new_tab_pending)
        focus = window.get_focus()
        self.assertTrue(focus is address or focus.is_ancestor(address))
        window.destroy()

    def test_mini_player_menu_reuses_pip_and_rejects_stale_media(self):
        queued, sent, menus = [], [], []
        def button(icon, label, callback):
            widget = Gtk.Button(icon_name=icon)
            widget.connect('clicked', lambda _: callback())
            return widget
        def show_menu(parent, model, x, y, local_activate):
            menus.append(NativeMenu(model, local_activate))
        services = SimpleNamespace(state={'media': {'tabId': 'video-a'}},
                                   send=lambda *args: sent.append(args))
        host = SimpleNamespace(button=button, send=lambda *args: None, services=services,
                               show_menu=show_menu, submit=queued.append)
        footer = NativeFooter(host)
        footer.state = {'media': {'tabId': 'video-a'}}
        gesture = SimpleNamespace(set_state=lambda _: None)
        footer.open_media_menu(gesture, 1, 10, 10)
        self.assertTrue(menus[-1].has_css_class('luma-menu-app'))
        menus[-1]._invoke((0,))
        self.assertEqual(len(queued), 1)
        queued.pop()()
        self.assertEqual(sent, [('sidebar', 'media:pip')])
        footer.open_media_menu(gesture, 1, 10, 10)
        footer.state = {'media': {'tabId': 'video-b'}}
        menus[-1]._invoke((0,))
        self.assertFalse(queued, 'Menu for an old media tab must not act')
        footer.state = {'media': {'tabId': 'video-a'}}
        footer.open_media_menu(gesture, 1, 10, 10)
        menus[-1]._invoke((0,))
        services.state = {'media': {'tabId': 'video-b'}}
        queued.pop()()
        self.assertEqual(len(sent), 1, 'Queued action must recheck the media identity')


if __name__ == '__main__':
    unittest.main()
