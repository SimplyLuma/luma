# SPDX-License-Identifier: Apache-2.0
"""Real LumaUI update prompt/deferral/staging flow; no network or installer run."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

os.environ['GSETTINGS_BACKEND']='memory'
import gi
gi.require_version('Gtk','4.0')
from gi.repository import GLib,Gtk
from luma_tester import gui,updates,image_library


def main():
    payload=b'Update UI fixture, not an executable.'
    artifact=dict(kind='tester_app',artifact_id='tester/0.8.0/linux-x86_64',
        file='tester.AppImage',url='https://example.invalid/tester.AppImage',
        sha256=hashlib.sha256(payload).hexdigest(),size_bytes=len(payload))
    release_check=threading.Event()
    def check():
        assert release_check.wait(10)
        return artifact
    downloads=[];revealed=[];failures=[];phase=0;started=time.monotonic()
    def download(item,path,**kwargs):
        assert kwargs['purpose']=='update' and kwargs['experience']=='other'
        path.write_bytes(payload);downloads.append(path)
    def launcher(file):
        return SimpleNamespace(open_containing_folder=lambda *args:revealed.append(Path(file.get_path())))
    with tempfile.TemporaryDirectory(prefix='tester-update-ui-') as temporary:
        root=Path(temporary)
        (root/'catalog').mkdir()
        os.environ['LUMA_TESTER_CATALOG']=str(root/'catalog')
        marker=root/'sessions/existing/snapshots/keep';marker.parent.mkdir(parents=True)
        marker.write_bytes(b'existing workspace snapshot')
        def inspect():
            nonlocal phase
            app=Gtk.Application.get_default()
            if not app or not app.get_active_window():return True
            try:
                assert time.monotonic()-started<20,phase
                if phase==0:
                    app.busy=True;release_check.set();phase=1
                elif phase==1 and app.pending_update:
                    assert getattr(app,'sheet_handle',None) is None
                    app.busy=False;app.refresh();phase=2
                elif phase==2:
                    assert app.sheet_handle and not app.sheet_handle.closed
                    assert json.loads(app.update_preferences.read_text())['offered_version']=='0.8.0'
                    app.close_sheet();app.check_app_updates();phase=3
                elif phase==3 and not app.update_checking:
                    assert app.pending_update is None and app.sheet_handle is None
                    app.check_app_updates(manual=True);phase=4
                elif phase==4 and not app.update_checking:
                    assert app.sheet_handle and not app.sheet_handle.closed
                    app.download_app_update(artifact);phase=5
                elif phase==5 and revealed:
                    assert downloads==revealed
                    assert revealed[0].read_bytes()==payload
                    assert not revealed[0].stat().st_mode&0o111
                    assert marker.read_bytes()==b'existing workspace snapshot'
                    assert app.stack.get_visible_child_name()=='welcome'
                    assert not app.active()
                    current=dict(artifact,kind='os_image',artifact_id='os/nightly/approved/x86_64',
                        arch='x86_64',file='current.iso',display_name='Approved current ISO')
                    old=dict(current,artifact_id='os/nightly/old/x86_64',display_name='Old straggler')
                    future=dict(current,artifact_id='os/nightly/unapproved/x86_64',display_name='Unapproved future ISO')
                    (root/'approved-images.json').write_text(json.dumps(dict(schema=1,generation=100,
                        images=[dict(artifact_id=current['artifact_id'],sha256=current['sha256'])])))
                    app.library_items=[old,future,current]
                    app.select_target('desktop');app.architectures['desktop']='auto'
                    app.update_build_menu()
                    def labels(widget):
                        result=[widget.get_label()] if isinstance(widget,Gtk.Label) else []
                        child=widget.get_first_child()
                        while child:result+=labels(child);child=child.get_next_sibling()
                        return result
                    words=labels(app.build_menu.get_popover())
                    assert 'Approved current ISO' in words,words
                    assert not {'Old straggler','Unapproved future ISO','Older Builds'}&set(words),words
                    output=Path(os.environ['DEV_ARTIFACT_DIR'])
                    (output/'updates-ui.json').write_text(json.dumps(dict(passed=True,
                        native_lumaui=True,active_session_deferred=True,offered_once=True,
                        manual_recheck=True,download_purpose_update=True,
                        package_revealed_not_executed=True,existing_snapshot_preserved=True,
                        installation_media_curated=True,no_older_builds_menu=True),indent=2)+'\n')
                    app.quit();return False
            except BaseException as error:
                failures.append(error);app.quit();return False
            return True
        with patch.object(updates,'check',side_effect=check), \
             patch.object(updates,'download',side_effect=download), \
             patch.object(image_library,'refresh',return_value=[]), \
             patch.object(Gtk.FileLauncher,'new',side_effect=launcher):
            GLib.timeout_add(100,inspect)
            assert gui.launch(root)==0
        if failures:raise failures[0]
        assert phase==5 and revealed


if __name__=='__main__':main()
