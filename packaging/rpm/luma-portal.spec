# SPDX-License-Identifier: Apache-2.0

Name:           luma-portal
Version:        0.1.0
Release:        1.luma.9%{?dist}
Summary:        Project Luma's desktop portal backend
License:        Apache-2.0
URL:            https://projectluma.org/platform/portal
Source0:        luma-portal.tar.gz
Source1:        LICENSE.md

BuildArch:      noarch
BuildRequires:  python3-devel >= 3.11
BuildRequires:  systemd-rpm-macros
Requires:       python3 >= 3.11
Requires:       python3-gobject
Requires:       gtk4 >= 4.18
Requires:       libadwaita >= 1.7
Requires:       luma-developer-platform
Requires:       xdg-desktop-portal

%description
Luma's portal backend. It implements two interfaces. AppChooser is the dialog shown when a file is
to be opened with something other than its default application. ScreenCast
answers "share my screen": it asks the Shell to show Luma's picker, with live
previews of every window and screen, and then creates the stream through
Mutter exactly as GNOME's backend does (ADR-041).

Built as the portal rather than as a dialog the file manager owns, so a
sandboxed application asking OpenURI to choose gets the same chooser; the file
manager is then one caller among several instead of the only surface that can
show it.

This package makes Luma's chooser the one that answers AppChooser requests,
and Luma's picker the one that answers screen sharing requests, through the
gnome-portals.conf it installs. That exact filename is what
xdg-desktop-portal looks for first under GNOME, and the first match wins
outright, so the file restates the upstream defaults alongside the chooser
rather than relying on them being inherited.

%prep
%autosetup -n luma-portal
cp %{SOURCE1} LICENSE.md

%build

%install
install -D -m 0644 data/luma-portal-appchooser.service \
  %{buildroot}%{_userunitdir}/luma-portal-appchooser.service
install -D -m 0644 data/luma-portal-appchooser.preset \
  %{buildroot}%{_userpresetdir}/80-luma-portal.preset
install -D -m 0755 bin/luma-portal-appchooser %{buildroot}%{_libexecdir}/luma-portal-appchooser
install -d -m 0755 %{buildroot}%{python3_sitelib}/luma_portal
install -m 0644 luma_portal/*.py %{buildroot}%{python3_sitelib}/luma_portal/
install -D -m 0644 data/luma.portal \
  %{buildroot}%{_datadir}/xdg-desktop-portal/portals/luma.portal
install -D -m 0644 data/org.freedesktop.impl.portal.desktop.luma.service \
  %{buildroot}%{_datadir}/dbus-1/services/org.freedesktop.impl.portal.desktop.luma.service
install -D -m 0644 data/open-with.css %{buildroot}%{_datadir}/luma-portal/open-with.css
install -D -m 0644 data/luma-appchooser.conf \
  %{buildroot}%{_sysconfdir}/xdg-desktop-portal/gnome-portals.conf

%check
PYTHONPATH=$PWD %{python3} -m unittest discover -s tests -v
PYTHONPATH=$PWD %{python3} -m py_compile luma_portal/*.py
# The configuration is only read if it owns the name GNOME looks for, and the
# picker is only reached if that file claims the interface. Both have been
# wrong before; neither is visible at runtime until someone tries to share.
grep -Fqx 'org.freedesktop.impl.portal.ScreenCast=luma;gnome;' \
  data/luma-appchooser.conf
grep -Fq 'org.freedesktop.impl.portal.ScreenCast' data/luma.portal
grep -Fq 'org.freedesktop.impl.portal.AppChooser' data/luma.portal
# Nothing may reach the picker except through the Shell's own interface, and
# nothing may stream before Share: the Mutter session is created in Start.
grep -Fq 'org.projectluma.Shell.ScreenShare' luma_portal/screencast.py
grep -Fq 'is-recording' luma_portal/mutter.py

%post
%systemd_user_post luma-portal-appchooser.service

%preun
%systemd_user_preun luma-portal-appchooser.service

%files
%license LICENSE.md
%{_userunitdir}/luma-portal-appchooser.service
%{_userpresetdir}/80-luma-portal.preset
%{_libexecdir}/luma-portal-appchooser
%{python3_sitelib}/luma_portal/
%{_datadir}/xdg-desktop-portal/portals/luma.portal
%{_datadir}/dbus-1/services/org.freedesktop.impl.portal.desktop.luma.service
%{_datadir}/luma-portal/open-with.css
%config(noreplace) %{_sysconfdir}/xdg-desktop-portal/gnome-portals.conf

%changelog
* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.9
- Answer screen sharing requests, so an application asking to share the
  screen gets Luma's picker with live previews of every window and screen
  instead of a list of window names
- Create the stream only once Share is pressed, and tell the application only
  about the sources that were chosen
- Honour a restore token only for the application it was issued to

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.8
- Prefer luma-background for the Background portal interface only, so a
  sandboxed app's background activity follows the same decisions as Settings
  and the dock; GNOME's backend still answers when luma-background is absent

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.7
- Claim the bus name before importing Adw. Importing it brings in Gdk, which
  asks the Settings portal for the colour scheme, which is served by the
  process waiting for this name: each waited 25 seconds for the other, the
  chooser was never reached, and every application asking the portal for its
  colour scheme stalled for those 25 seconds after a login
- Activate on demand rather than with the graphical session

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.6
- Speak app ids, not desktop file names, in both directions: the choices the
  caller offers now resolve, and the choice answered with actually opens
- Attach the chooser to the window that asked for it, so a portal backend
  stops appearing in the dock as a running application

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.5
- Skip desktop ids that name nothing installed, which raised out of
  ChooseApplication and left the chooser unshown and the caller unanswered

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- Let systemd own the backend as a Type=dbus unit in the graphical session,
  so a cold login cannot miss the frontend's one activation window

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Claim the bus name before initialising GTK, so activation no longer times
  out and the frontend stops falling back to the GNOME chooser
- Install the configuration as gnome-portals.conf, the name GNOME actually
  reads, restating the upstream defaults it now shadows

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Give the dialog a height, so the list is not one clipped row
- Focus reads as the accent rather than a warning
- Prefer this chooser, now that it has been seen rendered

* Sat Sep 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First packaging of the application chooser portal backend
