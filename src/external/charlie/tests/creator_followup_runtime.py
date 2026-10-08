#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Real Gtk/WebKit mail allocation, rendering and account navigation in private data.

No external account, network sync, auth token or mail sending is involved.
The HTML is owned locally; WebKit's native snapshot proves painted mail pixels.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import sys
from dataclasses import replace
from datetime import datetime, timezone

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('WebKit', '6.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gtk, GLib, Gio, WebKit, GdkPixbuf
from luma_appkit import EmptyState, SidebarRow, Toolbar
from charlie_luma.application import CharlieApplication, AccountWindow, HtmlMessagePreview
from charlie_luma.account_ui import AccountEditorWindow
from charlie_luma.model import Account, Message
from charlie_luma.html_reader import reader

HTML = '<h1>RENDERED EMAIL</h1><p>Private local message content.</p><table style="width:100%;background:#cc3300"><tr><td style="height:120px">RED PANEL</td></tr></table><table><tr><td>Actual HTML body</td></tr></table>'

def pump(seconds=.15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.004)

def wait(predicate, message, seconds=12):
    end = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError(message)
        pump(.04)

def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()

def snapshot(view, name):
    result = []
    def done(web, reply, *_):
        try:
            result.append(web.get_snapshot_finish(reply))
        except GLib.Error as error:
            result.append(error)
    view.get_snapshot(WebKit.SnapshotRegion.VISIBLE, WebKit.SnapshotOptions.NONE, None, done, None)
    wait(lambda: result, 'WebKit native snapshot did not finish')
    assert not isinstance(result[0], GLib.Error), str(result[0])
    texture = result[0]
    destination = Path(os.environ.get('LUMA_CREATOR_REVIEW_CAPTURE', tempfile.gettempdir()))
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / (name + '.png')
    assert texture.save_to_png(str(output)), 'WebKit snapshot not written'
    pixels = GdkPixbuf.Pixbuf.new_from_file(str(output))
    data, channels, stride = pixels.get_pixels(), pixels.get_n_channels(), pixels.get_rowstride()
    red = sum(1 for y in range(pixels.get_height()) for x in range(pixels.get_width())
              if data[y*stride+x*channels] > 150 and data[y*stride+x*channels+1] < 100
              and data[y*stride+x*channels+2] < 80)
    dark = sum(1 for y in range(min(120,pixels.get_height())) for x in range(pixels.get_width())
               if max(data[y*stride+x*channels:y*stride+x*channels+3]) < 90)
    assert red > 5000, f'HTML red panel not painted: {red} pixels'
    assert dark > 100, f'HTML heading/body not painted: {dark} pixels'
    return texture.get_width(), texture.get_height(), red, dark

with tempfile.TemporaryDirectory(prefix='charlie-creator-private-') as directory:
    os.environ.update(XDG_DATA_HOME=directory, XDG_CONFIG_HOME=directory, XDG_CACHE_HOME=directory,
                      LUMA_CHARLIE_FIXTURE='v71', CHARLIE_TEST_WIDTH='1440', CHARLIE_TEST_HEIGHT='900')
    schema = Path(os.environ['LUMA_CREATOR_SCHEMA_FILE'])
    schema_dir = Path(directory) / 'schema'; schema_dir.mkdir()
    (schema_dir/schema.name).write_bytes(schema.read_bytes())
    subprocess.run(['glib-compile-schemas', str(schema_dir)], check=True)
    os.environ.update(GSETTINGS_SCHEMA_DIR=str(schema_dir), GSETTINGS_BACKEND='memory')
    application = CharlieApplication(data_home=Path(directory)); application.did_initial_sync = True
    failures=[]
    def exercise():
        try:
            window = application.window
            wait(lambda: window and window.get_mapped() and window._threads, 'mail window not mapped')
            if '--html-only' not in sys.argv:
                # Footer must stay pinned while destinations scroll or reload.
                add = next(w for w in walk(window.nav) if w.get_name() == 'cr-add-account')
                assert add.get_parent() is window.nav_sidebar.footer, 'Add account is still a scrolling row'
                window._fill_nav(); window._fill_nav()
                assert sum(w.get_name() == 'cr-add-account' for w in walk(window.nav)) == 1
                for index in range(40):
                    window.nav_sidebar.list.append(SidebarRow(f'Private mailbox {index}'))
                pump()
                foot_bounds = add.compute_bounds(window.nav)[1]
                adj = window.nav_sidebar._scroll.get_vadjustment()
                assert adj.get_upper()>adj.get_page_size(), 'fixture did not produce an actual scrolling list'
                adj.set_value(adj.get_upper()-adj.get_page_size());pump()
                assert adj.get_value()>0
                moved = add.compute_bounds(window.nav)[1]
                assert moved.get_y() == foot_bounds.get_y(), 'Add account moved with list scroll'
                assert window.list_head.placement == 'header'
                assert window.list_head.get_style_context().get_border().top == 0, 'search inherited footer rule'
                assert window.list_first.split.get_spacing() == 8, 'mailbox/conversation have no shared gutter'
                list_bounds=window.list_host.compute_bounds(window.list_first.split)[1]
                thread_bounds=window.thread_host.compute_bounds(window.list_first.split)[1]
                assert thread_bounds.get_x()-list_bounds.get_x()-list_bounds.get_width()==8, 'pane gap is not exactly the shared gutter'
                print('PASS pinned account/header/shared pane gap', flush=True)
            account = application.store.accounts()[0]
            base = Message('creator-html',account.id,'inbox',5000,'<creator-local>', 'creator-thread',
                           'Local designed message','Local Sender','sender@local.invalid',(account.address,),
                           datetime.now(timezone.utc),'Private HTML','',HTML)
            for width in (1440,500,360):
                window.set_default_size(width,900)
                wait(lambda: window.get_surface().get_width() == width, f'width {width}')
                for outgoing in (False,True):
                    message = replace(base,outgoing=outgoing)
                    application.store.upsert_message(message)
                    window.refresh()
                    thread = next(t for t in window._all_threads(None,'inbox') if any(m.id == message.id for m in t.messages))
                    window.open_thread(thread.id);pump(.6)
                    previews = [w for w in walk(window) if isinstance(w,HtmlMessagePreview)]
                    assert len(previews)==1
                    preview=previews[0]
                    wait(lambda: not preview.view.is_loading(), 'HTML load did not finish')
                    assert preview.view.get_width() >= 150, f'native HTML collapsed at {width}: {preview.view.get_width()}'
                    assert not preview.view.get_settings().get_enable_javascript(), 'sender JS enabled'
                    assert not preview.view.get_settings().get_enable_html5_local_storage()
                    bubble=window._bubbles[message.id]
                    assert bubble.get_halign()==Gtk.Align.FILL, 'designed paper still shrink wraps'
                    assert preview.view.get_width() <= 640, 'HTML escaped native lane cap'
                    print('PASS painted native HTML', width,outgoing,snapshot(preview.view,f'charlie-html-{width}-{outgoing}'),flush=True)
            if '--html-only' not in sys.argv:
                # Account manager uses shared frame, meaningful empty state and actual enrollment action.
                for saved in application.store.accounts(): application.store.delete_account(saved.id)
                manager=AccountWindow(application);manager.present();pump()
                assert manager.pages.get_visible_child_name()=='empty'
                assert isinstance(manager.empty,EmptyState)
                assert not any(isinstance(w,Toolbar) for w in walk(manager)), 'old inner header remains'
                manager.empty.primary_button.emit('clicked');pump()
                editor=application.account_editor
                assert isinstance(editor,AccountEditorWindow)
                assert editor.stack.get_visible_child_name()=='providers'
                assert editor.back.has_css_class('lumaui-title-island-lead'), 'provider back is legacy GTK frame'
                provider=editor.provider_list.get_row_at_index(0)
                editor._provider_activated(editor.provider_list,provider);pump()
                assert editor.oauth_button.get_label() == 'Google sign-in unavailable'
                assert not editor.oauth_button.get_sensitive()
                assert 'Existing mail and sign-in data are kept.' in editor.oauth_copy.get_text()
                assert editor.manual_panel.get_visible(), 'Manual password setup must remain available'
                microsoft = editor.provider_list.get_row_at_index(1)
                editor._provider_activated(editor.provider_list, microsoft); pump()
                assert editor.oauth_button.get_sensitive(), 'Google policy must not disable Microsoft'
                assert 'Microsoft' in editor.oauth_button.get_label()
                editor._provider_activated(editor.provider_list, provider); pump()
                assert not editor.oauth_button.get_sensitive(), 'Returning to Google re-enabled sign-in'
                assert editor.stack.get_visible_child_name()=='setup'
                editor.back.emit('clicked');pump()
                assert editor.stack.get_visible_child_name()=='providers'
                editor.destroy()
                application.store.upsert_account(account);manager.reload();pump()
                assert manager.pages.get_visible_child_name()=='accounts'
                row=manager.list.get_row_at_index(0)
                assert isinstance(row,SidebarRow) and row.charlie_account.id==account.id
                assert manager.add_account.get_visible()
                manager.destroy()
                print('PASS shared empty/account/provider controls and real transition',flush=True)
        except BaseException as error:
            failures.append(error)
            import traceback;traceback.print_exc()
        application.quit()
        return GLib.SOURCE_REMOVE
    application.connect('activate',lambda *_:GLib.idle_add(exercise))
    application.run([])
    if failures: raise SystemExit(1)
