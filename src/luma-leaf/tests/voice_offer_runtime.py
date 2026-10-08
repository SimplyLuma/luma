# SPDX-License-Identifier: Apache-2.0
"""Native offer states and actual installed desktop-manager dispatch."""
import os,sys,tempfile,time
from pathlib import Path
from types import SimpleNamespace
work=tempfile.TemporaryDirectory(prefix='leaf-voice-offer-');root=Path(work.name)
for key in ('XDG_DATA_HOME','XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_STATE_HOME'):
 path=root/key;path.mkdir();os.environ[key]=str(path)
os.environ['XDG_DATA_DIRS']=str(root/'system-data')
applications=Path(os.environ['XDG_DATA_HOME'])/'applications';applications.mkdir()
import gi
gi.require_version('Adw','1');gi.require_version('Gtk','4.0')
from gi.repository import Adw,Gio,GLib,Gtk
from luma_leaf.reader_view import ReaderView
from luma_leaf.store import Library

Adw.init()
app=Adw.Application(application_id='org.projectluma.Leaf.VoiceOfferTest',flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
window=Adw.ApplicationWindow(application=app,default_width=520,default_height=450)
button=Gtk.ToggleButton(label='Listen');window.set_content(button);window.present()

def until(test):
 deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
  if test():return True
  time.sleep(.01)
 raise AssertionError('native offer/manager dispatch timed out')

class Host:
 def __init__(self):
  self.library=Library(root/'library');self.listen_button=button
  self._listen_handler=button.connect('toggled',lambda *_:None)
  self.narrator=SimpleNamespace(state=SimpleNamespace(playing=False));self.started=0;self.errors=[]
 def get_root(self):return window
 def start_listening(self):self.started+=1
 def _voice_problem(self,error):self.errors.append(error)

host=Host()
try:
 manager=Gio.DesktopAppInfo.new('org.projectluma.NaturalVoices.desktop')
except TypeError:
 manager=None
assert manager is None
ReaderView._offer_voice(host)
until(lambda:window.get_visible_dialog() is not None)
dialog=window.get_visible_dialog()
assert dialog.has_response('basic') and not dialog.has_response('manage') and not dialog.has_response('install')
dialog.emit('response','basic');dialog.close()
assert host.started==1 and host.library.pref('listen.basic-voice-ok',False)
# A real private desktop receiver proves the installed-manager API and ID.
marker=root/'manager-invoked'
launcher=root/'manager.py';launcher.write_text('from pathlib import Path\nimport sys\nPath(sys.argv[1]).write_text("opened")\n')
(applications/'org.projectluma.NaturalVoices.desktop').write_text('[Desktop Entry]\nType=Application\nName=Natural Voices\nExec='+sys.executable+' '+str(launcher)+' '+str(marker)+'\n')
def installed_manager():
 try: return Gio.DesktopAppInfo.new('org.projectluma.NaturalVoices.desktop') is not None
 except TypeError: return False
until(installed_manager)
ReaderView._offer_voice(host)
until(lambda:window.get_visible_dialog() is not None)
dialog=window.get_visible_dialog()
assert dialog.has_response('manage') and not dialog.has_response('install')
dialog.emit('response','manage');dialog.close()
until(marker.exists)
assert marker.read_text()=='opened' and not host.errors
window.close();host.library.db.close();app.run_dispose()
print('PASS native absent-manager offer persists Basic Voice; installed Natural Voices desktop manager actually launched; no nonexistent install target',flush=True)
