#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Native Messages tiles retain contact dispatch and one shared pane gutter."""
import json, os, tempfile, time
from pathlib import Path
from unittest.mock import patch
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Adw,Gio,GLib,Gtk
from luma_appkit import install_appkit,install_lumaui
from prairie_apps.messages import install_messages_theme
from prairie_apps.messages_port import FixtureMessagesWindow


def settle():
    end=time.monotonic()+.35
    while time.monotonic()<end:
        while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
        time.sleep(.005)


def children(widget):
    child=widget.get_first_child()
    while child:
        yield child
        child=child.get_next_sibling()


def main():
    app=Adw.Application(application_id='org.projectluma.Messages.DetailReview',flags=Gio.ApplicationFlags.NON_UNIQUE)
    assert app.register(None);install_appkit();install_lumaui();install_messages_theme()
    with tempfile.TemporaryDirectory(prefix='messages-details-') as directory:
        fixture=Path(directory)/'fixture.json'
        message=lambda i:{'id':i,'from':'alice','text':'Private fixture','at':'9:00 AM'}
        fixture.write_text(json.dumps({'selected':'group','people':{'alice':{'name':'Alice Example','handle':'alice'},'bob':{'name':'Bob Example','handle':'bob'}},'conversations':[
            {'id':'group','address':'+15105550101','name':'Group','group':['alice','bob'],'messages':[message(1)]},
            {'id':'alice','address':'+15105550102','person':'alice','messages':[message(2)]}]}))
        with patch.dict(os.environ,{'LUMA_MESSAGES_FIXTURE':str(fixture)}):
            for scheme in (Adw.ColorScheme.FORCE_LIGHT,Adw.ColorScheme.FORCE_DARK):
                Adw.StyleManager.get_default().set_color_scheme(scheme)
                for width,height in ((1180,740),(800,540),(400,700)):
                    window=FixtureMessagesWindow(app);window.set_default_size(width,height);window.present();settle()
                    view=window.surface
                    if width < 560:
                        view._phone_open_current();settle()
                    view.details.open(view.current['id']);settle()
                    assert view.details.sheet.get_mapped()
                    _,sheet=view.details.sheet.compute_bounds(window)
                    if width < 560:
                        assert view.details.is_drawer, 'Narrow details must use the shared drawer'
                        assert sheet.get_x() >= 0 and sheet.get_x()+sheet.get_width() <= window.get_width()+1
                        gap='drawer'
                    else:
                        assert view.details.sheet.get_parent() is view.details
                        _,island=view.island.compute_bounds(window)
                        gap=sheet.get_x()-island.get_x()-island.get_width()
                        assert abs(gap-8)<=1,f'Double or missing shared gutter: {gap}'
                    assert view.content.get_spacing()==0
                    group_tiles=next(x for x in children(view.details.body) if x.has_css_class('tile'))
                    assert len(tuple(children(group_tiles)))==2
                    assert all(button.get_height()==64 for button in children(group_tiles))
                    with patch.object(view,'_contact_action',return_value=True) as dispatch:
                        view.open('alice');view.details.open('alice');settle()
                        tiles=next(x for x in children(view.details.body) if x.has_css_class('tile'))
                        buttons=tuple(children(tiles));assert len(buttons)==4
                        assert all(button.get_height()==64 for button in buttons)
                        assert all(button.get_sensitive() for button in buttons[:3]) and not buttons[3].get_sensitive()
                        buttons[1].emit('clicked');assert dispatch.call_args.args[0]=='call'
                    view.details.close();settle();assert view.content.get_spacing()==0
                    print(f'PASS native Messages scheme={scheme.value_nick} width={window.get_width()} gap={gap} tiles=64, real contact dispatch')
                    window.close();settle()
    app.quit()


if __name__=='__main__':main()
