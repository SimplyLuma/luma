# SPDX-License-Identifier: MPL-2.0

Name:           luma-background
Version:        0.1.0
Release:        1.luma.5.creator20261007.1%{?dist}
Summary:        Background activity for Luma apps: small agents that keep apps working
License:        MPL-2.0
URL:            https://projectluma.org/developer/kit/background-agents
Source0:        luma-background.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel >= 3.11
BuildRequires:  systemd-rpm-macros
BuildRequires:  python3-gobject-base
BuildRequires:  luma-application-installer >= 0.1.0-1.luma.53.creator20261007.1
Requires:       python3 >= 3.11
Requires:       python3-gobject-base
# Native service clients require the signed installed/retained application boundary.
Requires:       luma-application-installer >= 0.1.0-1.luma.53.creator20261007.1
# RestartSteps=, RestartMaxDelaySec= and ManagedOOMMemoryPressureDurationSec=
Requires:       systemd >= 256
# Timers deliver scheduled wakes with busctl, which systemd ships.
Requires:       /usr/bin/busctl
# Wake-ups for alarms: polkit decides who may wake the computer.
Requires:       polkit
Requires:       /usr/bin/true
Recommends:     xdg-desktop-portal
# Makes this the Background portal backend; without it GNOME's answers.
Recommends:     luma-portal >= 0.1.0-1.luma.8
# Agents publish Live Extensions under their app's identity with this broker.
Recommends:     luma-developer-platform >= 0.1.0-1.luma.65

%description
Some apps are only useful if they keep working when their windows are closed:
messages, calls, reminders, alarms and mail. luma-background runs each such
app's small background agent instead of its whole interface.

It registers agents from installed declarations and from the Background
portal, applies the person's decisions over Luma's defaults, runs each agent as
a systemd user unit with its category's memory, processor and disk limits,
wakes agents at login, when the network comes online, after sleep and on
schedules, pauses the ones that can wait while Power Saver is on, restarts
crashed agents and logs everything with the app's ID. Settings and the dock
show and change it through org.projectluma.Background1, and sandboxed apps
reach the same decisions through the standard Background portal.

It also carries org.projectluma.BackgroundWake1, a small system service that
lets the active session wake a suspended computer for an alarm: polkit decides
who may, and systemd arms the RTC alarm as a transient timer.

%prep
%autosetup -n luma-background

%build

%install
install -d -m 0755 %{buildroot}%{python3_sitelib}/luma_background
install -m 0644 luma_background/*.py %{buildroot}%{python3_sitelib}/luma_background/
install -D -m 0755 bin/luma-background-service %{buildroot}%{_libexecdir}/luma-background-service
install -D -m 0755 bin/luma-background %{buildroot}%{_bindir}/luma-background
install -D -m 0644 data/luma-background.service %{buildroot}%{_userunitdir}/luma-background.service
install -m 0644 data/luma-background.slice data/luma-background-essential.slice \
  data/luma-background-deferrable.slice %{buildroot}%{_userunitdir}/
install -D -m 0644 data/luma-background.preset %{buildroot}%{_userpresetdir}/80-luma-background.preset
install -D -m 0644 data/org.projectluma.Background1.service \
  %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.Background1.service
install -m 0644 data/org.freedesktop.impl.portal.desktop.luma.background.service \
  %{buildroot}%{_datadir}/dbus-1/services/
install -D -m 0644 data/luma-background.portal \
  %{buildroot}%{_datadir}/xdg-desktop-portal/portals/luma-background.portal
install -D -m 0644 data/defaults.toml %{buildroot}%{_datadir}/luma-background/defaults.toml
install -D -m 0644 data/org.projectluma.Background.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.Background.desktop
install -d -m 0755 %{buildroot}%{_datadir}/luma/background %{buildroot}%{_sysconfdir}/luma-background
# GNOME's session starts autostart entries itself and reads gnome/autostart in
# the data directories before /etc/xdg/autostart: these hidden entries keep it
# from also starting the fallback of an agent this service runs.
install -d -m 0755 %{buildroot}%{_datadir}/gnome/autostart
install -m 0644 data/session-autostart/*.desktop %{buildroot}%{_datadir}/gnome/autostart/
# Wake-ups for alarms (system bus, D-Bus activated, polkit-guarded).
install -D -m 0755 bin/luma-background-wake %{buildroot}%{_libexecdir}/luma-background-wake
install -D -m 0644 data/luma-background-wake.service %{buildroot}%{_unitdir}/luma-background-wake.service
install -D -m 0644 data/org.projectluma.BackgroundWake1.conf \
  %{buildroot}%{_datadir}/dbus-1/system.d/org.projectluma.BackgroundWake1.conf
install -D -m 0644 data/org.projectluma.BackgroundWake1.system.service \
  %{buildroot}%{_datadir}/dbus-1/system-services/org.projectluma.BackgroundWake1.service
install -D -m 0644 data/org.projectluma.background.policy \
  %{buildroot}%{_datadir}/polkit-1/actions/org.projectluma.background.policy

%check
cd tests && PYTHONPATH=$PWD/..:$PWD %{python3} -m unittest -v test_declaration test_registry_units test_manager test_wake test_login \
  test_session_autostart test_native_service
cd .. && %{python3} -m py_compile luma_background/*.py
desktop-file-validate %{buildroot}%{_datadir}/applications/org.projectluma.Background.desktop || :
for entry in Calendar Charlie Connect Messages Phone; do
  desktop-file-validate %{buildroot}%{_datadir}/gnome/autostart/org.projectluma.$entry.Agent.desktop
  grep -qx 'Hidden=true' %{buildroot}%{_datadir}/gnome/autostart/org.projectluma.$entry.Agent.desktop
done
%{python3} -c 'import sys, xml.dom.minidom as m; [m.parse(p) for p in sys.argv[1:]]' \
  %{buildroot}%{_datadir}/dbus-1/system.d/org.projectluma.BackgroundWake1.conf \
  %{buildroot}%{_datadir}/polkit-1/actions/org.projectluma.background.policy
grep -qx 'SystemdService=luma-background-wake.service' \
  %{buildroot}%{_datadir}/dbus-1/system-services/org.projectluma.BackgroundWake1.service

%post
%systemd_user_post luma-background.service
%systemd_post luma-background-wake.service

%preun
%systemd_user_preun luma-background.service
%systemd_preun luma-background-wake.service

%postun
%systemd_postun luma-background-wake.service

%files
%license LICENSE
%{python3_sitelib}/luma_background/
%{_libexecdir}/luma-background-service
%{_bindir}/luma-background
%{_userunitdir}/luma-background.service
%{_userunitdir}/luma-background.slice
%{_userunitdir}/luma-background-essential.slice
%{_userunitdir}/luma-background-deferrable.slice
%{_userpresetdir}/80-luma-background.preset
%{_datadir}/dbus-1/services/org.projectluma.Background1.service
%{_datadir}/dbus-1/services/org.freedesktop.impl.portal.desktop.luma.background.service
%{_datadir}/xdg-desktop-portal/portals/luma-background.portal
%dir %{_datadir}/luma-background
%{_datadir}/luma-background/defaults.toml
%dir %{_datadir}/luma
%dir %{_datadir}/luma/background
%dir %{_sysconfdir}/luma-background
%{_datadir}/applications/org.projectluma.Background.desktop
%dir %{_datadir}/gnome
%dir %{_datadir}/gnome/autostart
%{_datadir}/gnome/autostart/org.projectluma.*.Agent.desktop
%{_libexecdir}/luma-background-wake
%{_unitdir}/luma-background-wake.service
%{_datadir}/dbus-1/system.d/org.projectluma.BackgroundWake1.conf
%{_datadir}/dbus-1/system-services/org.projectluma.BackgroundWake1.service
%{_datadir}/polkit-1/actions/org.projectluma.background.policy

%changelog
* Wed Oct 07 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.5
- Keep the four native core services behind independently updated, signed UIs; retain trusted host declarations and capped managed units
- Interface version 3 provides authenticated foreground leases without changing background decisions; disconnect, close and explicit stop release them

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- GNOME's session no longer starts the fallback autostart entries of Calendar, Charlie, Luma Connect, Messages and Phone agents that this service runs: hidden entries in gnome/autostart stand in front of them, so no scope fails at every login; without this package the fallbacks start as before
* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Open at Login (interface version 2): ListLoginItems, GetLoginItem, SetLoginItem and LoginItemsChanged give one answer per app from the person's and the system's XDG autostart entries, the Background portal's entries and agents, read as systemd-xdg-autostart-generator reads them
- Turning an app off writes the standard Hidden=true override in the person's folder and never touches system files; turning it on removes that override or writes an entry from the app's own desktop entry
- For an app whose portal autostart entry this service runs as its agent, the switch is the agent's allow decision, so the dock shows one control for it

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Charlie is on by default, like Luma's other communication apps
- An app whose agent the service runs is no longer started a second time by the session: the portal's autostart entry for it and any autostart entry naming its agent (X-Luma-Background-Agent, in the person's or the system's folder) stay masked while the agent is managed
- Units and those masks are loaded before the service reports ready, so the session's autostart can no longer run ahead of them at login
- org.projectluma.BackgroundWake1: the active session can wake a suspended computer for an alarm; polkit-guarded, eight wake-ups per person, armed by systemd as transient WakeSystem timers

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First release: the background activity service of ADR-033
