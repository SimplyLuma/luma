# SPDX-License-Identifier: Apache-2.0

Name:           luma-displays
Version:        0.1.0
Release:        1.luma.6.creator20261008.1%{?dist}
Summary:        Arrange displays and name the arrangements you use
License:        Apache-2.0
URL:            https://projectluma.org/apps/displays
Source0:        luma-displays.tar.gz
Source1:        LICENSE.md

BuildArch:      noarch
BuildRequires:  luma-developer-platform >= 0.1.0-1.luma.100.creator20261007.1
BuildRequires:  dbus-daemon
BuildRequires:  xorg-x11-server-Xvfb
BuildRequires:  python3-gobject-base
BuildRequires:  meson >= 1.3
BuildRequires:  ninja-build
BuildRequires:  python3-devel >= 3.11
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib
Requires:       python3 >= 3.11
Requires:       python3-gobject
Requires:       gtk4 >= 4.18
Requires:       libadwaita >= 1.7
Requires:       luma-developer-platform >= 0.1.0-1.luma.100.creator20261007.1
Requires:       mutter

%description
Displays opens when displays are plugged in together for the first time and
asks how to use them: extend, mirror or one display. Displays can be dragged
into place, rotated and given a refresh rate and scale, and every kept
arrangement can be named.

%prep
%autosetup -n luma-displays
cp %{SOURCE1} LICENSE.md

%build
%meson
%meson_build

%install
%meson_install
desktop-file-validate %{buildroot}%{_datadir}/applications/org.projectluma.Displays.desktop

%check
# Exercise the installed Python tree and real launcher without source cwd fallback.
(cd /tmp && PYTHONPATH=%{buildroot}%{python3_sitelib} %{python3} -B -c "import luma_displays.application, luma_displays.settings; assert luma_displays.settings.__file__.startswith('%{buildroot}'); print('PASS: installed Displays imports fixed host settings action')")
(cd /tmp && dbus-run-session -- xvfb-run -a env GSK_RENDERER=cairo PYTHONPATH=%{buildroot}%{python3_sitelib} %{buildroot}%{_bindir}/org.projectluma.Displays --help)
PYTHONPATH=$PWD %{python3} -m unittest discover -s tests -p "test_*.py" -v
appstream-util validate-relax --nonet data/org.projectluma.Displays.metainfo.xml
PYTHONPATH=$PWD %{python3} -m py_compile luma_displays/*.py
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1440x1000x24" env GSK_RENDERER=cairo PYTHONPATH=.:tests python3 tests/displays_runtime.py
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1920x1200x24" env GSK_RENDERER=cairo PYTHONPATH=.:tests python3 tests/sandbox_boundary_runtime.py

%files
%license LICENSE.md
%{_bindir}/org.projectluma.Displays
%{python3_sitelib}/luma_displays/
%{_datadir}/applications/org.projectluma.Displays.desktop
%{_datadir}/metainfo/org.projectluma.Displays.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Displays.svg
%{_datadir}/dbus-1/services/org.projectluma.Displays.service

%changelog
* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Ask about a new arrangement only after it has held for a few seconds, so no window flashes at login

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Keep one primary display when Mutter reports two, so changes apply

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First Displays: ask how to use a new arrangement, arrange, and name arrangements
