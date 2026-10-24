# SPDX-License-Identifier: GPL-3.0-only
"""Real GTK editor transactions across desktop and phone bar layouts."""
import unittest
from types import SimpleNamespace
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from luma_appkit import ActionCenter,BarSearch,ToastHost,install_appkit
from native_address_focus import AddressEdit
from responsive_chrome import ResponsiveChrome


def settle():
    loop=GLib.MainLoop();GLib.timeout_add(80,lambda:loop.quit() or False);loop.run()


class AddressBreakpointFocus(unittest.TestCase):
    def test_compact_reparent_preserves_text_reverse_selection_and_pending(self):
        entry=Gtk.Entry();first=Gtk.Box();second=Gtk.Box();first.append(entry)
        body=Gtk.Box();body.append(first);body.append(second)
        page=Gtk.Button(label='Page');body.append(page)
        window=Gtk.Window(child=body,default_width=800,default_height=400)
        self.addCleanup(window.destroy);window.present();settle()
        host=SimpleNamespace(mini=False,phone=False,phone_device=False,new_tab_pending=False,
            address=entry,page=page,get_focus=window.get_focus,get_width=lambda:800,
            address_focused=lambda:window.get_focus() is not None and window.get_focus().is_ancestor(entry),
            address_suggestions=SimpleNamespace(close=lambda:None),toolbar=Gtk.Box(),toggle=Gtk.Button(),
            set_phone_bleed=lambda _:None,sidebar_layout=SimpleNamespace(hide_peek=lambda:None,
                fixed=Gtk.Box(),handle=Gtk.Box(),edge=Gtk.Box(),collapsed=False))
        focus=Gtk.EventControllerFocus()
        def entered(*_):
            if not host.new_tab_pending:entry.set_text('https://active-page.invalid/')
            entry.select_region(0,-1)
        focus.connect('enter',entered);entry.add_controller(focus)
        entry.grab_focus();entry.set_text('unfinished typed query');entry.select_region(14,3)
        before=AddressEdit.capture(entry,host.new_tab_pending)
        chrome=ResponsiveChrome.__new__(ResponsiveChrome);chrome.host=host
        chrome.phone=False;chrome.compact=False;chrome.query_generation=0
        chrome.center=SimpleNamespace(searching=False,close_search=lambda:None,fold=lambda:None)
        chrome.reveal_bar=lambda:None
        chrome.render=lambda **_: (first.remove(entry),second.append(entry))
        ResponsiveChrome.allocated(chrome)
        self.assertEqual(entry.get_text(),before.text)
        self.assertEqual(entry.get_position(),before.position)
        self.assertEqual(tuple(entry.get_selection_bounds()),before.selection)
        self.assertEqual(host.new_tab_pending,before.new_tab_pending)
        self.assertTrue(host.address_focused())

    def test_initial_allocation_does_not_take_focus_from_startup(self):
        # No existing focus is not an instruction to focus a blank webpage.
        entry=Gtk.Entry();window=Gtk.Window(child=entry)
        self.addCleanup(window.destroy)
        focused=[]
        host=SimpleNamespace(mini=False,phone=False,phone_device=False,new_tab_pending=False,
            address=entry,get_focus=lambda:None,get_width=lambda:800,address_focused=lambda:False,
            page=SimpleNamespace(grab_focus=lambda:focused.append('page')),
            address_suggestions=SimpleNamespace(close=lambda:None),toolbar=Gtk.Box(),toggle=Gtk.Button(),
            set_phone_bleed=lambda _:None,sidebar_layout=SimpleNamespace(hide_peek=lambda:None,
                fixed=Gtk.Box(),handle=Gtk.Box(),edge=Gtk.Box(),collapsed=False))
        chrome=ResponsiveChrome.__new__(ResponsiveChrome);chrome.host=host
        chrome.phone=False;chrome.compact=False;chrome.query_generation=0
        chrome.center=SimpleNamespace(searching=False,close_search=lambda:None,fold=lambda:None)
        chrome.reveal_bar=lambda:None;chrome.render=lambda **_:None
        ResponsiveChrome.allocated(chrome)
        self.assertEqual(focused,[])

    def test_phone_live_editor_restores_text_cursor_and_pending_after_focus_idle(self):
        install_appkit()
        region=ToastHost(Gtk.Box());window=Gtk.Window(child=region,default_width=420,default_height=600)
        self.addCleanup(window.destroy)
        center=ActionCenter().attach(region);search=BarSearch('Address',text='https://old.invalid',opens=True)
        center.show_bar([search]);window.present();settle()
        host=SimpleNamespace(get_focus=window.get_focus,new_tab_pending=False)
        chrome=ResponsiveChrome.__new__(ResponsiveChrome);chrome.host=host
        chrome.phone=True;chrome.center=center;chrome.search=search
        edit=AddressEdit('in-progress query',5,(),True)
        chrome.restore_address_edit(edit);settle()
        field=window.get_focus()
        self.assertIsInstance(field,Gtk.Editable)
        self.assertEqual(field.get_text(),edit.text)
        self.assertEqual(field.get_position(),5)
        self.assertEqual(tuple(field.get_selection_bounds()),())
        self.assertTrue(host.new_tab_pending)

    def test_phone_restore_does_not_overwrite_or_refocus_another_editor(self):
        install_appkit()
        other=Gtk.Entry(text='sidebar rename in progress')
        region=ToastHost(other);window=Gtk.Window(child=region,default_width=420,default_height=600)
        self.addCleanup(window.destroy)
        center=ActionCenter().attach(region);search=BarSearch('Address',text='page',opens=True)
        center.show_bar([search]);window.present();settle()
        host=SimpleNamespace(get_focus=window.get_focus,new_tab_pending=False)
        chrome=ResponsiveChrome.__new__(ResponsiveChrome);chrome.host=host
        chrome.phone=True;chrome.center=center;chrome.search=search
        original=center.open_search
        def open_then_focus_elsewhere(item):
            original(item)
            GLib.idle_add(lambda:other.grab_focus() and False)
        center.open_search=open_then_focus_elsewhere
        chrome.restore_address_edit(AddressEdit('address being restored',3,(),True));settle()
        self.assertEqual(other.get_text(),'sidebar rename in progress')
        self.assertTrue(window.get_focus().is_ancestor(other))
        self.assertFalse(host.new_tab_pending)
        self.assertIsNone(chrome.capture_address_edit())


if __name__=='__main__':unittest.main()
