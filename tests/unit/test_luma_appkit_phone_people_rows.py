"""Physical phone portrait rows are64px without changing other lists."""
import unittest
from test_luma_appkit_list_status import settle
from gi.repository import Gtk
from luma_appkit import ListFirst, NavigationSidebar, NavigationRow, PersonAvatar, AccountCard, SidebarRow, RowLead, install_appkit, install_lumaui
class PeopleRows(unittest.TestCase):
    def test_phone_portrait_scope(self):
        Gtk.init(); install_appkit(); install_lumaui()
        for phone in (False, True):
            sidebar=NavigationSidebar()
            account=AccountCard("Ada",caption="My card",recessed=True)
            sidebar.append_header(account)
            sidebar.append_section("A")
            section=sidebar.list.get_first_child()
            person=NavigationRow('Ada',subtitle='@ada',icon_widget=PersonAvatar('Ada',34))
            ordinary=NavigationRow('Files',subtitle='Documents',icon_name='folder-symbolic')
            conversation=SidebarRow("Team",subtitle="Hello",lead=RowLead.group(["Ada", "Bob"]))
            sidebar.append_row(person);sidebar.append_row(ordinary);sidebar.append_row(conversation)
            page=ListFirst(sidebar,Gtk.Box(),title='People')
            window=Gtk.Window(child=page,default_width=402,default_height=874)
            window.add_css_class('luma-app-window')
            if phone:window.add_css_class('lumaui-phone-device')
            window.present();settle();page.show_list();settle()
            self.assertEqual(person.get_allocated_height(),64 if phone else 56)
            self.assertLess(ordinary.get_allocated_height(),64)
            self.assertEqual(section.get_style_context().get_margin().top,12)
            if phone:
                self.assertEqual(conversation.get_allocated_height(),64)
                content=account.get_child()
                self.assertEqual(content.get_allocated_height(),64)
            window.close()
if __name__=='__main__':unittest.main()
