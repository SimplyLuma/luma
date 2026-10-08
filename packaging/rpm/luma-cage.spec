# SPDX-License-Identifier: MIT

Name:           cage
Version:        0.3.1
Release:        1.luma.1%{?dist}
Summary:        Luma scanout-safe Wayland kiosk
License:        MIT
URL:            https://github.com/cage-kiosk/cage
Source0:        cage-0.3.1.tar.gz
Patch0:         0001-luma-preserve-authenticated-scanout.patch

BuildRequires:  gcc
BuildRequires:  meson
BuildRequires:  pkgconfig(scdoc)
BuildRequires:  pkgconfig(wlroots-0.20)
BuildRequires:  pkgconfig(wayland-protocols) >= 1.14
BuildRequires:  pkgconfig(wayland-server)
BuildRequires:  pkgconfig(xkbcommon)

%description
Fedora Cage with one explicitly gated Project Luma behavior: preserve the
final Presence scanout while the authenticated Phoc compositor takes the same
handheld DRM output. All unmarked Cage sessions retain upstream cleanup.

%prep
%autosetup -p1

%build
%meson
%meson_build

%install
%meson_install

%check
grep -Fq 'LUMA_CAGE_SCANOUT_HANDOFF' cage.c
grep -Fq '_exit(ret)' cage.c

%files
%license LICENSE
%doc README.md
%{_bindir}/cage
%{_mandir}/man1/cage.1.*

%changelog
* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.3.1-1.luma.1
- Preserve the final Presence scanout during the authenticated compositor swap
