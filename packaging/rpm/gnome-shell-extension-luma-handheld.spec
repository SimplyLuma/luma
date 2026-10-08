# SPDX-License-Identifier: Apache-2.0

%global extension_uuid handheld@project-luma.local

Name:           gnome-shell-extension-luma-handheld
Version:        0.1.0
Release:        1.luma.39%{?dist}
Summary:        Luma handheld interaction policy
License:        Apache-2.0
URL:            https://project-luma.local/
Source0:        extension.js
Source1:        metadata.json
Source2:        README.md
Source3:        LICENSE.md
Source4:        handheld.css
Source5:        luma-power-key-broker.py
Source6:        70-luma-handheld-power-key.rules
Source7:        luma-mobile-input-settings.py
Source8:        org.project_luma.MobileInputSettings.desktop
Source9:        org.project_luma.handheld.gschema.xml
Source10:       luma-keyboard-lab
Source11:       luma-keyboard-lab-session
Source12:       luma-keyboard-lab-entry.py
Source13:       luma-kwin-keyboard-engine
Source14:       luma-power-key-broker.service
BuildArch:      noarch
Requires:       gnome-shell >= 50
Requires:       python3-gobject-base
Requires:       libadwaita

%description
Adds edge-origin touch gestures and a single-visible-window posture to the
shared Luma Shell when running on a portrait handheld device.

%prep
cp %{SOURCE3} LICENSE

%build

%install
install -D -m 0644 %{SOURCE0} \
  %{buildroot}%{_datadir}/gnome-shell/extensions/%{extension_uuid}/extension.js
install -D -m 0644 %{SOURCE1} \
  %{buildroot}%{_datadir}/gnome-shell/extensions/%{extension_uuid}/metadata.json
install -D -m 0644 %{SOURCE2} \
  %{buildroot}%{_pkgdocdir}/README.md
install -D -m 0644 %{SOURCE4} \
  %{buildroot}%{_datadir}/gnome-shell/extensions/%{extension_uuid}/handheld.css
install -D -m 0755 %{SOURCE5} \
  %{buildroot}%{_libexecdir}/luma-power-key-broker
install -D -m 0644 %{SOURCE6} \
  %{buildroot}%{_udevrulesdir}/70-luma-handheld-power-key.rules
install -D -m 0755 %{SOURCE7} \
  %{buildroot}%{_libexecdir}/luma-mobile-input-settings
install -D -m 0644 %{SOURCE8} \
  %{buildroot}%{_datadir}/applications/org.project_luma.MobileInputSettings.desktop
install -D -m 0644 %{SOURCE9} \
  %{buildroot}%{_datadir}/glib-2.0/schemas/org.project_luma.handheld.gschema.xml
install -D -m 0755 %{SOURCE10} \
  %{buildroot}%{_libexecdir}/luma-keyboard-lab
install -D -m 0755 %{SOURCE11} \
  %{buildroot}%{_libexecdir}/luma-keyboard-lab-session
install -D -m 0755 %{SOURCE12} \
  %{buildroot}%{_libexecdir}/luma-keyboard-lab-entry
install -D -m 0755 %{SOURCE13} \
  %{buildroot}%{_libexecdir}/luma-kwin-keyboard-engine
install -D -m 0644 %{SOURCE14} \
  %{buildroot}%{_userunitdir}/luma-power-key-broker.service

%files
%license LICENSE
%doc %{_pkgdocdir}/README.md
%{_datadir}/gnome-shell/extensions/%{extension_uuid}/
%{_libexecdir}/luma-power-key-broker
%{_udevrulesdir}/70-luma-handheld-power-key.rules
%{_libexecdir}/luma-mobile-input-settings
%{_datadir}/applications/org.project_luma.MobileInputSettings.desktop
%{_datadir}/glib-2.0/schemas/org.project_luma.handheld.gschema.xml
%{_libexecdir}/luma-keyboard-lab
%{_libexecdir}/luma-keyboard-lab-session
%{_libexecdir}/luma-keyboard-lab-entry
%{_libexecdir}/luma-kwin-keyboard-engine
%{_userunitdir}/luma-power-key-broker.service

%posttrans
glib-compile-schemas %{_datadir}/glib-2.0/schemas &>/dev/null || :

%changelog
* Sun Aug 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.39
- Observe bottom navigation without claiming or delaying application touches
- Preserve release-versus-hold after dock or application gesture consumption
- Count bottom candidates and direction rejection for physical diagnostics

* Sun Aug 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.38
- Remove the global capture phase that blocked all other edge recognizers
- Add content-free per-gesture counters for physical acceptance diagnostics

* Sun Aug 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.37
- Detect All Apps origin from visible applications instead of mapped Home
- Accept held system navigation from the entire dock and bottom safe area
- Preserve horizontal dock scrolling with an upward-dominant direction gate

* Sun Aug 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.36
- Rearm the empty Activity View guard after the overview is definitively shown

* Sun Aug 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.35
- Let the configured wallpaper paint through the transparent Home surface
- Restore the launching app when All Apps is dismissed from an application
- Give bottom system navigation capture-phase priority over child gestures

* Sun Aug 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.34
- Place Home above the wallpaper group and below compositor-managed windows
- Make an already-settled Home gesture idempotent to prevent black repaints
- Guard Activity View until the final application dismissal returns to Home

* Sun Aug 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.33
- Make Home part of the actual background layer below application windows
- Replace the completed-edge timer race with one continuous release/hold pan
- Exit an empty Activity View directly to Home after the final window closes

* Sun Aug 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.32
- Prepare the native picker from the first bottom-edge progress frame
- Preserve quick-release Home and held-recents semantics without an inert delay

* Sun Aug 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.31
- Retire the physically rejected custom recents overlays from the extension
- Restore GNOME's native one-workspace window picker for responsive FP6 input

* Sun Aug 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.30
- Remove compositor snapshots and continuous card motion from FP6 recents
- Paint only three lightweight identity cards and commit gestures on release

* Sun Aug 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.29
- Replace continuously repainted recents clones with frozen in-memory textures
- Cache at most eight previews and fall back to app artwork when unavailable

* Sun Aug 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.28
- Replace desktop Overview on handhelds with overlapping live application cards
- Add horizontal recents browsing, tap-to-resume, and upward graceful close

* Sun Aug 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.27
- Make Home opaque and cancel stale Home callbacks during application launch
- Add touch scrolling, nested pull-down, and animated drawer dismissal

* Sun Aug 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.26
- Add a real wallpaper Home grid and dock-expanded scrolling application drawer
- Collapse handheld workspace navigation to one workspace and restore it docked

* Sun Aug 16 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.25
- Present handheld Home as a responsive 4x5 paged application launcher
- Preserve the running-window picker on swipe-and-hold and desktop grid modes

* Sat Aug 15 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.24
- Fit isolated Android windows to the exact panel-to-dock handheld work area

* Sat Aug 15 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.23
- Expose the non-sensitive Android geometry record to Luma diagnostics

* Sat Aug 15 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.22
- Recognize the isolated Android presentation as a first-class Luma window
- Record bounded frame, work-area, and dock geometry for physical diagnostics

* Sat Aug 15 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.21
- Keep failed Android launches visible with the hardware safety reason

* Sat Aug 15 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.20
- Route the handheld Back gesture into focused Android application navigation

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.19
- Route short power presses through a persistent raw-device broker
- Allow wake while display-off monitor posture is temporarily unavailable

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.18
- Add wvkbd lab and distinguish Ubuntu Touch Lomiri from Maliit Keyboard

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.17
- Add removable native KWin labs for Plasma Keyboard and Maliit

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.16
- Add a disposable native Squeekboard comparison without session integration

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.15
- Reliably reveal Stevia after its nested typing surface has mapped and focused

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.14
- Add isolated native keyboard labs and live availability-aware test controls
- Let any visible on-screen keyboard own the bottom edge and cover the dock

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.13
- Add recoverable display sleep/wake, FP6 power-key broker, and idle timeout
- Reserve bottom-edge navigation while the on-screen keyboard is visible
- Add mobile display and virtual-keyboard evaluation settings

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.12
- Restore quick Home versus held running-app picker bottom gestures
- Open the native OSK after a real touchscreen tap in an editable field
- Add lifecycle-bound immediate launch acknowledgement for cold applications
- Own short power presses as recoverable display sleep/wake on handhelds

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.11
- Keep app-open dock top and icon coordinates identical to the home dock
- Fill only the side and bottom safe area with separate background chrome

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.10
- Remove the floating dock's residual side inset from app-open system chrome

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.9
- Transform the persistent mobile dock into flush full-width app chrome
- Retain the floating rounded dock only when no application is visible

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.8
- Finish optical vertical centering of the handheld dock content row

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.7
- Match the handheld dock bottom safe-area gap to its optical side insets
- Lower the dock content row without changing the pill's outer geometry

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.6
- Route bottom swipe to the window picker and refine FP6 dock safe-area insets

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.5
- Refine FP6 panel and pill dock; add native horizontal touch panning

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.4
- Split top-edge calendar and Quick Settings actions using GJS tuple coordinates

* Thu Aug 13 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.3
- Use compositor-native edge gestures before dock and OSK surfaces

* Wed Aug 12 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.2
- Explicitly load handheld chrome styles and reserve the bottom edge for navigation

* Wed Aug 12 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.1
- Initial handheld edge gestures and single-surface posture
