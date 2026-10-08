# SPDX-License-Identifier: MPL-2.0
#
# Built by scripts/packages/build-luma-python-app.sh luma-disks. The tarball
# mirrors the repository: the app is src/luma-disks and its tests are
# tests/unit/test_luma_disks_*.py with their fixtures under tests/fixtures.
#
# Installed beside the stock disk utility for testing: its own application ID
# (org.projectluma.Disks.Preview, which is what the app registers) and desktop
# file, and no Obsoletes or Conflicts.
#
# Every operation that can change a disk stays behind LUMA_DISKS_ALLOW_WRITES,
# which is off unless set to 1. Nothing here sets it, and %%check proves both.
Name:           luma-disks
Version:        0.1.0
Release:        1.luma.4.creator20261004.1%{?dist}
Summary:        Disks for Luma
License:        MPL-2.0
URL:            https://projectluma.org/
Source0:        %{name}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-gobject
BuildRequires:  gtk4
BuildRequires:  libadwaita
BuildRequires:  luma-developer-platform >= 0.1.0-1.luma.78~preview.20260923.1
BuildRequires:  desktop-file-utils
BuildRequires:  dbus-daemon
BuildRequires:  xorg-x11-server-Xvfb
Requires:       python3
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       luma-developer-platform >= 0.1.0-1.luma.78~preview.20260923.1
# Drives, volumes and usage come from the UDisks2 daemon over the system bus.
Requires:       udisks2

%description
Disks shows the drives and volumes attached to this computer and how their
space is used, read from UDisks2. Changes to disks are switched off in this
build.

%prep
%autosetup -n %{name}
cp src/%{name}/LICENSES/MPL-2.0.txt .

%build
# Pure Python over LumaUI; nothing to compile.

%install
app=src/luma-disks
install -d %{buildroot}%{_datadir}/%{name}
cp -R $app/luma_disks $app/style %{buildroot}%{_datadir}/%{name}/
install -d %{buildroot}%{_bindir}
cat >%{buildroot}%{_bindir}/%{name} <<'EOF'
#!%{python3} -s
import sys
sys.path.insert(0, "%{_datadir}/%{name}")
from luma_disks.app import main
raise SystemExit(main())
EOF
chmod 0755 %{buildroot}%{_bindir}/%{name}
install -D -m 0644 $app/data/icons/org.projectluma.Disks.Preview.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Disks.Preview.svg
desktop-file-install --dir=%{buildroot}%{_datadir}/applications $app/data/org.projectluma.Disks.Preview.desktop
%py_byte_compile %{python3} %{buildroot}%{_datadir}/%{name}/luma_disks

%check
desktop=%{buildroot}%{_datadir}/applications/org.projectluma.Disks.Preview.desktop
desktop-file-validate $desktop
grep -Fxq 'Name=Disks' $desktop
test -f %{buildroot}%{_datadir}/%{name}/style/disks.css
# The desktop ID must be the application ID the app registers, or the shell
# cannot match its window to this entry.
grep -Fq "APP_ID = 'org.projectluma.Disks.Preview'" src/luma-disks/luma_disks/app.py
# Writes stay off: nothing that starts Disks turns them on, and with the
# switch unset the backend refuses a change.
if grep -Fq LUMA_DISKS_ALLOW_WRITES $desktop %{buildroot}%{_bindir}/%{name}; then
  echo 'the launcher or desktop entry sets LUMA_DISKS_ALLOW_WRITES' >&2
  exit 1
fi
env -u LUMA_DISKS_ALLOW_WRITES PYTHONPATH= %{python3} -c "
import sys
sys.path.insert(0, '%{buildroot}%{_datadir}/%{name}')
from luma_disks import backend
try:
    backend.require_writes()
except backend.DiskError:
    print('writes are off by default: OK')
else:
    sys.exit('require_writes() allowed a change with LUMA_DISKS_ALLOW_WRITES unset')
"
# The installed tree imports as the launcher imports it, LumaUI included.
PYTHONPATH= %{python3} -c "import sys; sys.path.insert(0, '%{buildroot}%{_datadir}/%{name}'); import luma_disks.app"
%{python3} counted-unittest.py --self-test
dbus-run-session -- xvfb-run -a env GSK_RENDERER=cairo %{python3} counted-unittest.py tests/unit 70

%files
%license MPL-2.0.txt
%{_bindir}/%{name}
%{_datadir}/%{name}/
%{_datadir}/applications/org.projectluma.Disks.Preview.desktop
%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Disks.Preview.svg

%changelog
* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4.creator20261004.1
- Release build from the reconciled 2026-09-28 tree.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- LumaUI port update: the app follows the latest approved design.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First package, installed beside the stock disk utility for testing, with
  changes to disks switched off.
