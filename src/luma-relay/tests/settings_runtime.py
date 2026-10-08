#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
import os,tempfile,time
from unittest.mock import patch
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Adw,Gio,GLib,Gtk
from luma_appkit import install_appkit,EmptyState
from luma_relay.settings import RelayWindow

def settle(test):
    until=time.monotonic()+10
    while time.monotonic()<until:
        while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
        if test():return
        time.sleep(.01)
    raise AssertionError('Relay did not settle')

with tempfile.TemporaryDirectory() as home:
    os.environ['XDG_DATA_HOME']=home+'/data';os.environ['XDG_CONFIG_HOME']=home+'/config'
    app=Adw.Application(application_id='org.projectluma.RelayTest',flags=Gio.ApplicationFlags.NON_UNIQUE);assert app.register(None);install_appkit()
    facts={'doctor':{'wine_available':False,'sandbox_available':True,'wine':'Not installed'},'windows':[], 'android':[], 'android_status':{},'android_version':'Not installed','versions':{'bwrap':'test','winetricks':'Not installed','waydroid':'Not installed'}}
    with patch.object(RelayWindow,'reload',lambda self:self._loaded(facts)):
        window=RelayWindow(app)
        for scheme in [Adw.ColorScheme.FORCE_LIGHT,Adw.ColorScheme.FORCE_DARK]:
            Adw.StyleManager.get_default().set_color_scheme(scheme)
            for width in [360,500,1024]:
                window.set_default_size(width,650);window.present();settle(lambda:window.get_allocated_width()==width)
                window.show_pane('windows');assert isinstance(window.detail_body.get_child(),EmptyState)
                assert window.detail_body.get_child().primary_button.get_label()=='Set up Windows applications'
                window.show_pane('android');assert isinstance(window.detail_body.get_child(),EmptyState)
                window.show_pane('about');assert window.heading.get_text()=='About Relay'
        window.close()
print('PASS: Relay missing runtimes, About, 360/500/1024, light/dark')
