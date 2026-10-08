"""Real local payload/GIO receiver and playback action, private stores only."""
import json
from pathlib import Path
import sys
import time
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk
from luma_appkit import ShareSubject, installed_targets
from luma_tide.application import TideApplication
from luma_tide.model import MediaMetadata
from luma_tide.playback import PlaybackState
from luma_tide.ui import TideWindow
from lumaui_runtime import click, count, named, settle, test_application, walk


with test_application('org.projectluma.TideAudioDeliveryTest') as (app, root):
    source = app.store.add_source('Private audio files', 'local-folder', root, local=True)
    paths = []
    for n, suffix in enumerate(('.flac', '.mp3'), 1):
        path = root / f'Payload {n}{suffix}'
        path.write_bytes((f'private original audio bytes {n}').encode())
        paths.append(path)
        app.store.upsert_copy(source.id, MediaMetadata(uri=path.as_uri(), content_digest=str(n) * 64,
            recording_id=f'private-delivery-{n}', title=f'Song {n}', artist='Private artist',
            album='Private album', album_artist='Private artist', track_number=n))
    window = TideWindow(app); app.window = window
    window.set_default_size(1024, 740); window.present()
    settle(lambda: count(window) == 2)
    album = window.library.albums[0]
    window.open_album(album.id)
    settle(lambda: named(window, 'td-album-more') is not None)
    click(window, named(window, 'td-album-more'))
    settle(lambda: any(isinstance(label, Gtk.Label) and label.get_text() == 'Shuffle album'
                       and label.get_mapped() for label in walk(window)))
    shuffle = next(button for button in walk(window) if isinstance(button, Gtk.Button)
                   and any(isinstance(label, Gtk.Label) and label.get_text() == 'Shuffle album'
                           for label in walk(button)))
    shuffle.emit('clicked')
    settle(lambda: app.controller.snapshot.shuffle)
    # A registered, actual GIO file receiver records only its wire arguments.
    # Private XDG roots keep this MIME association off the user's desktop.
    applications = Path(GLib.get_user_data_dir()) / 'applications'; applications.mkdir(parents=True, exist_ok=True)
    marker = root / 'receiver.json'
    recorder = root / 'receiver.py'
    recorder.write_text('import json,sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n')
    desktop = applications / 'org.projectluma.TidePrivateReceiver.desktop'
    desktop.write_text('[Desktop Entry]\nType=Application\nName=Private audio receiver\n'
                       f'Exec=python3 {recorder} {marker} %F\nMimeType=audio/flac;audio/mpeg;\nTerminal=false\n')
    receiver = Gio.DesktopAppInfo.new_from_filename(str(desktop)); assert receiver is not None
    assert receiver.set_as_default_for_type('audio/flac')
    assert receiver.set_as_default_for_type('audio/mpeg')
    settle(lambda: any(target.app_id == 'org.projectluma.TidePrivateReceiver'
                      for target in installed_targets(ShareSubject('Audio', mime_type='audio/flac'))))
    window.share_album(album)
    settle(lambda: hasattr(window, 'share_sheet') and window.share_sheet.get_mapped())
    sheet = window.share_sheet
    assert sheet.choices == ('send-copy',) and not sheet.people
    assert set(sheet.action_buttons) == {'copy', 'save'} and not sheet.show_link
    settle(lambda: any(target.app_id == 'org.projectluma.TidePrivateReceiver' for target in sheet.targets))
    # Actual Copy UI publishes actual local file URIs, never metadata or a
    # fabricated remotely accessible link.
    sheet.action_buttons['copy'].emit('clicked')
    read = []
    def copied(clipboard, result):
        stream, mime = clipboard.read_finish(result)
        read.append((stream.read_bytes(8192, None).get_data().decode(), mime))
        stream.close(None)
    window.get_clipboard().read_async(['text/uri-list'], GLib.PRIORITY_DEFAULT, None, copied)
    settle(lambda: bool(read))
    assert read[0][1] == 'text/uri-list'
    assert read[0][0].splitlines() == [path.as_uri() for path in paths]
    target_button = next(button for button in walk(sheet) if isinstance(button, Gtk.Button)
                         and any(isinstance(label, Gtk.Label) and label.get_text() == 'Private audio receiver'
                                 for label in walk(button)))
    target_button.emit('clicked'); settle(marker.exists)
    assert json.loads(marker.read_text()) == [str(path) for path in paths]
    assert [path.read_bytes() for path in paths] == [f'private original audio bytes {n}'.encode() for n in (1, 2)]
    # The actual stop-and-quit implementation receives the command. Ordinary
    # close keeps the live session; Quit must stop the same controller.
    action = Gio.SimpleAction.new('quit', None)
    action.connect('activate', lambda *_: TideApplication._quit(app))
    app.add_action(action)
    if app.controller.snapshot.state is not PlaybackState.PLAYING:
        window.play_album(album)
    settle(lambda: app.controller.snapshot.state is PlaybackState.PLAYING)
    assert TideApplication._window_closed(app, window)
    assert not window.get_visible() and app.controller.snapshot.state is PlaybackState.PLAYING
    window.present()
    command = next(command for group in window.commands.visible_groups(menu=True)
                   for command in group.commands if command.id == 'tide.quit')
    command.execute()
    assert app.controller.snapshot.state is PlaybackState.STOPPED
    print('PASS actual whole-album file clipboard, registered GIO delivery, original bytes unchanged, ordinary background close and production Quit stop (synthetic audio engine)', flush=True)
