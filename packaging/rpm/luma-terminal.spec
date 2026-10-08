# SPDX-License-Identifier: MPL-2.0
#
# Built by scripts/packages/build-luma-python-app.sh luma-terminal. The
# tarball mirrors the repository: the app is src/luma-terminal.
#
# Installed beside the stock terminal for testing: its own application ID
# (org.projectluma.Terminal) and desktop file, and no Obsoletes or Conflicts.
Name:           luma-terminal
Version:        0.1.0
Release:        1.luma.4.creator20261004.1%{?dist}
Summary:        Terminal for Luma
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
# Vte-3.91.typelib, the GTK 4 VTE the terminal widget comes from.
BuildRequires:  vte291-gtk4
Requires:       python3
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       luma-developer-platform >= 0.1.0-1.luma.78~preview.20260923.1
Requires:       vte291-gtk4

%description
A terminal built on VTE and LumaUI: sessions, split panes that keep their
working folder, and command blocks you can select and act on.

%prep
%autosetup -n %{name}
cp src/%{name}/LICENSES/MPL-2.0.txt .

%build
# Pure Python over LumaUI; nothing to compile.

%install
app=src/luma-terminal
install -d %{buildroot}%{_datadir}/%{name}
cp -R $app/luma_terminal $app/style %{buildroot}%{_datadir}/%{name}/
install -d %{buildroot}%{_bindir}
cat >%{buildroot}%{_bindir}/%{name} <<'EOF'
#!%{python3} -s
import sys
sys.path.insert(0, "%{_datadir}/%{name}")
from luma_terminal.terminal import main
raise SystemExit(main())
EOF
chmod 0755 %{buildroot}%{_bindir}/%{name}
install -D -m 0644 $app/data/icons/hicolor/scalable/apps/org.projectluma.Terminal.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Terminal.svg
# The checkout's entry runs `python3 -m luma_terminal` from the tree; the
# installed one runs the launcher, which finds the package where it lives.
sed 's|^Exec=.*|Exec=%{name}|' $app/data/applications/org.projectluma.Terminal.desktop \
  >org.projectluma.Terminal.desktop
grep -Fxq 'Exec=%{name}' org.projectluma.Terminal.desktop
desktop-file-install --dir=%{buildroot}%{_datadir}/applications org.projectluma.Terminal.desktop
%py_byte_compile %{python3} %{buildroot}%{_datadir}/%{name}/luma_terminal

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/org.projectluma.Terminal.desktop
grep -Fxq 'Name=Terminal' %{buildroot}%{_datadir}/applications/org.projectluma.Terminal.desktop
test -f %{buildroot}%{_datadir}/%{name}/style/terminal.css
%{python3} counted-unittest.py --self-test
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1280x800x24" env GSK_RENDERER=cairo GDK_DEBUG=no-portals PYTHONPATH=$PWD/src/luma-terminal %{python3} counted-unittest.py src/luma-terminal/tests 6
# The installed launcher imports from the installed tree, VTE included.
PYTHONPATH= %{python3} -c "import sys; sys.path.insert(0, '%{buildroot}%{_datadir}/%{name}'); import gi; gi.require_version('Vte', '3.91'); from gi.repository import Vte; import luma_terminal.terminal"
# A real shell spawned in the terminal, a command delivered, selected, split
# and a new session opened, under a private X server and session bus.
cd src/luma-terminal
SHELL=/bin/bash dbus-run-session -- xvfb-run -a --server-args="-screen 0 1280x800x24" \
  env GSK_RENDERER=cairo PYTHONPATH=$PWD %{python3} tests/smoke_vte.py

%files
%license MPL-2.0.txt
%{_bindir}/%{name}
%{_datadir}/%{name}/
%{_datadir}/applications/org.projectluma.Terminal.desktop
%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Terminal.svg

%changelog
* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4.creator20261004.1
- Release build from the reconciled 2026-09-28 tree.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- LumaUI port update: the app follows the latest approved design.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First package, installed beside the stock terminal for testing.
