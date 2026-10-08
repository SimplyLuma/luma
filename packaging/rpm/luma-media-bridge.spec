# SPDX-License-Identifier: Apache-2.0

Name:           luma-media-bridge
Version:        0.1.0
Release:        1.luma.2%{?dist}
Summary:        Players without MPRIS on the desktop's media controls
License:        Apache-2.0
URL:            https://projectluma.org/media
Source0:        luma-media-bridge.tar.gz
Source1:        LICENSE.md

BuildArch:      noarch
BuildRequires:  meson >= 1.3
BuildRequires:  ninja-build
BuildRequires:  python3-devel >= 3.11
BuildRequires:  python3-gobject-base
BuildRequires:  systemd-rpm-macros
Requires:       python3 >= 3.11
Requires:       python3-gobject-base

%description
Luma Media Bridge publishes media players that have their own local control
interface but no MPRIS, such as Kodi, as ordinary MPRIS players. The shelf's
live island, media keys and Ari then show and control them like any other
player. It connects only to players on this computer, and artwork comes from
the player's own local cache.

%prep
%autosetup -n luma-media-bridge
cp %{SOURCE1} LICENSE.md

%build
%meson
%meson_build

%install
%meson_install
# Enabled by the package itself: a preset only applies on first install.
install -d %{buildroot}%{_userunitdir}/graphical-session.target.wants
ln -s ../luma-media-bridge.service %{buildroot}%{_userunitdir}/graphical-session.target.wants/luma-media-bridge.service
install -D -m 0644 data/mpris-proxy-session-only.conf \
  %{buildroot}%{_userunitdir}/mpris-proxy.service.d/10-luma-session-only.conf

%check
PYTHONPATH=$PWD %{python3} -m unittest discover -s tests -p "test_*.py" -v

%files
%license LICENSE.md
%{_libexecdir}/luma-media-bridge
%{python3_sitelib}/luma_media_bridge/
%{_userunitdir}/luma-media-bridge.service
%{_userunitdir}/graphical-session.target.wants/luma-media-bridge.service
%dir %{_userunitdir}/mpris-proxy.service.d
%{_userunitdir}/mpris-proxy.service.d/10-luma-session-only.conf

%changelog
* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Do not start the bridge or BlueZ's mpris-proxy in the login screen's greeter session
* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First bridge: Kodi, through its local JSON-RPC connection, with playback, seeking and cached artwork
