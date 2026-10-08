# SPDX-License-Identifier: Apache-2.0

Name:           luma-android-runtime
Version:        0.1.0
Release:        1.luma.80.creator20261007.1%{?dist}
Summary:        Project Luma integration for Android applications
License:        Apache-2.0
URL:            https://project-luma.local/android
BuildArch:      noarch

Source0:        luma-android-runtime.tar.gz
Source1:        LICENSE.md
Source2:        counted-unittest.py

BuildRequires:  desktop-file-utils
BuildRequires:  python3-devel
BuildRequires:  shared-mime-info
BuildRequires:  systemd-rpm-macros
Requires:       waydroid >= 1.6.3
Requires:       python3
Requires:       glib2
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       luma-developer-platform >= 0.1.0-1.luma.46~preview.20260906.1
Requires:       shared-mime-info
Requires:       polkit
Requires:       lxc
Requires:       openssl
Recommends:     android-tools
Suggests:       freerdp
Suggests:       weston-libs-backend-rdp

%description
Project Luma's source-owned Android runtime policy, safe APK installer,
application integration service, adaptive settings surface, resource policy,
and recovery command line. The replaceable container engine is Waydroid.

%prep
%autosetup -n luma-android-runtime
cp %{SOURCE1} LICENSE.md

%build

%install
install -d %{buildroot}%{python3_sitelib}/luma_android
install -m 0644 luma_android/*.py %{buildroot}%{python3_sitelib}/luma_android/
install -D -m 0755 bin/luma-android %{buildroot}%{_bindir}/luma-android
install -D -m 0755 bin/luma-androidd %{buildroot}%{_bindir}/luma-androidd
install -D -m 0755 bin/luma-android-installer %{buildroot}%{_bindir}/luma-android-installer
install -D -m 0755 bin/luma-android-settings %{buildroot}%{_bindir}/luma-android-settings
install -D -m 0755 bin/luma-android-session %{buildroot}%{_bindir}/luma-android-session
install -D -m 0755 bin/luma-android-policy-generator \
  %{buildroot}%{_prefix}/lib/systemd/system-generators/luma-android-policy-generator
install -D -m 0755 bin/luma-waydroid-package-session \
  %{buildroot}%{_libexecdir}/luma-waydroid-package-session
install -D -m 0755 bin/luma-waydroid-binder-compat \
  %{buildroot}%{_libexecdir}/luma-waydroid-binder-compat
install -D -m 0755 bin/luma-waydroid-fp6-graphics-compat \
  %{buildroot}%{_libexecdir}/luma-waydroid-fp6-graphics-compat
install -D -m 0755 bin/luma-waydroid-fp6-gpu-watchdog \
  %{buildroot}%{_libexecdir}/luma-waydroid-fp6-gpu-watchdog
install -D -m 0755 bin/luma-waydroid-fp6-software-compositor \
  %{buildroot}%{_libexecdir}/luma-waydroid-fp6-software-compositor

install -D -m 0644 data/org.projectluma.AndroidInstaller.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.AndroidInstaller.desktop
# Standard XDG precedence, shared with the existing launcher-policy component.
# Keep the provider-owned Waydroid entry intact; removal restores its visibility.
install -D -m 0644 data/Waydroid.desktop \
  %{buildroot}%{_datadir}/luma/desktop-overrides/applications/Waydroid.desktop
install -D -m 0644 data/org.projectluma.AndroidSettings.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.AndroidSettings.desktop
install -D -m 0644 data/application-vnd.android.package-archive.xml \
  %{buildroot}%{_datadir}/mime/packages/luma-android.xml
install -D -m 0644 data/org.projectluma.Android1.service \
  %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.Android1.service
install -D -m 0644 data/org.projectluma.ApplicationInstaller1.service \
  %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.ApplicationInstaller1.service
install -D -m 0644 data/luma-android.service \
  %{buildroot}%{_userunitdir}/luma-android.service
install -D -m 0644 data/luma-android-session.service \
  %{buildroot}%{_userunitdir}/luma-android-session.service
install -D -m 0644 data/luma-android-initialize.service \
  %{buildroot}%{_unitdir}/luma-android-initialize.service
install -D -m 0644 data/50-luma-android.preset \
  %{buildroot}%{_presetdir}/50-luma-android.preset
install -D -m 0644 data/org.projectluma.waydroid-package-session.policy \
  %{buildroot}%{_datadir}/polkit-1/actions/org.projectluma.waydroid-package-session.policy
install -D -m 0644 data/luma-waydroid-package-session.rules \
  %{buildroot}%{_datadir}/polkit-1/rules.d/50-luma-waydroid-package-session.rules
install -D -m 0644 data/luma-android-user.conf \
  %{buildroot}%{_user_tmpfilesdir}/luma-android.conf
install -D -m 0644 data/luma-waydroid-fp6-gpu-watchdog.service \
  %{buildroot}%{_unitdir}/luma-waydroid-fp6-gpu-watchdog.service
install -D -m 0644 data/luma-android-fp6-software-compositor.service \
  %{buildroot}%{_userunitdir}/luma-android-fp6-software-compositor.service
install -d %{buildroot}%{_userunitdir}/graphical-session.target.wants
ln -s ../luma-android.service \
  %{buildroot}%{_userunitdir}/graphical-session.target.wants/luma-android.service

install -D -m 0644 config/luma-android.conf %{buildroot}%{_sysconfdir}/luma/android.conf
for profile in config/profiles/*.conf; do
  install -D -m 0644 "$profile" \
    "%{buildroot}%{_datadir}/luma/android/profiles/$(basename "$profile")"
done
install -D -m 0644 config/hardware/fairphone-fp6-kernel-notes.sha256 \
  %{buildroot}%{_datadir}/luma/android/hardware/fairphone-fp6-kernel-notes.sha256
install -D -m 0644 config/fp6-software-weston.ini \
  %{buildroot}%{_datadir}/luma/android/fp6-software-weston.ini

%check
PYTHONPATH=%{buildroot}%{python3_sitelib} python3 %{SOURCE2} tests 40
desktop-file-validate %{buildroot}%{_datadir}/applications/*.desktop %{buildroot}%{_datadir}/luma/desktop-overrides/applications/*.desktop

%post
update-mime-database %{_datadir}/mime >/dev/null 2>&1 || :
update-desktop-database %{_datadir}/applications >/dev/null 2>&1 || :
update-desktop-database %{_datadir}/luma/desktop-overrides/applications >/dev/null 2>&1 || :

%postun
update-mime-database %{_datadir}/mime >/dev/null 2>&1 || :
update-desktop-database %{_datadir}/applications >/dev/null 2>&1 || :
update-desktop-database %{_datadir}/luma/desktop-overrides/applications >/dev/null 2>&1 || :

%files
%license LICENSE.md
%config(noreplace) %{_sysconfdir}/luma/android.conf
%{_bindir}/luma-android
%{_bindir}/luma-androidd
%{_bindir}/luma-android-installer
%{_bindir}/luma-android-settings
%{_bindir}/luma-android-session
%{_prefix}/lib/systemd/system-generators/luma-android-policy-generator
%{_libexecdir}/luma-waydroid-package-session
%{_libexecdir}/luma-waydroid-binder-compat
%{_libexecdir}/luma-waydroid-fp6-graphics-compat
%{_libexecdir}/luma-waydroid-fp6-gpu-watchdog
%{_libexecdir}/luma-waydroid-fp6-software-compositor
%{python3_sitelib}/luma_android/
%{_datadir}/applications/org.projectluma.AndroidInstaller.desktop
%{_datadir}/applications/org.projectluma.AndroidSettings.desktop
%{_datadir}/luma/desktop-overrides/applications/Waydroid.desktop
%{_datadir}/mime/packages/luma-android.xml
%{_datadir}/dbus-1/services/org.projectluma.Android1.service
%{_datadir}/dbus-1/services/org.projectluma.ApplicationInstaller1.service
%{_userunitdir}/luma-android.service
%{_userunitdir}/luma-android-session.service
%{_unitdir}/luma-android-initialize.service
%{_presetdir}/50-luma-android.preset
%{_userunitdir}/luma-android-fp6-software-compositor.service
%{_userunitdir}/graphical-session.target.wants/luma-android.service
%{_datadir}/luma/android/
%{_datadir}/polkit-1/actions/org.projectluma.waydroid-package-session.policy
%{_datadir}/polkit-1/rules.d/50-luma-waydroid-package-session.rules
%{_user_tmpfilesdir}/luma-android.conf
%{_unitdir}/luma-waydroid-fp6-gpu-watchdog.service

%changelog
* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.70.preview20260915.1
- Never start the Android broker, session or compositor inside the GDM greeter
- Let the sandboxed broker open dconf's runtime file so appearance sync stops warning
- Drop CPUAccounting= from the container resource drop-in; current systemd ignores it

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.69.preview20260914.1
- Let the Android services publish rounded application icons, so Android apps match the dock
- Make every Android application a resizable window on desktop and tablet instead of letterboxing it

* Tue Sep 08 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.66.surfacepreview20260908.1
- Reconcile generated Android launchers after graphical-session startup ordering settles

* Sun Sep 06 2026 Project Luma <build@projectluma.invalid> - 0.1.0-1.luma.64.preview20260906.1
- Hide runtime-management launchers while retaining Android app support

* Sun Sep 06 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.63.preview20260906.1
- Separate native frame presentation from composition; sync host appearance.

* Sun Sep 06 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.62.preview20260906.1
- Add validated user-zero removal with explicit data retention to the existing package helper

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.58
- Report a successful fail-closed FP6 containment stop without a false failed unit
- Preserve failure status for real GPU faults and watchdog infrastructure loss
- Declare the systemd RPM macro dependency required by a clean Fedora builder

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.57
- Fail closed on FP6 after the supervised 30 Hz path reproduced GMU/DPU faults
- Retain software presentation only behind a root-owned, reboot-cleared lab gate

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.56
- Return the supervised all-software FP6 presentation to the proven 30 Hz bound
- Reject 60 Hz after a clean-boot GMU timeout and DPU preemption hangcheck

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.55
- Remove the unstable Mesa llvmpipe override while retaining SDL software rendering
- Supervise the viewer and stop hidden Android work whenever presentation exits
- Extend startup validation to catch delayed viewer initialization crashes

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.54
- Use SDL3's canonical Wayland driver hint and force any fallback GL into software
- Retest 60 Hz only after proving the 30 Hz viewer submits no ongoing GPU work

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.53
- Use FreeRDP's validated negative boolean form to disable its GFX pipeline
- Retain fail-closed software-only rendering after the rejected parser form

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.52
- Force the FP6 FreeRDP bridge onto Wayland software rendering and software GDI
- Prevent the Android viewer from opening a second accelerated host GPU context

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.51
- Bound FP6 software presentation to 30 Hz after the 60 Hz safety rejection
- Preserve the first usable midpoint above the rejected 20 Hz interaction mode

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.50
- Raise the isolated FP6 Android presentation from 20 Hz to 60 Hz
- Reject the unusably jittery low-refresh profile while retaining GPU isolation

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.49
- Keep cliprdr negotiated for RDP finalization while disabling all data directions
- Preserve Luma ownership of clipboard and file transfer without disconnecting Weston

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.48
- Let SDL FreeRDP establish its native-touch hint without environment conflicts
- Fail visibly when the FP6 presentation viewer exits during startup
- Preserve compositor runtime files across Android broker restarts

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.47
- Keep clipboard and file transfer at the Luma host boundary on FP6
- Prevent FreeRDP from attempting a forbidden FUSE mount in the broker sandbox

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.46
- Authenticate the isolated FP6 compositor from hardened broker namespaces
- Preserve strict process inspection for ordinary unsandboxed diagnostics

* Sun Aug 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.45
- Disable both SDL synthetic touch/mouse compatibility directions on FP6
- Keep RDPEI as the sole finger-input path so taps cannot leak into a cursor

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.44
- Require native RDPEI termination for the isolated FP6 Android presentation
- Keep phone hardware controls in Luma instead of forwarding them to Android

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.43
- Scale the bounded FP6 Android canvas exactly 2x into the measured app area
- Align SDL native touch through the same reversible presentation transform

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.42
- Replace deprecated fullscreen smart sizing with SDL native touch delivery
- Negotiate the exact borderless Luma work area to align pixels and input

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.41
- Migrate stale FP6 lab geometry once to the exact scale-safe software canvas
- Version the Android, Weston, FreeRDP, and admission geometry contract

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.40
- Align the bounded FP6 canvas to GNOME's scale-3 Wayland buffer contract
- Retain full-screen smart sizing and redirected multitouch at 486 by 936

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.39
- Scale the bounded FP6 software canvas to the full Luma application surface
- Redirect direct touchscreen input through RDP multitouch instead of a pointer

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.38
- Reuse the persistent FP6 viewer instead of recreating its full-screen surface
- Monitor kernel faults through a capability-free journal stream

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.37
- Recover the GPU watchdog after a harmless /dev/kmsg reader overrun
- Retry the public detached session handoff after a bounded cold-start race

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.36
- Authenticate the immutable active compositor through user systemd
- Keep the root watchdog isolated from user process memory and home data

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.35
- Supply explicit non-secret local RDP identity for unattended app launches

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.34
- Keep the private Wayland socket inside the compositor runtime directory
- Preserve read-only home isolation while exposing the guarded runtime path

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.33
- Present FP6 Android through a CPU-rendered, loopback-only compositor
- Keep the direct A810 path denied and continuously supervise containment

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.32
- Make blocked icon launches visible, named, and journal-diagnosable

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31
- Thaw a warm Waydroid container before boot-completion readiness probes
- Prevent a frozen Binder service from stalling ordinary warm app launch

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.30
- Revoke the IFPC diagnostic after cold Android triggered GMU and DPU faults
- Keep every FP6 kernel fail-closed pending a complete physical acceptance pass

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.29
- Wait for Android's public boot-completion property before launching apps
- Prevent successful-looking launches from being dropped during cold boot

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.28
- Reassert a requested application once after a genuinely cold Android start
- Keep warm application launches on the single-dispatch fast path

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.27
- Admit FP6 Android only on the physically verified IFPC kernel identity
- Stop and latch Android immediately on an Adreno hangcheck or GMU timeout
- Reconcile generated Android launchers through Luma's supervised lifecycle

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.26
- Temporarily thaw idle Waydroid containers for privileged package transactions
- Restore the prior frozen state after installs and compatibility policy updates

* Sat Aug 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.25
- Read package compatibility from Waydroid's public session property interface
- Retain the privileged container probe only as a compatibility fallback

* Fri Aug 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.24
- Keep optional desktop input policy from aborting application installation
- Retry transient Android compatibility-profile queries for split packages

* Fri Aug 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.23
- Restore the FP6 presentation gate after sustained Pixman nesting still wedged A810
- Keep Android applications installed while leaving desktop and tablet unchanged

* Fri Aug 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.22
- Reassert FP6 cold launches after the observed FallbackHome-to-Launcher handoff

* Fri Aug 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.21
- Reassert a requested FP6 app once after Android's cold-start launcher race

* Fri Aug 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.20
- Let the sandboxed FP6 compositor create its private user-runtime socket

* Fri Aug 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.19
- Present FP6 Android apps through an on-demand normal-window Pixman compositor
- Migrate the guest canvas to the exact accepted app-area geometry

* Fri Aug 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.18
- Block FP6 interactive Android windows after repeatable host GPU wedges
- Keep installed launchers visible and report the hardware gate through a notification

* Fri Aug 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.17
- Bound FP6 Android rendering to 720 by 1600 while native acceleration is unavailable
- Disable Waydroid subsurface paths implicated in host compositor freezes

* Fri Aug 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.16
- Route generated Waydroid launchers through Luma's on-demand lifecycle
- Resume Android before opening application permissions from a generated launcher

* Fri Aug 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.15
- Regenerate Waydroid base properties once when FP6 fallback values are first added
- Avoid an unnecessary privileged input-policy request on touch-first profiles

* Fri Aug 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.14
- Pre-create sandboxed user data roots before the broker starts
- Select the bundled ANGLE/Pastel fallback on Fairphone 6 until Mesa supports its GPU ID

* Fri Aug 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.13
- Allow the sandboxed broker to write its private Android session log

* Fri Aug 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.12
- Mask binderfs only when the kernel exposes built-in Binder devices instead
- Create validated Binder nodes with explicit world read/write permissions

* Fri Aug 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.11
- Keep Android's soft keyboard closed when the desktop hardware keyboard is active
- Preserve touch-first Android input behavior on tablet and handheld profiles

* Fri Aug 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.10
- Preserve built-in Binder devices on kernels that intentionally omit binderfs
- Override Fedora Waydroid's binderfs-only pre-start path with validated device identities

* Fri Aug 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.9
- Add on-demand handheld lifecycle and stricter phone CPU, memory, IO, and task ceilings
- Keep one shared Android runtime while preventing automatic phone warm-up at login

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.8
- Verify staged package bytes without misapplying the compressed outer size to APK members

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.7
- Select one compatible ABI, density, and locale split from package-set metadata
- Query the live Android device profile through the existing bounded helper

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.6
- Accept bounded APKS, XAPK, and APKM archives through Android's atomic split transaction
- Add a narrowly authorized, path-constrained package-session helper for split installs
- Extend the MIME and Filer handoff contracts without giving Filer package privileges

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.5
- Give the sandboxed broker a private writable package-staging runtime directory
- Resolve unexpected installer worker failures visibly instead of spinning forever

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- Wait for Android Package Manager beyond Waydroid's early RUNNING state
- Hold install completion until Waydroid's public application registry settles
- Rebind stale Android sessions to the current Wayland login after compositor loss

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Add a confirmed per-app removal surface and warm-before-uninstall behavior
- Bound warm-session retries when no graphical compositor is available

* Thu Aug 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Supervise the warm session correctly and start it only after Android apps exist
- Open application permissions through Waydroid's public intent interface

* Wed Aug 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Add the first source-owned converged Android runtime integration
