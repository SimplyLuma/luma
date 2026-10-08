# SPDX-License-Identifier: Apache-2.0

Name:           luma-greeter
Version:        0.1.0
Release:        1.luma.15%{?dist}
Summary:        Project Luma native handheld Presence greeter
License:        Apache-2.0
URL:            https://projectluma.org/
Source0:        luma-greeter.c
Source1:        luma-greeter.css
Source2:        README.md
Source3:        LICENSE.md
Source4:        luma-greeter-session.c
Source5:        30-luma-greetd-vt.conf
Source6:        luma-presence-compositor.c
Source7:        luma-display.sysusers
Source8:        luma-display.tmpfiles
Source9:        luma-phosh-client-session
Source10:       60-luma-greeter-brightness.rules

BuildRequires:  gcc
BuildRequires:  gtk4-devel
BuildRequires:  json-glib-devel
BuildRequires:  pkgconfig
BuildRequires:  systemd-rpm-macros
Requires:       google-figtree-fonts
Requires:       greetd >= 0.10.3
Requires:       luma-greetd-session >= 0.10.3-1.luma.1
Requires:       gtk4
Requires:       json-glib
Requires:       luma-backgrounds >= 0.1.0-1.luma.3
Requires:       luma-phosh >= 0.55.0-1.luma.6
Requires:       phoc >= 0.55.1-2.luma.3
Requires:       wlr-randr

%description
Luma's source-owned pre-session Presence presentation for handheld devices.
It authenticates through greetd and PAM, reads the selected identity from the
host account service, and contains no credential store, web view, user-session
overlay, or Android dependency.

%prep
cp %{SOURCE0} luma-greeter.c
cp %{SOURCE1} luma-greeter.css
cp %{SOURCE2} README.md
cp %{SOURCE3} LICENSE.md
cp %{SOURCE4} luma-greeter-session.c
cp %{SOURCE5} 30-luma-greetd-vt.conf
cp %{SOURCE6} luma-presence-compositor.c
cp %{SOURCE7} luma-display.sysusers
cp %{SOURCE8} luma-display.tmpfiles
cp %{SOURCE9} luma-phosh-client-session
cp %{SOURCE10} 60-luma-greeter-brightness.rules

%build
%{__cc} %{build_cflags} %{build_ldflags} -Werror -Wall -Wextra \
  -o luma-greeter luma-greeter.c \
  $(pkg-config --cflags --libs gtk4 json-glib-1.0)
%{__cc} %{build_cflags} %{build_ldflags} -Werror -Wall -Wextra \
  -o luma-greeter-session luma-greeter-session.c
%{__cc} %{build_cflags} %{build_ldflags} -Werror -Wall -Wextra \
  -o luma-presence-compositor luma-presence-compositor.c

%install
install -D -m 0755 luma-greeter %{buildroot}%{_bindir}/luma-greeter
install -D -m 0755 luma-greeter-session \
  %{buildroot}%{_libexecdir}/luma-greeter-session
install -D -m 0755 luma-presence-compositor \
  %{buildroot}%{_libexecdir}/luma-presence-compositor
install -D -m 0755 luma-phosh-client-session \
  %{buildroot}%{_libexecdir}/luma-phosh-client-session
install -D -m 0644 luma-greeter.css \
  %{buildroot}%{_datadir}/luma-greeter/luma-greeter.css
install -D -m 0644 README.md %{buildroot}%{_pkgdocdir}/README.md
install -D -m 0644 30-luma-greetd-vt.conf \
  %{buildroot}%{_unitdir}/greetd.service.d/30-luma-greetd-vt.conf
install -D -m 0644 luma-display.sysusers \
  %{buildroot}%{_sysusersdir}/luma-display.conf
install -D -m 0644 luma-display.tmpfiles \
  %{buildroot}%{_tmpfilesdir}/luma-display.conf
install -D -m 0644 60-luma-greeter-brightness.rules \
  %{buildroot}%{_datadir}/polkit-1/rules.d/60-luma-greeter-brightness.rules

%check
test -x %{buildroot}%{_bindir}/luma-greeter
test -x %{buildroot}%{_libexecdir}/luma-greeter-session
test -x %{buildroot}%{_libexecdir}/luma-presence-compositor
test -x %{buildroot}%{_libexecdir}/luma-phosh-client-session
test -s %{buildroot}%{_datadir}/luma-greeter/luma-greeter.css
test -s %{buildroot}%{_unitdir}/greetd.service.d/30-luma-greetd-vt.conf
grep -Fq 'd /run/luma-display 2770 greetd luma-display -' luma-display.tmpfiles
grep -Fq 'LUMA_HOME_READY' luma-greeter.c
grep -Fq '_exit(EXIT_SUCCESS)' luma-greeter.c
grep -Fq 'LUMA_IDLE_SECONDS 30' luma-greeter.c
grep -Fq 'org.freedesktop.login1.set-brightness' \
  60-luma-greeter-brightness.rules

%files
%license LICENSE.md
%doc %{_pkgdocdir}/README.md
%{_bindir}/luma-greeter
%{_libexecdir}/luma-greeter-session
%{_libexecdir}/luma-presence-compositor
%{_libexecdir}/luma-phosh-client-session
%{_datadir}/luma-greeter/
%{_unitdir}/greetd.service.d/30-luma-greetd-vt.conf
%{_sysusersdir}/luma-display.conf
%{_tmpfilesdir}/luma-display.conf
%{_datadir}/polkit-1/rules.d/60-luma-greeter-brightness.rules

%changelog
* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.15
- Declare only the session type. Supplying XDG_VTNR made logind treat the new
  session as the seat's owner, activate it, and pause the running compositor's
  DRM and input devices mid-handoff. The seat now stays with the compositor;
  greetd gives the authenticated session no seat at all.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.14
- Supply XDG_VTNR and XDG_SESSION_TYPE when starting the authenticated session.
  greetd runs that session in a terminal mode that never touches the VT the
  persistent compositor owns, so it set neither PAM_TTY nor XDG_VTNR while
  still exporting XDG_SEAT=seat0. logind rejects a session on a seat that has
  VTs unless vtnr is 1..63, so CreateSession failed with InvalidParameter, the
  account got no runtime directory, the session aborted on its own missing-bus
  guard, and greetd tore down the compositor and restarted into the greeter.

* Mon Aug 31 2026 Project Luma <builds@projectluma.org> - 0.1.0-1.luma.13
- Blank idle pre-session Presence through narrowly authorized logind brightness control

* Sun Aug 30 2026 Project Luma <builds@projectluma.org> - 0.1.0-1.luma.12
- Keep Presence visually stable while authenticated Home assembles

* Sun Aug 30 2026 Project Luma <builds@projectluma.org> - 0.1.0-1.luma.11
- Make persistent Wayland sockets inherit the authenticated display group

* Sun Aug 30 2026 Project Luma <builds@projectluma.org> - 0.1.0-1.luma.10
- Bind the authenticated Phosh client to Luma's systemd user bus

* Sun Aug 30 2026 Project Luma <builds@projectluma.org> - 0.1.0-1.luma.9
- Assign the image-created Luma account to display access during composition

* Sun Aug 30 2026 Project Luma <builds@projectluma.org> - 0.1.0-1.luma.8
- Use Fedora's packaged Phoc configuration path

* Sun Aug 30 2026 Project Luma <builds@projectluma.org> - 0.1.0-1.luma.7
- Keep Presence and authenticated Home on one persistent compositor

* Sun Aug 30 2026 Project Luma <builds@projectluma.org> - 0.1.0-1.luma.6
- Paint a final wallpaper-only frame before the authenticated compositor handoff

* Sun Aug 30 2026 Project Luma <builds@projectluma.org> - 0.1.0-1.luma.5
- Keep compositor diagnostics off the product VT during session handoff

* Sun Aug 30 2026 Project Luma <builds@projectluma.org> - 0.1.0-1.luma.4
- Start the authenticated renderer directly and reserve a quiet display VT

* Sun Aug 30 2026 Project Luma <builds@projectluma.org> - 0.1.0-1.luma.3
- Enforce a square avatar allocation and center the quiet unlock hint

* Sun Aug 30 2026 Project Luma <builds@projectluma.org> - 0.1.0-1.luma.2
- Set the FP6 logical panel scale through native compositor initialization

* Sun Aug 30 2026 Project Luma <builds@projectluma.org> - 0.1.0-1.luma.1
- Introduce the native greetd/PAM-backed handheld Presence greeter
