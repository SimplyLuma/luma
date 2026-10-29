# SPDX-License-Identifier: Apache-2.0
"""Exercise separate picker actions, cancellation and confirmed deletion in GTK."""
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from unittest.mock import patch

if os.environ.get('TRY_UI_SOURCE'):
    sys.path.insert(0,os.environ['TRY_UI_SOURCE'])
os.environ['GSETTINGS_BACKEND']='memory'
import gi
gi.require_version('Gtk','4.0')
from gi.repository import GLib, Gtk
from luma_tester import gui, image_library, image_storage, vm
from luma_tester.core import Session
from luma_tester.downloads import host_arch


def descendants(widget):
    yield widget
    child=widget.get_first_child()
    while child:
        yield from descendants(child)
        child=child.get_next_sibling()


def screenshot(window,name):
    if not os.environ.get('DEV_ARTIFACT_DIR'):return
    subprocess.run(['import','-window','root',str(Path(os.environ['DEV_ARTIFACT_DIR'])/name)],check=True,timeout=10)


def main():
    failures=[];phase=0;app=None
    with tempfile.TemporaryDirectory(prefix='try-delete-ui-') as name:
        root=Path(name);state=root/'state';catalog=root/'catalog';catalog.mkdir()
        os.environ['LUMA_TESTER_CATALOG']=str(catalog)
        image=dict(sha256='a'*64,media='disk')
        base=image_storage.cache_path(state,image);base.parent.mkdir(parents=True);base.write_bytes(b'UI fixture')
        manifest=dict(schema=1,id='delete-ui',arch=host_arch(),target='desktop',boot='uefi',cloud_init=True,
            user='luma',build='Luma prepared preview',image=dict(url=base.as_uri(),sha256='a'*64,bytes=base.stat().st_size))
        registered=state/'imported-images/test.json';vm.atomic_json(registered,manifest)
        session=Session(registered,state);session.directory.mkdir(parents=True)
        vm.atomic_json(session.directory/'manifest.json',manifest);session.disk.write_bytes(b'saved files')
        def inspect():
            nonlocal phase,app
            app=Gtk.Application.get_default()
            if not app or not app.get_active_window():return True
            try:
                if phase==0:
                    app.window.set_default_size(1200,900);app.select_intent('try');app.select_target('desktop')
                    app.stack.set_visible_child_name('session');app.update_images();app.build_menu.popup();phase=1
                    return True
                if phase==1:
                    menu=app.build_menu.get_popover();assert isinstance(menu,gui.ImageMenu)
                    buttons=[w for w in descendants(menu) if isinstance(w,Gtk.Button) and w.get_tooltip_text()=='Delete downloaded image…']
                    assert len(buttons)==1,len(buttons)
                    screenshot(app.window,'image-picker-delete.png')
                    menu._shown_at=0 # The menu has been open; test does not depend on wall-clock scheduling.
                    buttons[0].emit('clicked');phase=2;return True
                if phase==2:
                    assert base.exists() and session.disk.exists(),'Opening confirmation deleted data'
                    labels=[w.get_text() for w in descendants(app.window) if isinstance(w,Gtk.Label)]
                    assert 'Delete image?' in labels and any('saved workspace' in x for x in labels)
                    screenshot(app.window,'image-delete-confirmation.png')
                    cancel=next(w for w in descendants(app.window) if isinstance(w,Gtk.Button) and getattr(w,'label_widget',None) is not None and w.label_widget.get_text()=='Cancel')
                    cancel.emit('clicked');assert base.exists() and session.disk.exists()
                    app.confirm_delete_image(image,'Luma prepared preview');phase=3;return True
                if phase==3:
                    delete=next(w for w in descendants(app.window) if isinstance(w,Gtk.Button) and getattr(w,'label_widget',None) is not None and w.label_widget.get_text()=='Delete Image')
                    delete.emit('clicked');phase=4;return True
                if app.busy:return True
                assert not base.exists() and not session.directory.exists() and not registered.exists()
                assert app.session is None or app.session.directory.exists()
                assert not app.select_image(),'Deleted image selection was retained'
                app.build_menu.popup();phase=5
                assert not any(isinstance(w,Gtk.Button) and w.get_tooltip_text()=='Delete downloaded image…' for w in descendants(app.build_menu.get_popover()))
                app.quit();return False
            except BaseException as exc:
                failures.append(exc);app.quit();return False
        def expired():
            failures.append(AssertionError('Image deletion UI check timed out'))
            if app:app.quit()
            return False
        with patch('luma_tester.updates.check',return_value=None),patch.object(image_library,'refresh',return_value=[]),patch.object(image_library,'cached',return_value=[]):
            GLib.timeout_add(350,inspect);GLib.timeout_add_seconds(30,expired)
            result=gui.launch(state)
        if failures:raise failures[0]
        assert result==0
        print('Image picker separate trash action, confirmation, cancel, deletion and selection cleanup: PASS')


if __name__=='__main__':main()
