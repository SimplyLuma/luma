# SPDX-License-Identifier: GPL-3.0-only
#
# Viola for Luma: the signed Viola 0.2.10 engine with the native-integration
# platform patches, driven by the native GTK host (chromium-linux/appkit-spike).
# Release 30 of the native integration is the build Nick runs on his ThinkPad
# (20260921-native-30, chromium-linux/releases/native30). Source0 comes from
# assemble-native-engine.sh, which verifies every engine file against
# native-30-engine.sha256.

%global viola_version 0.2.10
%global viola_build 8210
%global native_release 35
# Native release 30's own engine (released already stripped).
%global engine_release 30
%global approved_release 20260921-native-30
%global engine_sha256 d6a87f43aff38963678dcfd0f89223fdc798193745dac99df4882c03938feff1
%global source_commit %{?viola_commit}%{!?viola_commit:unknown}

# Prebuilt Chromium engine: no debuginfo split and no automatic stripping (the
# release already stripped it); keep its private libraries out of the
# RPM dependency graph so they never satisfy or demand system libraries.
%global debug_package %{nil}
%global __strip /bin/true
%global _build_id_links none
%global __provides_exclude_from ^/opt/viola/.*$
%global __requires_exclude ^(libQt5.*|libQt6.*)$
# The engine stays byte-identical to the approved release (its wrapper keeps #!/bin/bash).
%global __brp_mangle_shebangs_exclude_from ^/opt/viola/.*$

Name:           viola-browser-stable
Version:        151.0.7922.72
Release:        13.viola%{viola_build}.native%{native_release}.creator20261008.1%{?dist}
Summary:        Viola web browser
License:        GPL-3.0-only AND BSD-3-Clause AND LGPL-2.1-or-later AND Apache-2.0 AND IJG AND MIT AND GPL-2.0-or-later AND ISC AND OpenSSL AND (MPL-1.1 OR GPL-2.0-only OR LGPL-2.0-only)
URL:            https://github.com/nmcmil/Viola
ExclusiveArch:  x86_64

Source0:        viola-native-engine-%{viola_version}-native%{engine_release}.tar.zst
Source1:        viola-luma-host-%{viola_version}-native%{native_release}.tar.gz

BuildRequires:  zstd
BuildRequires:  desktop-file-utils
BuildRequires:  librsvg2-tools
BuildRequires:  python3
BuildRequires:  systemd-rpm-macros
BuildRequires:  python3-gobject
BuildRequires:  gtk4
BuildRequires:  libadwaita
BuildRequires:  luma-application-installer >= 0.1.0-1.luma.61
BuildRequires:  luma-developer-platform
BuildRequires:  gstreamer1-plugins-bad-free-libs
# The host's widget tests need a display: a headless Weston.
BuildRequires:  weston
BuildRequires:  dbus-daemon
# The engine's own libraries, so %%check can run it.
BuildRequires:  nss
BuildRequires:  gtk3
BuildRequires:  alsa-lib
BuildRequires:  cups-libs
BuildRequires:  mesa-libgbm
BuildRequires:  libXcomposite
BuildRequires:  libXdamage
BuildRequires:  libxkbcommon
BuildRequires:  at-spi2-atk
BuildRequires:  vulkan-loader

Requires:       python3
Requires:       python3-gobject
Requires:       gtk4
# Chromium hidden native windows use the supported GTK3 backend.
Requires:       gtk3
# Menu headings, shortcut chips and disabled rows take solid inks (libadwaita 0039).
Requires:       libadwaita >= 1.9.3-1.luma.48
Requires:       luma-developer-platform >= 0.1.0-1.luma.102
Requires:       luma-application-installer >= 0.1.0-1.luma.61
Requires:       mutter
Requires:       dbus-broker
Requires:       systemd
Requires:       gstreamer1-plugins-bad-free-libs
Requires:       xdg-utils
Requires:       ca-certificates
Requires:       liberation-fonts
Provides:       viola-browser = %{version}-%{release}

%description
Viola, Luma's web browser. Luma's native window, toolbar, sidebar and menus
drive Viola's Chromium engine, which keeps browser state, profiles, extensions,
downloads and sandboxing. This build is Viola %{viola_version} (build
%{viola_build}) with native integration release %{native_release}.

%prep
%setup -q -c -T
zstd -dc %{SOURCE0} | tar -xf -
mkdir host
tar -xzf %{SOURCE1} -C host --strip-components=1
# Every engine and host file must be the approved release's.
(cd engine && sha256sum -c --quiet ../host/luma-package/native-%{engine_release}-engine.sha256)
(cd host/appkit-spike && sha256sum -c --quiet ../luma-package/native-%{native_release}-host.sha256)

%build
# The engine remains the admitted prebuilt release. Render the maintained
# locked SVG into standard hicolor PNG sizes as part of the normal build.
mkdir -p host/appkit-spike/assets/viola-icon
for size in 16 24 32 48 64 128 256 512 1024; do
  rsvg-convert --width "$size" --height "$size" \
    host/appkit-spike/assets/org.projectluma.Viola.NativeIntegration.svg \
    > "host/appkit-spike/assets/viola-icon/viola-$size.png"
done

%install
engine=%{buildroot}/opt/viola/browser
install -d %{buildroot}/opt/viola
mv engine "$engine"
mv "$engine/chrome-sandbox.packaged" "$engine/chrome-sandbox"
chmod 4755 "$engine/chrome-sandbox"
install -d %{buildroot}%{_bindir}
install -m 0755 host/luma-package/viola-browser %{buildroot}%{_bindir}/viola-browser
install -D -m 0644 host/luma-package/org.projectluma.ViolaHost1.service \
  %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.ViolaHost1.service
ln -s /opt/viola/browser/viola-browser %{buildroot}%{_bindir}/viola-browser-stable

# Flatpak mounts xdg-run/luma-viola read-only only if the parent exists already.
# Normal user tmpfiles creates it before dbus.socket allows first activation.
install -D -m 0644 host/luma-package/luma-viola-user.conf \
  %{buildroot}%{_user_tmpfilesdir}/luma-viola.conf
install -D -m 0644 host/luma-package/luma-viola-dbus-socket.conf \
  %{buildroot}%{_userunitdir}/dbus.socket.d/50-luma-viola-runtime.conf

# Exactly the approved host files; menus and toolkit follow the installed
# Luma libadwaita and GTK (no private resource overlay since release 23).
install -d %{buildroot}%{_datadir}/viola-browser/luma-host
(cd host/appkit-spike && cut -c67- ../luma-package/native-%{native_release}-host.sha256 |
  xargs -d '\n' cp --parents -t %{buildroot}%{_datadir}/viola-browser/luma-host/)
find %{buildroot}%{_datadir}/viola-browser -type d -exec chmod 0755 {} +
find %{buildroot}%{_datadir}/viola-browser -type f -exec chmod 0644 {} +

install -d %{buildroot}%{_datadir}/applications
desktop-file-install --dir=%{buildroot}%{_datadir}/applications host/luma-package/viola-browser.desktop
desktop-file-install --dir=%{buildroot}%{_datadir}/applications host/luma-package/com.rhyme.viola.desktop
for size in 16 24 32 48 64 128 256 512 1024; do
  install -D -m 0644 "host/appkit-spike/assets/viola-icon/viola-$size.png" \
    %{buildroot}%{_datadir}/icons/hicolor/${size}x${size}/apps/viola-browser.png
  ln -s viola-browser.png %{buildroot}%{_datadir}/icons/hicolor/${size}x${size}/apps/com.rhyme.viola.png
done
install -D -m 0644 host/appkit-spike/assets/org.projectluma.Viola.NativeIntegration.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/viola-browser.svg
for name in com.rhyme.viola org.projectluma.Viola.NativeIntegration; do
  ln -s viola-browser.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/$name.svg
done
cp -a system/usr/share/appdata system/usr/share/gnome-control-center system/usr/share/man \
  %{buildroot}%{_datadir}/
install -d %{buildroot}%{_datadir}/licenses/%{name}
cp -a system/usr/share/licenses/viola-browser-stable/. %{buildroot}%{_datadir}/licenses/%{name}/

python3 - %{buildroot} <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
chrome = root / 'opt/viola/browser/chrome'
record = {
    'viola_version': '%{viola_version}', 'viola_build': %{viola_build},
    'native_release': %{native_release}, 'approved_release': '%{approved_release}', 'engine_release': %{engine_release},
    'source': {'repository': 'https://github.com/nmcmil/Viola', 'commit': '%{source_commit}'},
    'engine_released_sha256': '%{engine_sha256}',
    'engine_packaged_sha256': hashlib.sha256(chrome.read_bytes()).hexdigest(),
}
(root / 'usr/share/viola-browser/provenance.json').write_text(json.dumps(record, indent=2) + '\n')
PY

%check
engine_manifest=$PWD/host/luma-package/native-%{engine_release}-engine.sha256
host_manifest=$PWD/host/luma-package/native-%{native_release}-host.sha256
host=%{buildroot}%{_datadir}/viola-browser/luma-host
# The installed host is exactly the approved one: every file and nothing else.
(cd "$host" && sha256sum -c --quiet "$host_manifest")
test "$(find "$host" -type f | wc -l)" = "$(wc -l <"$host_manifest")"
# The host's unit tests (the approved files plus test-only modules), on a headless Weston.
# (Wayland socket paths are limited to 108 bytes, so not below the build tree.)
export XDG_RUNTIME_DIR=$(mktemp -d /tmp/viola-check.XXXXXX)
weston --backend=headless --socket=viola-check --idle-time=0 >"$PWD/check-weston.log" 2>&1 &
weston_pid=$!
for _ in $(seq 100); do [ -S "$XDG_RUNTIME_DIR/viola-check" ] && break; sleep 0.1; done
[ -S "$XDG_RUNTIME_DIR/viola-check" ] || { tail -n 5 "$PWD/check-weston.log"; exit 1; }
# Tests corrected after the release was cut (luma-package/check-tests) replace
# their released copies for %%check only; the installed host stays the release's.
rm -rf check-host
cp -a host/appkit-spike check-host
cp host/luma-package/check-tests/test_*.py check-host/
status=0
(cd check-host && WAYLAND_DISPLAY=viola-check GDK_BACKEND=wayland GSK_RENDERER=cairo PYTHONDONTWRITEBYTECODE=1 \
  dbus-run-session -- python3 -m unittest discover -p 'test_*.py') || status=$?
kill "$weston_pid" 2>/dev/null || true
wait "$weston_pid" 2>/dev/null || true
rm -rf "$XDG_RUNTIME_DIR"
[ "$status" = 0 ]
desktop-file-validate %{buildroot}%{_datadir}/applications/viola-browser.desktop
desktop-file-validate %{buildroot}%{_datadir}/applications/com.rhyme.viola.desktop
grep -qx 'NoDisplay=true' %{buildroot}%{_datadir}/applications/com.rhyme.viola.desktop
bash -n %{buildroot}%{_bindir}/viola-browser
# Validate shipped native startup inputs; actual new-UID ordering is a runtime gate.
cmp host/luma-package/luma-viola-user.conf %{buildroot}%{_user_tmpfilesdir}/luma-viola.conf
cmp host/luma-package/luma-viola-dbus-socket.conf %{buildroot}%{_userunitdir}/dbus.socket.d/50-luma-viola-runtime.conf
cmp host/appkit-spike/assets/org.projectluma.Viola.NativeIntegration.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/viola-browser.svg
for size in 16 24 32 48 64 128 256 512 1024; do
  cmp host/appkit-spike/assets/viola-icon/viola-$size.png \
    %{buildroot}%{_datadir}/icons/hicolor/${size}x${size}/apps/viola-browser.png
done
# Menus take surface and ink from the installed toolkit: no private overlay.
test ! -e %{buildroot}%{_datadir}/viola-browser/toolkit
grep -qx 'unset G_RESOURCE_OVERLAYS' %{buildroot}%{_bindir}/viola-browser
! grep -q 'G_RESOURCE_OVERLAYS=' %{buildroot}%{_bindir}/viola-browser
# The engine runs and reports its version.
%{buildroot}/opt/viola/browser/chrome --version | grep -F '%{version}'
# The whole engine, executable included, is byte-identical to the released one
# (the SUID helper is installed under its final name), and nothing else is there.
test "$(sha256sum <%{buildroot}/opt/viola/browser/chrome | cut -c1-64)" = '%{engine_sha256}'
grep -qx '%{engine_sha256}  chrome' "$engine_manifest"
(cd %{buildroot}/opt/viola/browser &&
  sed 's/  chrome-sandbox\.packaged$/  chrome-sandbox/' "$engine_manifest" | sha256sum -c --quiet)
test "$(find %{buildroot}/opt/viola/browser -type f | wc -l)" = "$(wc -l <"$engine_manifest")"
test -u %{buildroot}/opt/viola/browser/chrome-sandbox
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["engine_packaged_sha256"] == r["engine_released_sha256"] == "%{engine_sha256}", r' \
  %{buildroot}%{_datadir}/viola-browser/provenance.json

%files
%dir %{_userunitdir}/dbus.socket.d
%{_user_tmpfilesdir}/luma-viola.conf
%{_userunitdir}/dbus.socket.d/50-luma-viola-runtime.conf
%{_datadir}/dbus-1/services/org.projectluma.ViolaHost1.service
%license %{_datadir}/licenses/%{name}
%dir /opt/viola
/opt/viola/browser
%{_bindir}/viola-browser
%{_bindir}/viola-browser-stable
%{_datadir}/viola-browser
%{_datadir}/applications/viola-browser.desktop
%{_datadir}/applications/com.rhyme.viola.desktop
%{_datadir}/icons/hicolor/*/apps/viola-browser.png
%{_datadir}/icons/hicolor/*/apps/com.rhyme.viola.png
%{_datadir}/icons/hicolor/scalable/apps/viola-browser.svg
%{_datadir}/icons/hicolor/scalable/apps/com.rhyme.viola.svg
%{_datadir}/icons/hicolor/scalable/apps/org.projectluma.Viola.NativeIntegration.svg
%{_datadir}/appdata/viola-browser.appdata.xml
%{_datadir}/gnome-control-center/default-apps/viola-browser.xml
%{_mandir}/man1/viola-browser.1*
%{_mandir}/man1/viola-browser-stable.1*

%changelog
* Thu Oct 08 2026 Nick McMillen <nmcmil@users.noreply.github.com> - 151.0.7922.72-13.viola8210.native35.creator20261008.1
- Restore the established hidden-engine GTK3 backend, guarantee its runtime dependency, and report abnormal engine termination while retaining the exact native30 engine.

* Thu Oct 08 2026 Nick McMillen <nmcmil@users.noreply.github.com> - 151.0.7922.72-12.viola8210.native34.creator20261008.1
- Create the owned private display parent during normal user startup before the signed browser sandbox mounts it read-only.

* Wed Oct 07 2026 Nick McMillen <nmcmil@users.noreply.github.com> - 151.0.7922.72-10.viola8210.native32.creator20261007.1
- Keep portable compositor lease cleanup idempotent after release or incomplete startup, preserving host wire interface31.

* Wed Oct 07 2026 Nick McMillen <nmcmil@users.noreply.github.com> - 151.0.7922.72-9.viola8210.native31.creator20261007.1
- Retain the approved native30 engine and provide a bounded signed portable UI compositor lease with caller-loss cleanup.
- Delegate native activation to the signed owner and use the portable application identity for the shared update monitor.

* Mon Oct 05 2026 Nick McMillen <nmcmil@users.noreply.github.com> - 151.0.7922.72-8.viola8210.native30.toolbar20261005.5
- Forward native touchscreen gestures to Chromium with bounded motion traffic.
- Give Mini the shared action bar and labelled primary Open in Viola action.

* Mon Oct 05 2026 Nick McMillen <nmcmil@users.noreply.github.com> - 151.0.7922.72-7.viola8210.native30.toolbar20261005.4
- Use shared raised pin cards and the darker inset address Well.
- Restore scroll-away page controls; bound and coalesce heavy-page wheel input.
- Add aggregate address input diagnostics without recording typed content.

* Mon Oct 05 2026 Nick McMillen <nmcmil@users.noreply.github.com> - 151.0.7922.72-7.viola8210.native30.toolbar20261005.4
- Shared floating page controls at desktop, compact and phone widths.
- Above-bar address suggestions and shared ShareSheet/Downloads.
- Toolkit inset workspace picker, raised active tabs and reversible top pins.
- Keep the title row free of the sidebar button and page title.
- Explicit GTK3 backend for hidden Chromium; visible host remains GTK4.
- Require the shared v71 AppKit rather than accepting an older toolkit.
- Ship the locked pointer-and-rings icon from Nick's October 4 handoff.

* Tue Sep 22 2026 Nick McMillen <nmcmil@users.noreply.github.com> - 151.0.7922.72-5.viola8210.native30
- Native integration release 30 as Nick runs it: the Skia capture completion
  crash is fixed, command timeouts no longer close every window, and the
  release 28 and 29 tab restore, wheel and clipboard fixes are included.
  The engine ships exactly as released (already stripped).

* Fri Sep 18 2026 Nick McMillen <nmcmil@users.noreply.github.com> - 151.0.7922.72-4.viola8210.native23
- Native integration release 23 as approved: zoom shortcuts, completed-download
  file actions, native select menus and paste, engine pipe response locking.
  Menus and toolkit follow the installed Luma appearance (no private overlay).

* Thu Sep 17 2026 Nick McMillen <nmcmil@users.noreply.github.com> - 151.0.7922.72-3.viola8210.native22
- App menus use the system appearance tokens for surface and ink, so they
  are legible in light, frost and glass (native integration release 22).

* Thu Sep 17 2026 Nick McMillen <nmcmil@users.noreply.github.com> - 151.0.7922.72-2.viola8210.native21
- Viola 0.2.10 with Luma native integration release 21 (native window,
  independent DevTools window, mouse back/forward, context-menu grab fix).
