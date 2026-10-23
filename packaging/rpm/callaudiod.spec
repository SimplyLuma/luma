# Fedora's callaudiod 0.1.99-3 with Luma patches: speaker on/off switches voice
# call profiles on cards whose speaker and earpiece are separate sinks, and the
# daemon survives sinks without an active port and early requests.
Name:       callaudiod
Version:    0.1.99
Release:    3.luma3%{?dist}
Summary:    Daemon for dealing with audio routing during phone calls

License:        GPL-3.0-or-later
URL:            https://gitlab.com/mobian1/callaudiod
Source0:        https://gitlab.com/mobian1/callaudiod/-/archive/%{version}/%{name}-%{version}.tar.gz
Patch0:         0001-pulse-switch-voice-call-profiles-for-the-speaker.patch
Patch1:         0002-pulse-tolerate-sinks-without-an-active-port.patch
Patch2:         0003-pulse-hold-mode-requests-until-the-card-is-known.patch

BuildRequires:  gcc
BuildRequires:  gcc-c++
BuildRequires:  meson
BuildRequires:  appstream-devel

BuildRequires:  pkgconfig(alsa)
BuildRequires:  pkgconfig(gobject-2.0)
BuildRequires:  pkgconfig(gio-unix-2.0)
BuildRequires:  pkgconfig(libpulse)
BuildRequires:  pkgconfig(libpulse-mainloop-glib)

%description
callaudiod is a daemon for dealing with audio routing during phone calls.
It provides a D-Bus interface allowing other programs to:

switch audio profiles
output audio to the speaker or back to its original port
mute the microphone


%package devel
Summary: Development files for %{name}
Requires: %{name}%{?_isa} = %{version}-%{release}

%description devel
The %{name}-devel package contains libraries and header files for
developing applications that use %{name}.

%prep
%autosetup -n %{name}-%{version} -p1

%build
%meson
%meson_build

%install
%meson_install

%files
%{_bindir}/%{name}
%{_bindir}/callaudiocli
%dir %{_includedir}/libcallaudio-0.1
%{_libdir}/libcallaudio-0.1.so.0
%{_datadir}/dbus-1/interfaces/org.mobian_project.CallAudio.xml
%{_datadir}/dbus-1/services/org.mobian_project.CallAudio.service

%files devel
%{_includedir}/libcallaudio-0.1/libcallaudio.h
%{_includedir}/libcallaudio-0.1/libcallaudio-enums.h
%{_libdir}/libcallaudio-0.1.so
%{_libdir}/pkgconfig/libcallaudio-0.1.pc

%doc README.md
%license COPYING

%changelog
* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.99-3.luma3
- Hold mode requests that arrive before the card is known (D-Bus activation).

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.99-3.luma2
- Do not crash on sinks without an active port (profile switches, start-up);
  answer D-Bus calls when a card or sink query fails.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.99-3.luma1
- Switch voice call profiles for the speaker when the active sink has no
  speaker (or earpiece) port: UCM devices sharing a PCM get separate profiles.

* Fri Jan 16 2026 Fedora Release Engineering <releng@fedoraproject.org> - 0.1.99-3
- Rebuilt for https://fedoraproject.org/wiki/Fedora_44_Mass_Rebuild

* Thu Sep 04 2025 Tomi Lähteenmäki <lihis@lihis.net> - 0.1.99-1
- Upstream release 0.1.99 (fedora#2392935)
