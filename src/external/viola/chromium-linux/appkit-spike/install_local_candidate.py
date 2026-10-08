# SPDX-License-Identifier: GPL-3.0-only
"""Install Nick's explicitly accepted candidate without altering the system RPM.

Run after staging the release and stopping the exact source preview unit.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import gi
from gi.repository import Gio

home = Path.home()
base = home / '.local/lib/viola-luma'
release = base / 'releases/20260914-native-1'
assert (release / 'engine/chrome').is_file()
state = home / '.local/state/viola-luma'
backup = state / 'launcher-backup-20260914'
backup.mkdir(parents=True, exist_ok=True)
profile = home / '.local/share/viola-luma/profile'
if not profile.exists():
    sources = list((home / 'viola-dev/appkit-spike-results/manual-close-tab-reopen-20260914').glob('native-window-*/profile'))
    assert len(sources) == 1, 'Expected exactly one source profile'
    profile.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copytree(sources[0], profile, symlinks=True)
    # These are runtime locks from the stopped source process, never profile data.
    for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
        path = profile / name
        if path.is_symlink() or path.exists(): path.unlink()
    profile.chmod(0o700)
link = base / 'current.new'
if link.is_symlink(): link.unlink()
link.symlink_to(release)
link.replace(base / 'current')
bin_dir = home / '.local/bin'
bin_dir.mkdir(parents=True, exist_ok=True)
launcher = bin_dir / 'viola-luma'
launcher.write_text('''#!/usr/bin/bash
set -euo pipefail
release="$HOME/.local/lib/viola-luma/current"
export G_RESOURCE_OVERLAYS="/org/gnome/Adwaita/styles=$release/toolkit/adw:/org/gtk/libgtk/theme/Default=$release/toolkit/gtk"
exec /usr/bin/python3 "$release/host/integrated_window.py" --engine "$release/engine/chrome" --profile "$HOME/.local/share/viola-luma/profile" --work-root "$HOME/.local/state/viola-luma/sessions" --interactive "$@"
''')
launcher.chmod(0o755)
applications = home / '.local/share/applications'
app_id = 'org.projectluma.Viola.NativeIntegration.desktop'
body = f'''[Desktop Entry]
Type=Application
Version=1.0
Name=Viola
Comment=Viola for Luma
Exec={launcher} %U
Icon={release}/host/assets/org.projectluma.Viola.NativeIntegration.svg
Terminal=false
Categories=Network;WebBrowser;
MimeType=text/html;x-scheme-handler/http;x-scheme-handler/https;
StartupNotify=true
StartupWMClass=org.projectluma.Viola.NativeIntegration
'''
(applications / app_id).write_text(body)
old_ids = ['org.projectluma.Installed.rpm-viola-browser-stable-c3812ebd3343.desktop', 'com.rhyme.viola.LumaPreview.desktop']
for old_id in old_ids:
    path = applications / old_id
    if path.exists() and not (backup / old_id).exists(): shutil.copy2(path, backup / old_id)
    path.write_text(body + 'NoDisplay=true\n')
old_script = home / 'bin/viola-luma-preview'
if old_script.exists():
    if not (backup / 'viola-luma-preview').exists(): shutil.copy2(old_script, backup / 'viola-luma-preview')
    old_script.write_text(f'#!/usr/bin/bash\nexec {launcher} "$@"\n')
    old_script.chmod(0o755)
settings = Gio.Settings.new('org.gnome.shell')
favorites = settings.get_strv('favorite-apps')
replacement = [app_id if value in old_ids else value for value in favorites]
replacement = list(dict.fromkeys(replacement))
settings.set_strv('favorite-apps', replacement)
Gio.Settings.sync()
for kind in ('x-scheme-handler/http','x-scheme-handler/https','text/html'):
    old = subprocess.check_output(['xdg-mime','query','default',kind],text=True).strip()
    if old in old_ids:
        subprocess.run(['xdg-mime','default',app_id,kind],check=True)
subprocess.run(['update-desktop-database',str(applications)],check=True)
(backup/'installation.json').write_text(json.dumps({'release':str(release),'profile':str(profile),'previous_favorites':favorites,'desktop_id':app_id},indent=2))
print(json.dumps({'launcher':str(launcher),'profile':str(profile),'desktop_id':app_id,'backup':str(backup)}))
