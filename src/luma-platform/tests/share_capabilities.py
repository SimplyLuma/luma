# SPDX-License-Identifier: Apache-2.0
"""Native MIME receivers, actual delivered bytes and truthful action feedback."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

# GIO reads these paths once. Keep every registration private to this probe.
temporary = tempfile.TemporaryDirectory(prefix='luma-share-receiver-')
root = Path(temporary.name)
os.environ['XDG_DATA_HOME'] = str(root / 'data')
os.environ['XDG_DATA_DIRS'] = str(root / 'other')
apps = root / 'data/applications'; apps.mkdir(parents=True)
receipt = root / 'received'
receiver = root / 'receiver.py'
receiver.write_text('import hashlib,pathlib,sys\np=pathlib.Path(sys.argv[1]); pathlib.Path(sys.argv[2]).write_text(hashlib.sha256(p.read_bytes()).hexdigest())\n')
# File placeholder precedes the receipt argument in the Python receiver.
for name, mime, field in [('Receiver', 'application/x-luma-share-proof', '%f'),
                          ('Launcher', 'application/x-luma-share-proof', ''),
                          ('WrongType', 'image/png', '%f')]:
    (apps / f'org.projectluma.{name}.desktop').write_text(
        '[Desktop Entry]\nType=Application\nName=' + name + '\n'
        f'Exec={sys.executable} {receiver} {field} {receipt}\nMimeType={mime};\n')
subprocess.run(['update-desktop-database', str(apps)], check=True)
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, Gio, GLib, Gtk
from luma_appkit import Person, ShareSheet, ShareSubject, install_appkit, install_lumaui
from luma_appkit.bar_share import Collaborator, share_actions
assert Gtk.init_check() and Gdk.Display.get_default(), 'actual GTK display required'
install_appkit(); install_lumaui()
subject = ShareSubject('A private test document')
subject.mime_type = 'application/x-luma-share-proof'
subject.share_link_available = False
# Existing API runs the same default-target behavior on the shipped baseline.
default_sheet = ShareSheet(subject)
assert [(t.key, t.label) for t in default_sheet.targets] == [('org.projectluma.Receiver', 'Receiver')], default_sheet.targets
from luma_appkit import ShareResult, installed_targets
targets = installed_targets(subject)
assert [(t.key, t.label) for t in targets] == [('org.projectluma.Receiver', 'Receiver')], targets
assert installed_targets(ShareSubject('Unsupported')) == []
assert not any(t.key == 'nearby' for t in targets)
file = root / 'copy.luma'; file.write_bytes(b'Original bytes\x00\xff\nwith content')
app = Gio.DesktopAppInfo.new(targets[0].app_id + '.desktop')
assert app.launch([Gio.File.new_for_path(str(file))], None)
end = time.monotonic() + 5
while not receipt.exists() and time.monotonic() < end:
    while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
    time.sleep(.01)
assert receipt.read_text() == hashlib.sha256(file.read_bytes()).hexdigest(), 'receiver must read original bytes'

def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from walk(child)
        child = child.get_next_sibling()

def text(widget):
    return [w.get_label() for w in walk(widget) if isinstance(w, Gtk.Label)]

person = Person('A friend', username='friend')
for outcome in (ShareResult(False, 'Unavailable'), None, ShareResult(True, 'Sent')):
    sheet = ShareSheet(subject, people=[person], targets=[], on_choice=lambda *_a, o=outcome: o)
    tile = sheet._person_tile(person); tile.emit('clicked')
    assert ('A friend' in sheet.sent) == (isinstance(outcome, ShareResult) and outcome.completed)
    copy = sheet.action_buttons['copy']; copy.emit('clicked')
    assert ('Copied' in text(copy)) == (isinstance(outcome, ShareResult) and outcome.completed)
assert not any(choice == 'copy-link' for choice, *_ in share_actions(ShareSubject('Local event', kind='link')))
assert not any(w.has_css_class('lumaui-share-link') for w in walk(ShareSheet(subject, targets=[])))

collaborator = Collaborator(person, 'view')
sheet = ShareSheet(ShareSubject('Shared document', kind='doc'), choices=('work-together',),
                   collaborators=[collaborator], suggest=lambda _q: [person],
                   on_choice=lambda *_: ShareResult(False, 'Server rejected the action'))
sheet.query = 'friend'; sheet._invite(person)
assert sheet.collaborators == [collaborator] and sheet.query == 'friend', 'rejected invite must not add an editor'
print('PASS native MIME filtering, real receiver bytes, refusal feedback, truthful copy/send and rejected invite')

# Directory replies arrive on GTK's main context after network completion.
# Only current queries in a live invitation mode may change the native rows.
sheet = ShareSheet(ShareSubject('Async document', kind='doc'), targets=[],
                   choices=('work-together', 'send-copy'), suggest=lambda _q: [])
sheet.invite.set_text(' alice ')
alice = Person('Alice', username='alice')
assert sheet.set_suggestions('alice', [alice], 'Ready')
assert sheet._matches() == [alice]
invite = sheet.invite
sheet._receivers_ready([], None)
sheet.set_targets(targets)
assert sheet.invite is invite and sheet.invite.get_text() == ' alice ', 'receiver discovery must preserve the live invitation entry'
sheet.invite.set_text('bob')
assert not sheet.set_suggestions('alice', [alice]), 'stale reply must be refused'
assert sheet._matches() == [], 'previous query must not leak into current rows'
assert sheet.set_suggestions('bob', [], 'Directory unavailable')
assert sheet.none_label.get_label() == 'Directory unavailable'
sheet._mode_changed('send-copy')
assert not sheet.set_suggestions('bob', [alice]), 'copy mode cannot receive an invite'
sheet._mode_changed('work-together')
sheet.invite.set_text('alice')
sheet.close()
assert not sheet.set_suggestions('alice', [alice]), 'dismissed sheets refuse replies'
print('PASS async ShareSheet current/stale/failure/mode/dismissal guards')
recipient = ShareSheet(ShareSubject('Recipient document', kind='doc'), targets=[],
                      choices=('work-together',), collaborators=[collaborator], manage_access=False)
assert not recipient.invite.get_sensitive()
assert not recipient.set_suggestions('', [alice])
assert all(not w.get_sensitive() for w in walk(recipient) if isinstance(w, Gtk.Button) and w.has_css_class('lumaui-share-role'))
assert not any(w.has_css_class('lumaui-share-link') for w in walk(recipient)), 'unavailable links must be omitted in collaboration mode'
print('PASS recipient permissions and truthful collaboration link availability')
recipient = ShareSheet(ShareSubject('Owned elsewhere', kind='doc'), targets=[], choices=('work-together',),
                      owner=Person('Actual document owner', username='owner'), owner_is_you=False, manage_access=False)
assert 'Actual document owner' in text(recipient) and 'You' not in text(recipient)
print('PASS recipient sees the actual owner name')
