"""Mapped v71 identity and inline badge bounds, preserving default subjects."""
import unittest
from gi.repository import Gtk, GLib
from luma_appkit import ActionCenter, BarAction, BarChip, SPACER, ToastHost, install_appkit, install_lumaui
from luma_appkit.action_center import make_control, set_badge

def settle():
    done=[]
    GLib.timeout_add(180,lambda:done.append(True) and False)
    while not done: GLib.MainContext.default().iteration(True)

def bounds(child,parent):
    ok,rect=child.compute_bounds(parent)
    assert ok
    return rect

class IdentityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init();install_appkit();install_lumaui()

    def test_identity_and_default(self):
        for width in (360,402,500,1024,1180):
            host=ToastHost(Gtk.Box());center=ActionCenter().attach(host)
            window=Gtk.Window(child=host,default_width=width,default_height=740)
            lead=Gtk.Box(width_request=32,height_request=32)
            center.show_bar([BarChip('An unusually long application title',lead=lead,
                meta='Installed with Luma',presentation='identity'),SPACER,
                BarAction('share-2',tooltip='Share'),BarAction('', 'Open',primary=True)],fill=True)
            window.present();settle()
            identity=lead.get_parent();copy=identity.get_last_child()
            title,detail=copy.get_first_child(),copy.get_last_child()
            self.assertTrue(identity.has_css_class('lumaui-bar-identity'))
            self.assertLess(bounds(title,identity).get_y(),bounds(detail,identity).get_y())
            for control in (identity,center.bar_row.get_last_child()):
                rect=bounds(control,window)
                self.assertGreaterEqual(rect.get_x(),0)
                self.assertLessEqual(rect.get_x()+rect.get_width(),width+1)
            window.close();settle()
        plain=make_control(BarChip('Document',meta='PDF'))
        self.assertTrue(plain.has_css_class('lumaui-bar-subject'))
        with self.assertRaises(ValueError):make_control(BarChip('Invalid',presentation='unknown'))

    def test_inline_badge_count_and_activation(self):
        host=ToastHost(Gtk.Box());center=ActionCenter().attach(host)
        window=Gtk.Window(child=host,default_width=360,default_height=740)
        calls=[]
        item=BarAction('star','Discover',dropdown=True,badge=2,badge_tone='red',
                       badge_placement='inline',on_activate=lambda:calls.append(True))
        center.show_bar([item,SPACER,BarAction('search',tooltip='Search')],fill=True)
        window.present();settle()
        button=center.bar_row.get_first_child();badge=button.bar_badge
        self.assertIs(badge.get_parent(),button.get_child())
        for count in (2,123,0,9):
            set_badge(button,count);settle();settle()
            self.assertEqual(badge.get_visible(),bool(count))
            if count:
                rect=bounds(badge,button)
                self.assertGreaterEqual(rect.get_x(),0,(count,badge.get_label(),badge.get_visible(),badge.get_mapped(),button.get_width(),badge.get_width(),button.get_child().get_width()))
                self.assertGreaterEqual(rect.get_y(),0)
                self.assertLessEqual(rect.get_x()+rect.get_width(),button.get_width())
                self.assertLessEqual(rect.get_y()+rect.get_height(),button.get_height())
        button.emit('clicked');self.assertEqual(calls,[True])
        corner=make_control(BarAction('bell',tooltip='Alerts',badge=3))
        self.assertIsInstance(corner,Gtk.Overlay)
        with self.assertRaises(ValueError):BarAction('bell',tooltip='Alerts',badge_placement='inline')
        window.close();settle()

if __name__=='__main__':unittest.main()
