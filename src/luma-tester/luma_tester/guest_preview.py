# SPDX-License-Identifier: Apache-2.0
"""First-use defaults for the isolated, automatically signed-in VM preview."""
USER = 'luma'
PASSWORD = 'TryLuma'

# The public preview password is for local desktop authorization only.
# Root remains locked and SSH continues to require the launcher's private key.
SSH_POLICY = '''from pathlib import Path
import subprocess
p=Path('/etc/ssh/sshd_config.d/00-try-luma-preview.conf')
p.write_text('PasswordAuthentication no\\nKbdInteractiveAuthentication no\\n')
subprocess.run(['/usr/sbin/sshd','-t'],check=True)
settings=subprocess.check_output(['/usr/sbin/sshd','-T'],text=True).splitlines()
assert 'passwordauthentication no' in settings and 'kbdinteractiveauthentication no' in settings
subprocess.run(['systemctl','reload','sshd'],check=True)
'''

# Some generalized images contain only Flatpak's user-repository config. An
# incomplete user repo prevents resolution of otherwise healthy system apps.
# Initialize only that exact empty skeleton; never remove app data or repair an
# existing object store implicitly. Existing workspaces need this check too.
FLATPAK_REPAIR = '''import configparser, subprocess
from pathlib import Path
repo = Path.home()/'.local/share/flatpak/repo'
config = repo/'config'
if (not repo.is_symlink() and config.is_file() and not config.is_symlink()
        and {p.name for p in repo.iterdir()} == {'config'}):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config)
    mode = parser.get('core', 'mode', fallback='')
    if mode in ('bare-user', 'bare-user-only'):
        subprocess.run(['ostree', '--repo='+str(repo), 'init', '--mode='+mode], check=True)
'''

CONFIGURE = FLATPAK_REPAIR + '''import os, subprocess
from pathlib import Path
marker=Path.home()/'.local/state/try-luma/preview-configured-v2'
if not marker.exists():
    subprocess.run(['sudo','python3','-c',SSH_POLICY],check=True)
    subprocess.run(['sudo','chpasswd'],input=PASSWORD_INPUT,text=True,check=True)
    env=dict(os.environ,DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/'+str(os.getuid())+'/bus')
    for schema,key,value in [('org.gnome.desktop.screensaver','lock-enabled','false'),('org.gnome.desktop.session','idle-delay','0')]:
        subprocess.run(['gsettings','set',schema,key,value],env=env,check=True)
    marker.parent.mkdir(parents=True,exist_ok=True)
    marker.write_text('1\\n')
# Emulated guests can exceed GNOME's default 45-second service startup limit.
# Apply this separately so existing previews retain their password and settings.
env=dict(os.environ,DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/'+str(os.getuid())+'/bus')
startup_marker=Path.home()/'.local/state/try-luma/shell-startup-configured-v1'
if not startup_marker.exists():
    unit='org.gnome.Shell@user.service'
    dropin=Path.home()/'.config/systemd/user'/ (unit+'.d')/'60-try-luma.conf'
    dropin.parent.mkdir(parents=True,exist_ok=True)
    dropin.write_text('[Service]\\nTimeoutStartSec=300\\n')
    subprocess.run(['systemctl','--user','daemon-reload'],env=env,check=True)
    state=subprocess.check_output(['systemctl','--user','show',unit,'-p','ActiveState','--value'],env=env,text=True).strip()
    if state in ('activating','failed','deactivating'):
        subprocess.run(['sudo','systemctl','--no-block','restart','gdm'],check=True)
    startup_marker.parent.mkdir(parents=True,exist_ok=True)
    startup_marker.write_text('1\\n')
'''.replace('SSH_POLICY', repr(SSH_POLICY)).replace('PASSWORD_INPUT', repr(USER + ':' + PASSWORD + '\n'))


def supported(session):
    """Only launcher-managed Luma previews have the public local credentials.

    Completed installer workspaces predate the unified image protocol, but
    remain managed previews. Arbitrary imported images keep their own accounts.
    """
    from . import provision
    return session.manifest.get('user') == USER and (
        session.manifest.get('guest_protocol') == 'luma-unified-v1' or provision.ready(session))


def configure(session):
    import subprocess
    from .core import run
    run(session.ssh(['python3', '-c', CONFIGURE]), stdout=subprocess.DEVNULL, timeout=60)
