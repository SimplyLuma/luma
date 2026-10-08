# SPDX-License-Identifier: Apache-2.0
# Private experiment only. No composed pin or enabled service until Class D gates.
Name:           luma-continuity
Version:        0.1.0
Release:        0.63.experiment%{?dist}
Summary:        Luma Connect experimental native device boundary
License:        Apache-2.0
BuildArch:      noarch
Source0:        luma-continuity.tar.gz
Source1:        org.project_luma.shell-state.gschema.xml
BuildRequires:  python3-devel
BuildRequires:  luma-developer-platform >= 0.1.0
BuildRequires:  xorg-x11-server-Xvfb
BuildRequires:  xorg-x11-xauth
BuildRequires:  dbus-daemon
BuildRequires:  systemd-rpm-macros
BuildRequires:  python3dist(cryptography)
BuildRequires:  python3-gobject
BuildRequires:  luma-application-installer >= 0.1.0-1.luma.55
BuildRequires:  gstreamer1-plugins-base
BuildRequires:  gstreamer1-plugins-good
BuildRequires:  gstreamer1-plugins-bad-free
BuildRequires:  libnice-gstreamer1
BuildRequires:  pipewire-utils
BuildRequires:  pipewire-pulseaudio
BuildRequires:  wireplumber
BuildRequires:  openssl
BuildRequires:  qrencode-libs
BuildRequires:  nodejs
BuildRequires:  prairie-core-apps >= 0.1.0-1.luma.74.connect.19~preview.20260913.1
BuildRequires:  (python3dist(websockets) >= 15.0.1 with python3dist(websockets) < 18)
BuildRequires:  (python3dist(authlib) >= 1.7.2 with python3dist(authlib) < 1.8)
BuildRequires:  (python3dist(httpx) >= 0.28.1 with python3dist(httpx) < 0.29)
BuildRequires:  (python3dist(joserfc) >= 1.7.5 with python3dist(joserfc) < 2)
Requires:       python3
Requires:       openssl
# Pairing and Power Mode QR codes: python3-qrcode when present, else libqrencode.
Requires:       (python3dist(qrcode) or qrencode-libs)
# Power Mode pairs with Android wireless debugging through adb.
Recommends:     android-tools
Requires:       (python3dist(websockets) >= 15.0.1 with python3dist(websockets) < 18)
Requires:       python3dist(cryptography)
Requires:       (python3dist(authlib) >= 1.7.2 with python3dist(authlib) < 1.8)
Requires:       (python3dist(httpx) >= 0.28.1 with python3dist(httpx) < 0.29)
Requires:       (python3dist(joserfc) >= 1.7.5 with python3dist(joserfc) < 2)
Requires:       python3-gobject
Requires:       gstreamer1-plugins-base
Requires:       gstreamer1-plugins-good
Requires:       gstreamer1-plugins-bad-free
Requires:       libnice-gstreamer1
Requires:       pipewire-pulseaudio
Requires:       libsecret
Requires:       prairie-core-apps >= 0.1.0-1.luma.74.connect.32~preview.20260915.1
Requires:       luma-developer-platform
Requires:       luma-application-installer >= 0.1.0-1.luma.55
Requires:       libadwaita >= 1.6

%description
Explicit local device pairing tools and native continuity transport library,
and Luma Connect for Android phones (ADR-021): QR pairing without an account,
notifications, clipboard, files, webcam, screen, calls and texts.
The account daemon is session D-Bus activated on demand and remains signed out
without administrator-provided HTTPS configuration. No composed release integration. Native app and hardware acceptance remain open.

%prep
%autosetup -n luma-continuity

%build

%install
install -d %{buildroot}%{python3_sitelib}/luma_continuity
install -m 0644 luma_continuity/*.py %{buildroot}%{python3_sitelib}/luma_continuity/
install -D -m 0755 bin/luma-connect-device %{buildroot}%{_bindir}/luma-connect-device

install -D -m 0755 bin/luma-connect %{buildroot}%{_bindir}/luma-connect
install -D -m 0755 bin/luma-connect-daemon %{buildroot}%{_bindir}/luma-connect-daemon
install -D -m 0644 data/org.projectluma.Connect.desktop %{buildroot}%{_datadir}/applications/org.projectluma.Connect.desktop
install -D -m 0644 data/org.projectluma.Connect1.service %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.Connect1.service
# D-Bus activation runs the daemon under systemd, never beside it.
install -D -m 0644 data/luma-connect.service %{buildroot}%{_userunitdir}/luma-connect.service
# ADR-033: the daemon is Luma Connect's background agent, declared for luma-background;
# the autostart entry starts it until that service is installed.
install -D -m 0644 data/org.projectluma.Connect.toml %{buildroot}%{_datadir}/luma/background/org.projectluma.Connect.toml
install -D -m 0644 data/org.projectluma.Connect.Agent.desktop %{buildroot}%{_sysconfdir}/xdg/autostart/org.projectluma.Connect.Agent.desktop
install -D -m 0644 data/icons/hicolor/scalable/apps/org.projectluma.Connect.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Connect.svg
install -d %{buildroot}%{_datadir}/icons/hicolor/scalable/actions
install -m 0644 data/icons/hicolor/scalable/actions/luma-connect-*-symbolic.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/actions/

%check
PYTHONPATH=. %{__python3} -W error::ResourceWarning tests/run_package_checks.py
# Actual shared sign-in controls on an isolated display/bus and private state.
# The production appearance schema controls Light/Ink; no installed Shell dependency.
mkdir -p native-signin-schemas
install -m 0644 %{SOURCE1} native-signin-schemas/
glib-compile-schemas native-signin-schemas
signin_root=$(mktemp -d)
mkdir -p "$signin_root"/{config,data,cache,state,runtime,captures}
chmod 0700 "$signin_root/runtime"
timeout 120s xvfb-run -a -s "-screen 0 1920x1080x24" dbus-run-session -- env \
  HOME="$signin_root" XDG_CONFIG_HOME="$signin_root/config" XDG_DATA_HOME="$signin_root/data" \
  XDG_CACHE_HOME="$signin_root/cache" XDG_STATE_HOME="$signin_root/state" XDG_RUNTIME_DIR="$signin_root/runtime" \
  GSETTINGS_SCHEMA_DIR="$PWD/native-signin-schemas" GSETTINGS_BACKEND=keyfile GSK_RENDERER=cairo PYTHONPATH=. \
  LUMA_CONNECT_SMOKE_OUTPUT="$signin_root/captures" %{__python3} tests/native_signin_ui_smoke.py
rm -rf "$signin_root"
# Connect shows the phone's notifications here, so Settings must list it.
grep -qx 'X-GNOME-UsesNotifications=true' \
  %{buildroot}%{_datadir}/applications/org.projectluma.Connect.desktop
node shell/test-notification-export.mjs

%files
%license LICENSE.md
%doc README.md
%{python3_sitelib}/luma_continuity/
%{_bindir}/luma-connect-device

%{_bindir}/luma-connect
%{_bindir}/luma-connect-daemon
%{_datadir}/applications/org.projectluma.Connect.desktop
%{_datadir}/dbus-1/services/org.projectluma.Connect1.service
%{_userunitdir}/luma-connect.service
%dir %{_datadir}/luma
%dir %{_datadir}/luma/background
%{_datadir}/luma/background/org.projectluma.Connect.toml
%config(noreplace) %{_sysconfdir}/xdg/autostart/org.projectluma.Connect.Agent.desktop
%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Connect.svg
%{_datadir}/icons/hicolor/scalable/actions/luma-connect-*-symbolic.svg

%changelog
* Tue Oct 06 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.60.experiment
- Remove the unshipped decorative Sign In glyph and qualify every native form icon.

* Tue Oct 06 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.59.experiment
- Bound shared native sign-in forms, preserve entry focus rings, and use the
  shipped Hub glyph; prefer chosen computer names over firmware placeholders.

* Mon Oct 05 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.57.experiment
- Sign In and Enter Your Code use shared LumaUI cards, fields, type and buttons. Preserve sync code validation and callbacks; qualify real responsive Light/Ink, one request at a time, error retry and dismissed callback behavior.

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.56.experiment
- Luma Connect declares X-GNOME-UsesNotifications, so Settings lists it under Notifications and its per-app switches, including Badge App Icon, can be reached. It posts the phone’s notifications on this computer as itself.

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.55.experiment
- Luma Connect's daemon is its background agent (ADR-033, communication): declared in /usr/share/luma/background for luma-background (luma-connect-daemon --agent), started at login by the autostart entry until that service is installed, publishing whether the link is on, paired and reachable phones, the account state and hub enrolment as org.projectluma.Connect.Agent
- D-Bus activation of org.projectluma.Connect1 goes through luma-connect.service (SystemdService=), so a second daemon is never started outside systemd; the agent takes the name over from an on-demand daemon
- On resume and when the network returns, Luma Hub sync starts at once and its watcher reconnects, so shared calendars and contacts don't wait for the next timer

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.54.experiment
- Luma Connect: the sidebar and content islands keep their gap again

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.53.experiment
- Account shows your name, email and phone number, each with a dialog to change it, and says plainly what is and isn’t verified
- A switch lets people who have your number find you in Messages once numbers can be verified; it is off unless you turn it on
- Sign-In names where your password lives and opens its settings

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.52.experiment
- Luma Connect is rebuilt around one account: Account, Syncing, Devices and Phones, with the account at the head of the sidebar
- Sign in with a one-time code, and add another device from Devices with a code this computer makes
- Syncing shows each service with its app and syncs on request; background refreshes no longer flash banners or rebuild the page
- Every screen keeps the title bar; notices are toasts; the keyring notice sits with the relay instead of blocking the app
- The relay's messages, calls and sessions move into their own sheet under Phones
- A new app icon and Lucide glyphs throughout

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.51.experiment
- Pairing QR codes use the shared encoder in prairie-core-apps (prairie_apps.qr_code)

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-0.50.experiment
- Luma Connect for Android phones (ADR-021): QR pairing without an account, notifications with replies, clipboard, files and links, find my phone, webcam and screen with codec negotiation, trackpad, Do Not Disturb, hotspot, approvals, Bluetooth calls, texts and Power Mode
- One Phones panel for Android phones and phones on the Luma account; Open Messages replaces Use for Messages
- message_services lists every paired phone that allowed texts for Messages (ADR-022)
- Pairing QR codes draw with libqrencode when python3-qrcode is absent
- Includes the Codex 0.49 sources unchanged apart from the merge
