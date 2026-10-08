# SPDX-License-Identifier: Apache-2.0
#
# Built by scripts/packages/build-luma-python-app.sh luma-maps. The tarball
# mirrors the repository: the app is src/luma-maps and its tests are
# tests/unit/test_maps_*.py with tests/fixtures/maps-v70.json.
#
# Replaces the private luma-maps-preview snapshot (0.1.0-2.test.20260906,
# installed under /opt/luma-previews/maps). The desktop ID stays
# org.projectluma.Maps and the command stays luma-maps, so dock pins and
# anything that launches Maps keep working across the upgrade.
#
# License: the source files carry Apache-2.0 headers, as the preview package
# did; they are not relicensed here.
Name:           luma-maps
Version:        0.1.0
Release:        1.luma.7.creator20261005.1%{?dist}
Summary:        Maps for Luma
License:        Apache-2.0
URL:            https://projectluma.org/
Source0:        %{name}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-gobject
BuildRequires:  gtk4
BuildRequires:  libadwaita
BuildRequires:  libportal >= 0.7
BuildRequires:  libportal-gtk4
BuildRequires:  libshumate
BuildRequires:  luma-developer-platform >= 0.1.0-1.luma.78~preview.20260923.1
BuildRequires:  desktop-file-utils
BuildRequires:  xorg-x11-server-Xvfb
BuildRequires:  dbus-daemon
BuildRequires:  libXtst
Requires:       python3
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       luma-developer-platform >= 0.1.0-1.luma.78~preview.20260923.1
# Shumate-1.0 draws the map tiles.
Requires:       libportal >= 0.7
Requires:       libportal-gtk4
Requires:       xdg-desktop-portal
Requires:       xdg-desktop-portal-gnome
Requires:       geoclue2
Requires:       libshumate
Obsoletes:      luma-maps-preview < 0.1.0-3
Provides:       luma-maps-preview = %{version}-%{release}

%description
Maps finds places, keeps the ones you save, and shows the way there, drawn
with LumaUI over map tiles from the providers it is configured with.

%prep
%autosetup -n %{name}
cp src/%{name}/LICENSE.md .

%build
# Pure Python over LumaUI; nothing to compile.

%install
app=src/luma-maps
install -d %{buildroot}%{_datadir}/%{name}/data
cp -R $app/luma_maps %{buildroot}%{_datadir}/%{name}/
cp -R $app/data/icons $app/data/maps.css $app/data/providers.toml %{buildroot}%{_datadir}/%{name}/data/
install -d %{buildroot}%{_bindir}
cat >%{buildroot}%{_bindir}/%{name} <<'EOF'
#!%{python3} -s
import sys
sys.path.insert(0, "%{_datadir}/%{name}")
from luma_maps.application import main
raise SystemExit(main())
EOF
chmod 0755 %{buildroot}%{_bindir}/%{name}
install -D -m 0644 $app/data/icons/org.projectluma.Maps.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Maps.svg
desktop-file-install --dir=%{buildroot}%{_datadir}/applications $app/data/org.projectluma.Maps.desktop
%py_byte_compile %{python3} %{buildroot}%{_datadir}/%{name}/luma_maps

%check
desktop=%{buildroot}%{_datadir}/applications/org.projectluma.Maps.desktop
desktop-file-validate $desktop
grep -Fxq 'Name=Maps' $desktop
grep -Fxq 'Exec=%{name}' $desktop
test -f %{buildroot}%{_datadir}/%{name}/data/maps.css
test -f %{buildroot}%{_datadir}/%{name}/data/providers.toml
%{python3} counted-unittest.py --self-test
%{python3} counted-unittest.py tests/unit 25
# The installed tree imports as the launcher imports it, Shumate included, and
# reads its shipped provider list.
# The launcher and desktop entry run the real app, never the isolated
# preview ID, so the desktop ID above matches the window's application ID.
if grep -Fq LUMA_MAPS_PREVIEW $desktop %{buildroot}%{_bindir}/%{name}; then
  echo 'the launcher or desktop entry selects the Maps preview' >&2
  exit 1
fi
env -u LUMA_MAPS_PREVIEW PYTHONPATH= HOME=$PWD/home XDG_CONFIG_HOME=$PWD/home/config %{python3} -c "
import sys
sys.path.insert(0, '%{buildroot}%{_datadir}/%{name}')
import luma_maps.application
import gi
gi.require_version('Xdp', '1.0')
gi.require_version('XdpGtk4', '1.0')
from gi.repository import Xdp, XdpGtk4
assert Xdp.LocationMonitorFlags.NONE == 0
assert hasattr(Xdp.LocationAccuracy, 'EXACT')
assert hasattr(Xdp.Portal, 'initable_new')
assert luma_maps.application.APP_ID == 'org.projectluma.Maps', luma_maps.application.APP_ID
from luma_maps import providers
shipped = providers._config_paths()[0]
assert shipped.is_file(), f'shipped provider list missing: {shipped}'
assert providers.load().tiles, 'the shipped provider list names no tile provider'
print('Maps imports from the installed tree and reads', shipped)
"

# Native controls and mouse input use the installed app and actual shared kit.
# The authoritative shell schema is part of this Source0, on a private backend.
xvfb-run -a --server-args="-screen 0 1920x1080x24" dbus-run-session -- \
  env GSK_RENDERER=cairo GTK_A11Y=none \
    LUMA_CREATOR_SCHEMA_FILE="$PWD/src/luma-shell-state/org.project_luma.shell-state.gschema.xml" \
    LUMA_MAPS_FIXTURE="$PWD/tests/fixtures/maps-v70.json" \
    PYTHONPATH=%{buildroot}%{_datadir}/%{name} \
  %{python3} src/luma-maps/tests/actionbar_runtime.py
xvfb-run -a --server-args="-screen 0 1920x1080x24" dbus-run-session -- \
  env GSK_RENDERER=cairo GTK_A11Y=none \
    LUMA_MAPS_FIXTURE="$PWD/tests/fixtures/maps-v70.json" \
    PYTHONPATH=%{buildroot}%{_datadir}/%{name} \
  %{python3} src/luma-maps/tests/compact_sidebar_runtime.py

%files
%license LICENSE.md
%{_bindir}/%{name}
%{_datadir}/%{name}/
%{_datadir}/applications/org.projectluma.Maps.desktop
%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Maps.svg

%changelog
* Mon Oct 05 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.7.creator20261005.1
- Keep sidebar access in the application menu at every responsive width
- Exercise actual search selection and guide navigation through that command

* Mon Oct 05 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.6.creator20261005.1
- Use shared raised icon controls in the desktop map action bar
- Preserve native zoom, permission-based location and responsive search actions
- Replay actual mouse input and desktop/phone transitions in light and dark

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4.creator20261004.1
- Release build from the reconciled 2026-09-28 tree.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- LumaUI port update: the app follows the latest approved design.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First package, replacing the luma-maps-preview test snapshot with the same
  desktop ID and command.
