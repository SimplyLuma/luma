# SPDX-License-Identifier: Apache-2.0

Name:           luma-relay
Version:        0.1.0
Release:        1.luma.12%{?dist}
Summary:        Project Luma application compatibility integration
License:        Apache-2.0
URL:            https://projectluma.org/relay
BuildArch:      noarch

Source0:        luma-relay.tar.gz
Source1:        LICENSE.md

BuildRequires:  luma-developer-platform >= 0.1.0-1.luma.47~preview.20260906.1
BuildRequires:  dbus-daemon
BuildRequires:  xorg-x11-server-Xvfb
BuildRequires:  python3-gobject-base
BuildRequires:  desktop-file-utils
BuildRequires:  python3-devel
BuildRequires:  shared-mime-info
Requires:       bubblewrap
Requires:       gtk4
Requires:       libadwaita
Requires:       luma-developer-platform >= 0.1.0-1.luma.47~preview.20260906.1
Requires:       python3
Requires:       python3-gobject
Requires:       shared-mime-info
Suggests:       wine >= 11.0
Suggests:       wine-dxvk
Suggests:       winetricks
Suggests:       wine-luma-relay-explorer >= 11.0-3.luma.2

%description
Luma Relay makes applications built for other operating systems participate in
Luma as first-class applications. This package provides the adaptive control
surface, safe package inspection, per-application Windows environments,
sandbox policy, application records, and desktop integration. Wine, Waydroid,
and optional translation components retain their upstream identities.

%package fex
Summary:        Optional ARM Windows preview backend for Luma Relay
Requires:       %{name} = %{version}-%{release}
Requires:       fex-emu = 2604-1.fc44
Requires:       fex-emu-rootfs-fedora = 44^20260410.n.0-1.fc44
Requires:       erofs-utils >= 1.9

%description fex
Explicitly enables Relay's FEX/Wine preview on ARM systems with 4 KiB pages.
Uses Fedora's pinned runtime filesystem as a shared read-only cache inside
Relay's existing per-application sandbox. Physical-device acceptance is open.
Removing this companion disables translation without removing application data.

%prep
%autosetup -n luma-relay
cp %{SOURCE1} LICENSE.md

%build

%install
install -d %{buildroot}%{python3_sitelib}/luma_relay
install -m 0644 luma_relay/*.py %{buildroot}%{python3_sitelib}/luma_relay/
install -D -m 0755 bin/luma-relay %{buildroot}%{_bindir}/luma-relay
install -D -m 0755 bin/luma-relay-installer %{buildroot}%{_bindir}/luma-relay-installer
install -D -m 0755 bin/luma-relay-settings %{buildroot}%{_bindir}/luma-relay-settings
install -D -m 0755 bin/luma-relay-session %{buildroot}%{_bindir}/luma-relay-session
install -D -m 0644 data/org.projectluma.Relay.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.Relay.desktop
install -D -m 0644 data/org.projectluma.RelayInstaller.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.RelayInstaller.desktop
install -D -m 0644 data/luma-relay.xml \
  %{buildroot}%{_datadir}/mime/packages/luma-relay.xml
install -D -m 0644 data/org.projectluma.Relay.metainfo.xml \
  %{buildroot}%{_datadir}/metainfo/org.projectluma.Relay.metainfo.xml
install -D -m 0644 data/org.projectluma.Relay.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Relay.svg
install -D -m 0644 config/relay.conf %{buildroot}%{_sysconfdir}/luma/relay.conf
install -D -m 0644 config/fex.json %{buildroot}%{_datadir}/luma-relay/backends/fex.json

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/*.desktop
python3 -m py_compile %{buildroot}%{python3_sitelib}/luma_relay/*.py
PYTHONPATH=%{buildroot}%{python3_sitelib} python3 -m unittest discover -s tests -p "test_*.py"

env PYTHONPATH=. python3 tests/presentation.py
dbus-run-session -- xvfb-run -a --server-args="-screen 0 1440x1000x24" env GSK_RENDERER=cairo PYTHONPATH=. python3 tests/settings_runtime.py

%post
update-mime-database %{_datadir}/mime >/dev/null 2>&1 || :
update-desktop-database %{_datadir}/applications >/dev/null 2>&1 || :
gtk-update-icon-cache --force --quiet %{_datadir}/icons/hicolor >/dev/null 2>&1 || :

%postun
update-mime-database %{_datadir}/mime >/dev/null 2>&1 || :
update-desktop-database %{_datadir}/applications >/dev/null 2>&1 || :
gtk-update-icon-cache --force --quiet %{_datadir}/icons/hicolor >/dev/null 2>&1 || :

%files
%license LICENSE.md
%config(noreplace) %{_sysconfdir}/luma/relay.conf
%{_bindir}/luma-relay
%{_bindir}/luma-relay-installer
%{_bindir}/luma-relay-settings
%{_bindir}/luma-relay-session
%{python3_sitelib}/luma_relay/
%{_datadir}/applications/org.projectluma.Relay.desktop
%{_datadir}/applications/org.projectluma.RelayInstaller.desktop
%{_datadir}/mime/packages/luma-relay.xml
%{_datadir}/metainfo/org.projectluma.Relay.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Relay.svg

%files fex
%{_datadir}/luma-relay/backends/fex.json

%changelog
* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.12
- Ship the approved 6 September canvas artwork as the bundled application icon
  instead of the superseded flat 128px tile. The icon generator owns this file
  now, so the next canvas revision reaches it without a hand copy.

* Sun Sep 06 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.11.preview20260906.1
- Let Install own the portable-app launch step and retain Windows environments on removal

* Sat Sep 05 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.9
- Add an explicit optional ARM FEX backend within the private Relay sandbox
- Preserve installer source binding when native notification overlays are used

* Fri Sep 04 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.8
- Relay's settings and installer are the Application Kit's window

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.7
- Refresh the trusted native-notification bridge inside each private capsule.
- Bind the bridge read-only at runtime so Windows applications cannot replace it.

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.6
- Discover Fedora's Wine 11 WOW64 runtime layout for native notifications
- Bind the matching Explorer and native bridge paths into each capsule

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.5
- Mount Mutter's per-session Xauthority file read-only inside each capsule
- Limit the display credential mount to the user's private runtime directory

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- Use a fixed-length private runtime socket for native notifications
- Bind only that per-app endpoint into the Relay sandbox

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Add a per-application native notification broker without exposing session D-Bus
- Keep the Relay session owner alive for the full lifetime of the Wine process

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Bind NTSYNC into each capsule and detect Fedora's packaged DXVK layout
- Add explicit shared-folder revocation and linked upstream credits

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Add the adaptive Relay compatibility control surface
- Add bounded EXE/MSI inspection and one private Wine environment per app
- Add sandboxed launch, standard application entries, and reviewed components
