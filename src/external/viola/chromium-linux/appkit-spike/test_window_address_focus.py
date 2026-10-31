# SPDX-License-Identifier: GPL-3.0-only
"""Initial blank/private window editor focus without late text replacement."""
from types import SimpleNamespace
import unittest
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from integrated_window import IntegratedWindow
from native_windows import NativeWindows


class WindowAddressFocus(unittest.TestCase):
    def test_initial_blank_focus_after_editor_enabled_and_bar_rendered(self):
        entry=Gtk.Entry();field=Gtk.Box();field.append(entry);field.set_sensitive(False)
        window=Gtk.Window();window.set_child(field)
        self.addCleanup(window.destroy)
        rendered=[]
        bookmark=Gtk.Button(child=Gtk.Image())
        host=SimpleNamespace(state=None,open_popover=None,phone=False,mini=False,new_tab_pending=False,
            address=entry,address_field=field,toolbar=Gtk.Box(),back=Gtk.Button(),forward=Gtk.Button(),bookmark=bookmark,
            present_selected_frame=lambda:None,sidebar=SimpleNamespace(render=lambda _:None),
            address_suggestions=SimpleNamespace(owns_focus=lambda:False,refresh_icon=lambda:None,changed=lambda:None))
        host.responsive=SimpleNamespace(reveal_bar=lambda:None,render=lambda:rendered.append(field.get_sensitive()))
        host.address_focused=lambda:window.get_focus() is not None
        def edit():
            self.assertEqual(rendered,[True])
            self.assertTrue(field.get_sensitive())
            IntegratedWindow._edit_blank_tab(host)
        host._edit_blank_tab=edit
        state={'activeTabId':'initial-private-blank','activeUrl':'about:blank','incognito':True}
        IntegratedWindow.render_state(host,state)
        self.assertTrue(host.address_focused())
        self.assertTrue(host.new_tab_pending)
        entry.set_text('typed immediately')
        IntegratedWindow.render_state(host,state)
        self.assertEqual(entry.get_text(),'typed immediately')

    def test_late_window_identity_preserves_existing_address_typing(self):
        # Exercise the actual asynchronous native-window completion callback.
        queued=[];calls=[]
        window=SimpleNamespace(services=SimpleNamespace(state={'activeUrl':'about:blank'}),mini=False,
            address_focused=lambda:True,_focus_address=lambda:calls.append('reset'),
            present=lambda:calls.append('present'),submit=lambda work,done:queued.append((work,done)))
        manager=SimpleNamespace(present=True,detached_tabs={})
        import native_windows
        original=native_windows.NativeIdentityMenu
        native_windows.NativeIdentityMenu=lambda *_:None
        self.addCleanup(setattr,native_windows,'NativeIdentityMenu',original)
        NativeWindows.connected(manager,{'window':window})
        queued[0][1]({'items':[]})
        self.assertEqual(calls,['present'])


if __name__=='__main__':unittest.main()
