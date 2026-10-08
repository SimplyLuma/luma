#!/usr/bin/env python3
"""Installed-package native ARM Dash test; private user settings/session bus only."""
from pathlib import Path
import argparse,os,subprocess,resource,signal,shutil,json,platform
p=argparse.ArgumentParser();p.add_argument('--fixture',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--scale',type=int,choices=[1,2],default=1);p.add_argument('--rtl',action='store_true');a=p.parse_args()
assert os.geteuid()!=0 and platform.machine()=='aarch64'
out=a.output.resolve();out.mkdir(mode=0o700)
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
env=os.environ.copy()
for k in ['DBUS_SESSION_BUS_ADDRESS','DISPLAY','WAYLAND_DISPLAY','LD_LIBRARY_PATH','GI_TYPELIB_PATH','GNOME_SHELL_BUILDDIR','GNOME_SHELL_DATADIR','GSETTINGS_SCHEMA_DIR','G_RESOURCE_OVERLAYS','DCONF_PROFILE','GTK_A11Y','NO_AT_BRIDGE']:
 env.pop(k,None)
for k,n in [('HOME','home'),('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data'),('XDG_CACHE_HOME','cache'),('XDG_RUNTIME_DIR','runtime')]:
 d=out/n;d.mkdir(mode=0o700);env[k]=str(d)
env['LUMA_DASH_DIRECT']='1'
env['LUMA_CONNECT_PRIVATE_SMOKE']='1'
env['LUMA_CONNECT_SMOKE_OUTPUT']=str(out)
env['LUMA_CONNECT_UI_FIXTURE']=str(a.fixture.parent/'native_ui_smoke.py')
env['PYTHONPATH']=str(a.fixture.parent.parent)
services=out/'data/dbus-1/services';services.mkdir(parents=True)
(services/'org.projectluma.Connect1.service').write_text('[D-BUS Service]\nName=org.projectluma.Connect1\nExec=/usr/bin/python3 -m luma_continuity.daemon\n')
env['LUMA_STATUS_SCALE']=str(a.scale)
env['LUMA_STATUS_RTL']='1' if a.rtl else '0'
env.update(G_DEBUG='fatal-warnings',GSETTINGS_BACKEND='keyfile',XDG_CURRENT_DESKTOP='GNOME',GNOME_SHELL_SESSION_MODE='user',XDG_DATA_DIRS='/usr/local/share:/usr/share',TMPDIR=str(out/'runtime'),SHELL_BACKGROUND_IMAGE='/usr/share/backgrounds/luma/luma-mesh-gradient.jpg')
fixture=out/'dash-native.js';shutil.copy2(a.fixture,fixture)
wrapper=out/'wayland-only.sh';wrapper.write_text('#!/bin/sh\nexec "$@" --no-x11\n');wrapper.chmod(0o700)
launcher=out/'private-session.py';launcher.write_text('''import subprocess,sys,time
p=subprocess.Popen(['/usr/libexec/at-spi-bus-launcher','--launch-immediately'])
registry=None
pipewire=subprocess.Popen(['/usr/bin/pipewire'])
try:
 for i in range(30):
  r=subprocess.run(['gdbus','call','--session','--dest','org.freedesktop.DBus','--object-path','/org/freedesktop/DBus','--method','org.freedesktop.DBus.NameHasOwner','org.a11y.Bus'],capture_output=True,text=True)
  if 'true' in r.stdout:break
  if p.poll() is not None:raise SystemExit('Accessibility launcher failed')
  time.sleep(0.1)
 else:raise SystemExit('Accessibility bus readiness timed out')
 registry=subprocess.Popen(['/usr/libexec/at-spi2-registryd','--use-gnome-session'])
 time.sleep(0.3)
 if registry.poll() is not None:raise SystemExit('Accessibility registry failed')
 raise SystemExit(subprocess.call(sys.argv[1:]))
finally:
 if registry is not None:
  registry.terminate();registry.wait(timeout=5)
 p.terminate();p.wait(timeout=5)
 pipewire.terminate();pipewire.wait(timeout=5)
''')
cmd=['dbus-run-session','--','python3',str(launcher),'/usr/bin/gnome-shell','--headless','--wayland','--no-x11','--virtual-monitor','2560x1440' if a.scale==2 else '1280x720','--wayland-display','luma-connect-test-display','--automation-script',str(fixture)]
(out/'packages.txt').write_text(subprocess.check_output(['rpm','-q','gnome-shell','gnome-shell-common','gnome-control-center','luma-shell-state'],text=True))
(out/'invocation.json').write_text(json.dumps({'command':cmd,'environment':{k:v for k,v in env.items() if k.startswith(('G_DEBUG','GSETTINGS','XDG','GNOME','SHELL_'))}},indent=2))
with (out/'runtime.log').open('w') as log:
 proc=subprocess.Popen(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 try:code=proc.wait(timeout=150)
 except subprocess.TimeoutExpired:code=124
 finally:
  try:os.killpg(proc.pid,signal.SIGTERM)
  except ProcessLookupError:pass
  try:proc.wait(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
text=(out/'runtime.log').read_text();passed=code==0 and 'PASS native Connect runner' in text
(out/'runner-result.json').write_text(json.dumps({'exit':code,'native_cases_passed':passed},indent=2)+'\n')
raise SystemExit(0 if passed else 1)
