# SPDX-License-Identifier: Apache-2.0
"""Native GTK readonly/recovery and real bus invalidation, with explicit transport seams."""
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Adw,Gio,GLib
from luma_appkit import install_appkit,install_lumaui
from prairie_apps.connect_sync import ConnectError,DeviceIdentity
from prairie_apps import collaboration_transport as transport
from prairie_apps.collaboration import CollaborationCache
from prairie_apps.notes import _install_notes_style
from prairie_apps.notes_lumaui import NotesLumaWindow

def settle(predicate,label,seconds=5):
 deadline=time.monotonic()+seconds
 while not predicate():
  assert time.monotonic()<deadline,label
  context=GLib.MainContext.default()
  while context.pending():context.iteration(False)
  time.sleep(.005)

identity=DeviceIdentity('owned-native-device','','https://owned.invalid','Owned','2026-10-07')
connection=Gio.bus_get_sync(Gio.BusType.SESSION,None)
owner=Gio.bus_own_name_on_connection(connection,transport.BUS,Gio.BusNameOwnerFlags.NONE,None,None)
def has_owner():
 return connection.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','NameHasOwner',GLib.Variant('(s)',(transport.BUS,)),GLib.VariantType.new('(b)'),Gio.DBusCallFlags.NONE,1000,None).unpack()[0]
settle(has_owner,'real local bus owner')
cache=transport._MetadataCache();fetches=[]
def fresh():fetches.append(True);return identity
with patch.object(transport,'_metadata',cache),patch.object(transport,'_fresh_identity',fresh):
 transport.identity();transport.identity();assert len(fetches)==1
 connection.emit_signal(None,transport.PATH,transport.BUS,'StateChanged',GLib.Variant('(t)',(1,)))
 settle(lambda:cache.value is None,'real StateChanged invalidates metadata')
 transport.identity();assert len(fetches)==2
 Gio.bus_unown_name(owner)
 settle(lambda:cache.value is None,'real NameOwnerChanged invalidates metadata')
print('Actual GIO StateChanged/NameOwnerChanged invalidation PASS (transport seam, no authorization claim)')

with tempfile.TemporaryDirectory(prefix='luma-notes-role-') as temporary:
 env={'HOME':temporary,'XDG_DATA_HOME':temporary+'/data','XDG_CONFIG_HOME':temporary+'/config','XDG_CACHE_HOME':temporary+'/cache','XDG_STATE_HOME':temporary+'/state','GSETTINGS_BACKEND':'memory'}
 with patch.dict(os.environ,env),patch('prairie_apps.connect_sync.load_identity',side_effect=ConnectError('Owned offline transport')):
  app=Adw.Application(application_id='org.projectluma.PrivateNotesRole',flags=Gio.ApplicationFlags.NON_UNIQUE);assert app.register(None)
  install_appkit();install_lumaui();_install_notes_style()
  window=NotesLumaWindow(app);window.present();settle(window.get_mapped,'actual Notes GTK mapping')
  note=window.store.create_note(title='Owned role recovery',body='Personal text retained offline');window._open_note(note)
  window._apply_collaboration_role();assert window.editor.get_editable(),'Unshared personal note must remain editable offline'
  db=CollaborationCache()
  with db.db:db.db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)',('owned-note',identity.hub,identity.device_id,'note',note.id,1,'edit','{}'))
  window._apply_collaboration_role()
  assert not window.editor.get_editable() and not window.title_entry.get_editable() and not window.format_toolbar.get_sensitive(),'Shared note with unavailable current metadata must fail readonly'
  client=SimpleNamespace(address=identity.hub,identity=identity)
  with patch('prairie_apps.connect_sync.load_identity',return_value=identity),patch('prairie_apps.collaboration.CollaborationClient',return_value=client):
   window._apply_collaboration_role();assert window.editor.get_editable(),'Successful current metadata and actual local edit role recovers editing'
   with db.db:db.db.execute("UPDATE documents SET role='comment' WHERE id='owned-note'")
   window._apply_collaboration_role();assert not window.editor.get_editable(),'Successful metadata alone cannot override current comment role'
   with db.db:db.db.execute("UPDATE documents SET role='edit' WHERE id='owned-note'")
   window._apply_collaboration_role();assert window.editor.get_editable()
  with db.db:db.db.execute("UPDATE documents SET role='revoked' WHERE id='owned-note'")
  window._apply_collaboration_role();assert window.editor.get_editable(),'Revoked recoverable local copy remains personal/editable offline'
  assert window.store.get_note(note.id).body==note.body,'Every refusal/recovery preserves original note bytes'
  db.close();window.close();app.quit()
print('Actual native Notes GTK personal-offline/shared-readonly/edit-recovery/comment-role/revoked-recovery PASS')
