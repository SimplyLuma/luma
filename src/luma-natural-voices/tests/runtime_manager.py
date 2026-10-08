# SPDX-License-Identifier: Apache-2.0
"""Actual ordinary-user GTK manager lifecycle using the native speech service."""
import os,subprocess,tempfile,threading,time
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='luma-natural-voice-ui-') as folder:
 root=Path(folder)
 for name in ('XDG_RUNTIME_DIR','XDG_DATA_HOME','XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_STATE_HOME'):
  p=root/name;p.mkdir(mode=0o700);os.environ[name]=str(p)
 socket=root/'speechd.sock';os.environ['SPEECHD_ADDRESS']='unix_socket:'+str(socket)
 log=(root/'speechd.log').open('w')
 server=subprocess.Popen(['speech-dispatcher','-s','-t','0','-c','unix_socket','-S',str(socket)],stdout=log,stderr=subprocess.STDOUT)
 try:
  import gi
  gi.require_version('Adw','1');gi.require_version('Gtk','4.0')
  from gi.repository import Adw,GLib
  from luma_natural_voices.application import VoiceApplication,VoiceWindow
  Adw.init();app=VoiceApplication();assert app.register(None)
  def until(test):
   deadline=time.monotonic()+8
   while time.monotonic()<deadline:
    while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
    if test():return
    time.sleep(.01)
   raise AssertionError('native GTK voice manager lifecycle timed out')
  until(socket.exists)
  for width in (360,640,1024):
   window=VoiceWindow(app);window.set_default_size(width,500);window.present()
   until(lambda:window.get_width()>0 and not window.busy)
   assert not window.closed
   window.close()
   until(lambda:not any(t.name.startswith('voice-discovery') and t.is_alive() for t in threading.enumerate()))
   assert window.closed and window.client is None
   print('PASS actual native Natural Voices manager startup/close',width,flush=True)
 finally:
  if server.poll() is None:server.terminate()
  try:server.wait(timeout=3)
  except subprocess.TimeoutExpired:server.kill();server.wait()
  log.close()
