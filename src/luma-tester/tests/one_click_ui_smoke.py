# SPDX-License-Identifier: Apache-2.0
"""Exercise fresh native UI Start -> resolve -> fetch -> launch, with no menus."""
import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

if os.environ.get('TRY_UI_SOURCE'):
    import sys
    sys.path.insert(0,os.environ['TRY_UI_SOURCE'])
os.environ['GSETTINGS_BACKEND']='memory'
import gi
gi.require_version('Gtk','4.0')
from gi.repository import GLib,Gtk
from luma_tester import gui,image_library,experiences
from luma_tester.downloads import host_arch


def main():
    calls=[];failures=[];started=time.monotonic();phase=0
    arch=host_arch()
    artifact=dict(kind='os_image',artifact_id='os/nightly/qa/'+arch,arch=arch,
        file='luma.iso',url='https://example.invalid/luma.iso',size_bytes=1,sha256='a'*64)
    with tempfile.TemporaryDirectory(prefix='try-one-click-') as temporary:
        root=Path(temporary)
        catalog=root/'catalog';catalog.mkdir()
        os.environ['LUMA_TESTER_CATALOG']=str(catalog)
        def fetch(item,storage,experience,report,progress=None):
            assert item==artifact and experience=='desktop'
            calls.append('download');report('Downloading Luma…')
            path=root/'image.json'
            path.write_text(json.dumps(dict(schema=1,id='one-click',arch=arch,target='desktop',
                boot='uefi',media='iso',cloud_init=False,user='luma',build='UI fixture',
                image=dict(url=(root/'image.iso').as_uri(),sha256='a'*64,bytes=1))))
            return path
        def inspect():
            nonlocal phase
            app=Gtk.Application.get_default()
            if not app or not app.get_active_window():return True
            try:
                assert time.monotonic()-started<20,'One-click UI timed out'
                if phase==0:
                    assert app.target=='desktop'
                    app.select_intent('try')
                    assert app.select_image() is None
                    assert app.run_button.get_sensitive()
                    def spawned(session,path):
                        try:
                            assert session.manifest['arch']==arch
                            assert app.target=='desktop'
                            assert calls==['download']
                            calls.append('launch')
                        except BaseException as exc:failures.append(str(exc))
                        app.busy=False;app.quit();return False
                    app.spawn=spawned
                    phase=1
                    app.run_button.emit('clicked')
            except BaseException as exc:
                failures.append(str(exc));app.busy=False;app.quit();return False
            return True
        with patch('luma_tester.updates.check',return_value=None), \
             patch.object(image_library,'refresh',return_value=[artifact]), \
             patch.object(image_library,'cached',return_value=[]), \
             patch.object(image_library,'listed',return_value=True), \
             patch.object(image_library,'fetch',side_effect=fetch):
            GLib.timeout_add(50,inspect)
            code=gui.launch(root/'state')
        assert not failures,failures
        assert code==0 and calls==['download','launch'],(code,calls)
        print('Fresh Desktop Start resolves host image, downloads, and launches without menu selection: PASS')


if __name__=='__main__':main()
