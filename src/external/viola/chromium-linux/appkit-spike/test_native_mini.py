# SPDX-License-Identifier: GPL-3.0-only
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk
from native_mini import NativeMini, MiniIdle
from integrated_window import IntegratedWindow
from engine_pipe import EnginePipe


class MiniPresentationTest(unittest.TestCase):
    def test_compact_composition_has_no_sidebar_reveal_and_keeps_native_frame(self):
        host=SimpleNamespace(connect=lambda *args:None,add_css_class=lambda _:None,title_bar=SimpleNamespace(pack_end=lambda w:None),
            button=lambda *args:Gtk.Button(),sidebar=Gtk.Box(),sidebar_bin=Gtk.Box(),
            toggle=Gtk.Button(),bookmark=Gtk.Button(),new_tab=Gtk.Button(),menu_button=Gtk.Button(),
            sidebar_layout=SimpleNamespace(handle=Gtk.Box(),edge=Gtk.Box(),reveal=Gtk.Box()),
            address=Gtk.Entry(),island=Gtk.Box(orientation=Gtk.Orientation.VERTICAL),toolbar=Gtk.Box())
        host.responsive=SimpleNamespace(expand=Gtk.Button(label='Open in Viola'))
        host.toolbar.set_visible(False)
        host.island.append(host.toolbar)
        compact=NativeMini(host)
        self.addCleanup(compact.idle.close)
        self.assertFalse(any(widget.get_visible() for widget in (host.sidebar,host.sidebar_bin,host.toggle,host.bookmark,host.new_tab,host.sidebar_layout.edge)))
        self.assertFalse(host.toolbar.get_visible())
        self.assertTrue(host.menu_button.get_visible())
        self.assertIs(compact.expand,host.responsive.expand)
        self.assertFalse(compact.permission.get_visible())

    def test_mini_shortcuts_cannot_create_hidden_tabs_or_reveal_sidebar(self):
        calls=[]
        host=SimpleNamespace(mini=True,phone=False,_new_window=lambda:calls.append('new-window'),close=lambda:calls.append('close-mini'))
        IntegratedWindow._new_tab(host)
        IntegratedWindow._close_tab(host)
        IntegratedWindow._toggle_sidebar(host)
        self.assertEqual(calls,['new-window','close-mini'])

    def test_external_dispatch_only_forwards_existing_supported_url(self):
        host=SimpleNamespace(executable='/engine',profile='/profile',display=None,log=None,process=SimpleNamespace(poll=lambda:None))
        with patch('engine_pipe.subprocess.run',return_value=SimpleNamespace(returncode=0)) as run:
            self.assertFalse(EnginePipe.open_external(host,'javascript:alert(1)'))
            self.assertFalse(EnginePipe.open_external(host,'--no-sandbox'))
            run.assert_not_called()
            self.assertTrue(EnginePipe.open_external(host,'https://example.test/auth?state=fixture'))
            self.assertEqual(run.call_args.args[0],['/engine','--user-data-dir=/profile','--no-first-run','--','https://example.test/auth?state=fixture'])


class MiniRetentionTest(unittest.TestCase):
    def test_retention_never_closes_active_flow_and_respects_never(self):
        calls=[]
        window=SimpleNamespace(connect=lambda *args:None,is_active=lambda:False,get_visible_dialog=lambda:None,close=lambda:calls.append('closed'))
        idle=MiniIdle(window)
        self.addCleanup(idle.close)
        idle.update({'activeUrl':'https://example.test','settings':{'miniViolaAutoCloseHours':0}})
        idle.last_activity=0
        self.assertTrue(idle.tick());self.assertEqual(calls,[])
        idle.hours=1
        window.is_active=lambda:True
        self.assertTrue(idle.tick());self.assertEqual(calls,[])
        window.is_active=lambda:False
        window.get_visible_dialog=lambda:object()
        idle.last_activity=0
        self.assertTrue(idle.tick());self.assertEqual(calls,[])
        window.get_visible_dialog=lambda:None
        idle.last_activity=0
        idle.close()  # Remove the real timer before invoking its expiry branch.
        self.assertFalse(idle.tick());self.assertEqual(calls,['closed'])
