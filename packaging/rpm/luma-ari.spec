# SPDX-License-Identifier: Apache-2.0

Name:           luma-ari
Version:        0.1.0
Release:        1.luma.16.creator20261007.1%{?dist}
Summary:        Luma's assistant, running an open model on this computer
License:        Apache-2.0
URL:            https://projectluma.org/apps/ari
Source0:        luma-ari.tar.gz
Source1:        LICENSE.md

BuildArch:      noarch
BuildRequires:  luma-developer-platform >= 0.1.0-1.luma.100.creator20261007.1
BuildRequires:  python3-gobject-base
BuildRequires:  meson >= 1.3
BuildRequires:  ninja-build
BuildRequires:  python3-devel >= 3.11
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib
BuildRequires:  systemd-rpm-macros
BuildRequires:  glib2-devel
BuildRequires:  xorg-x11-server-Xvfb
BuildRequires:  dbus-daemon
BuildRequires:  libXtst
Requires:       python3 >= 3.11
Requires:       python3-gobject
Requires:       libsecret
Requires:       gtk4 >= 4.18
Requires:       libadwaita >= 1.7
Requires:       luma-developer-platform >= 0.1.0-1.luma.100.creator20261007.1
Requires:       luma-ari-runtime >= 0.4.0
Requires:       luma-displays
Requires:       dbus-common
Requires:       luma-application-installer >= 0.1.0-1.luma.55.creator20261007.1
# The weather tool reads the Weather app's forecast backend when it is there.
Recommends:     prairie-core-apps

%description
Ari answers questions and changes personal settings such as the dock, the
theme, the wallpaper and the refresh rate. Every change is a step that can be
undone. The daemon runs an open model locally through luma-ari-runtime, keeps
conversations and an append-only activity log in the person's own data folder,
and decides in code which tools may run.

%prep
%autosetup -n luma-ari/src/luma-ari
cp %{SOURCE1} LICENSE.md

%build
%meson
%meson_build

%install
%meson_install
desktop-file-validate %{buildroot}%{_datadir}/applications/org.projectluma.Ari.desktop

%check
PYTHONPATH=$PWD GSETTINGS_BACKEND=memory %{python3} -m unittest discover -s ../../tests/luma-ari -p "test_*.py" -v
PYTHONPATH=$PWD dbus-run-session -- %{python3} ../../tests/luma-ari/native_host_identity.py
appstream-util validate-relax --nonet data/org.projectluma.Ari.metainfo.xml
%{python3} -m py_compile ari/*.py ari/tools/*.py ari_ui/*.py eval/run.py
# Every module the daemon imports must be installed, not only present in the tree.
PYTHONPATH=%{buildroot}%{python3_sitelib} %{python3} -P -c "import ari.cloud, ari.service"
glib-compile-schemas --strict --dry-run data
%{python3} -c "import json; json.load(open('eval/cases.json'))"
native_home=$(mktemp -d)
trap 'rm -rf "$native_home"' EXIT
PYTHONPATH=%{buildroot}%{python3_sitelib} XDG_CONFIG_HOME="$native_home/config" XDG_DATA_HOME="$native_home/data" GSETTINGS_BACKEND=memory GSK_RENDERER=cairo dbus-run-session -- xvfb-run -a -s "-screen 0 1920x1080x24" %{python3} ../../tests/luma-ari/native_sidebar_controls.py

%files
%license LICENSE.md
%{_bindir}/ari
%{_bindir}/org.projectluma.Ari
%{_libexecdir}/luma-ari/ari-daemon
%{python3_sitelib}/ari/
%{python3_sitelib}/ari_ui/
%{_datadir}/luma-ari/
%{_datadir}/applications/org.projectluma.Ari.desktop
%{_datadir}/metainfo/org.projectluma.Ari.metainfo.xml
%{_datadir}/glib-2.0/schemas/org.projectluma.Ari.gschema.xml
%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Ari.svg
%{_datadir}/dbus-1/services/org.projectluma.Ari1.service
%{_datadir}/dbus-1/services/org.projectluma.Ari.service
%{_userunitdir}/ari-daemon.service

%changelog
* Wed Oct 07 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.16.creator20261007.1
- Preserve secondary ink on welcome suggestions and verify real mapped contrast.

* Mon Oct 05 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.16.creator20261007.1
- Keep sidebar controls in the application menu and F9; preserve phone title navigation.
- Gate installed responsive sidebar/menu behavior with the real GTK window.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.13.creator20261004.1
- Ari's own mark is found as a named icon from its icons folder.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.11.lumaui20260928.1
- LumaUI update from the Prairie rollout review.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.10
- LumaUI port update: the app follows the latest approved design.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.9
- LumaUI port: the app is rebuilt on LumaUI to match the approved design.

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.8
- Draw Ari's icon as a full tile at the corner ratio every Luma app icon uses, so it matches the dock

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.7
- Think through OpenRouter: recommended models by role, any model by search or ID, live prices, expensive-model confirmation, a monthly limit and the key in the keyring

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.6
- Take nothing when idle: the model unloads a minute after an answer, at once when Ari closes or memory runs short, and the daemon exits after two quiet minutes
- The model runs at low CPU and I/O weight with half the context memory

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.5
- One settings catalogue for every user preference, with undo
- Any media player through MPRIS; time zone, volume, Do Not Disturb, Wi-Fi, Bluetooth and tiling
- Approval cards with Ask before system changes, every change, or never; a lockable master switch
- Wallpapers as Settings lists them; apps by any name; replies in the person's units
- Short follow-ups keep their context; instructions are never recited; false claims are corrected

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- Models, Permissions and Activity pages: switch, download and remove models, turn personal settings off, undo from the log

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Screen brightness, with undo
- Ari knows the current settings and what she changed today
- A setting's value must come from the person; the model cannot substitute one

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Let the model go after five quiet minutes; fall back to the CPU when GPU memory is short
- The quick-popover shortcut also closes it

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First Ari: local model, answers, personal settings with undo, activity log
