# SPDX-License-Identifier: GPL-3.0-only
"""Native file offers and actions for Chromium-owned download notifications."""
from pathlib import Path
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk


def completed_file(download):
    if not download or download.get('state') != 'done':
        return None
    path = download.get('path')
    if not isinstance(path, str) or not path.startswith('/'):
        return None
    try:
        return Gio.File.new_for_path(path) if Path(path).is_file() else None
    except (OSError, ValueError):
        return None


def file_offer(file, clipboard=False):
    uri = file.get_uri()
    formats = [Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list([file])),
               Gdk.ContentProvider.new_for_bytes('text/uri-list', GLib.Bytes.new((uri+'\r\n').encode()))]
    if clipboard:
        formats.append(Gdk.ContentProvider.new_for_bytes('x-special/gnome-copied-files',
                       GLib.Bytes.new(('copy\n'+uri).encode())))
    return Gdk.ContentProvider.new_union(formats)


class DownloadActions:
    def __init__(self, footer, card):
        self.footer, self.card = footer, card
        drag = Gtk.DragSource(actions=Gdk.DragAction.COPY)
        drag.connect('prepare', self.prepare)
        card.open.add_controller(drag)
        self.drag = drag
        click = Gtk.GestureClick(button=3, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        click.connect('pressed', self.context)
        card.add_controller(click)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', self.key)
        card.open.add_controller(keys)

    def download(self):
        guid = self.card.item.get('downloadId')
        return next((row for row in self.footer.state.get('downloads', [])
                     if guid and row.get('id') == guid), None)

    def prepare(self, *_):
        file = completed_file(self.download())
        return file_offer(file) if file else None

    def key(self, _controller, key, _code, state):
        if key == Gdk.KEY_Menu or (key == Gdk.KEY_F10 and state & Gdk.ModifierType.SHIFT_MASK):
            return self.show()
        return False

    def context(self, gesture, _count, x, y):
        if self.show(x, y):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def show(self, x=None, y=None):
        download = self.download()
        if not download:
            return False
        guid, state = download['id'], download.get('state')
        file = completed_file(download)
        actions = [('Open', 'open', bool(file)), ('Show in Files', 'reveal', bool(file)),
                   ('Copy File', 'copy', bool(file))]
        if state in ('progressing', 'paused'):
            actions.append(('Resume' if state == 'paused' else 'Pause',
                            'resume' if state == 'paused' else 'pause', True))
            actions.append(('Cancel Download', 'cancel', True))
        elif state == 'interrupted' and download.get('canResume'):
            actions.append(('Resume', 'resume', True))
        actions += [('Remove from Download History', 'remove', state not in ('progressing','paused')),
                    ('Dismiss Notification', 'dismiss', True)]
        items = [dict(index=i,kind='item',label=label,visible=True,enabled=enabled)
                 for i,(label,_,enabled) in enumerate(actions)]
        notification = self.card.item['id']
        def activate(_nonce,path):
            if len(path)!=1 or not 0<=path[0]<len(actions):return
            _,command,enabled=actions[path[0]]
            current=self.download()
            if not enabled or not current or current.get('id')!=guid:return
            if command=='copy':
                actual=completed_file(current)
                if actual:self.card.get_display().get_clipboard().set_content(file_offer(actual,clipboard=True))
            elif command=='dismiss':
                self.footer.host.send('notification:dismiss',{'id':notification})
            else:
                # Chromium validates current state and owns opening/revealing,
                # pausing, cancelling and history removal. Remove never deletes
                # the downloaded file from disk.
                self.footer.host.submit(lambda:self.footer.host.services.send(
                    'overlay','download:'+command,{'id':guid}))
        self.footer.host.show_menu(self.card,{'nonce':'download:'+guid,'items':items},
                                  x,y,local_activate=activate)
        return True
