# SPDX-License-Identifier: Apache-2.0

Name:           luma-application-installer
Version:        0.1.0
Release:        1.luma.68.creator20261008.1%{?dist}
Summary:        Project Luma universal application package broker
License:        Apache-2.0
URL:            https://projectluma.org/applications
BuildArch:      noarch

Source0:        luma-application-installer.tar.gz
Source1:        LICENSE.md
Source2:        org.project_luma.shell-state.gschema.xml

BuildRequires:  python3-gobject
BuildRequires:  python3-cairo
BuildRequires:  flatpak-libs
BuildRequires:  ostree-libs
BuildRequires:  desktop-file-utils
BuildRequires:  python3-devel
BuildRequires:  shared-mime-info
BuildRequires:  systemd-rpm-macros
BuildRequires:  gtk4
BuildRequires:  libadwaita
BuildRequires:  luma-developer-platform >= 0.1.0-1.luma.91.creator20261005.1
BuildRequires:  xorg-x11-server-Xvfb
BuildRequires:  dbus-daemon
BuildRequires:  dconf
BuildRequires:  glib2
Requires:       cpio
Requires:       bubblewrap
Requires:       dpkg
Requires:       flatpak
Requires:       gtk4
Requires:       libadwaita
Requires:       luma-developer-platform >= 0.1.0-1.luma.91.creator20261005.1
Requires:       container-selinux
Requires:       policycoreutils
Requires:       podman
# PID 1 in every package capsule: reaps orphaned processes (podman --init).
Requires:       catatonit
# Debian capsules reach the network through slirp4netns (backends._install_deb).
Requires:       slirp4netns
# Publisher repository keys and APT index signatures are checked with gpg.
Requires:       gnupg2
Requires:       polkit
Requires:       python3
Requires:       luma-apk-metadata = 0.1.0-1.luma.1~preview.20260910.1%{?dist}
Requires:       python3-libarchive-c
# Launch failures are written to the journal with fields Luma Vitals reads.
Requires:       python3-systemd
# AppImages run against the host's libraries; this fills the AppImage baseline.
Requires:       luma-appimage-compat
Requires:       python3-gobject
Requires:       python3-pyyaml
Requires:       flatpak-libs
Requires:       ostree-libs
# Firmware updates are listed and installed through fwupd over D-Bus.
Recommends:     fwupd
Requires:       rpm
Requires:       rpm-ostree
Requires:       shared-mime-info
Requires:       snapd
Requires:       squashfs-tools
Requires:       xdg-dbus-proxy
Recommends:     luma-android-runtime >= 0.1.0-1.luma.62
Recommends:     luma-relay >= 0.1.0-1.luma.10

%description
Project Luma's single application-package review and transaction boundary.
It statically inspects RPM, DEB, AppImage, Flatpak, and Snap inputs; dispatches
Android and Windows inputs to their dedicated compatibility services; and
preserves each ecosystem's dependency, sandbox, signature, and update model.

%prep
%autosetup -n luma-application-installer/src/luma-installer
cp %{SOURCE1} LICENSE.md
# Private canonical appearance policy for genuine Light/Dark native checks.
mkdir -p tests/appearance-schema
cp %{SOURCE2} tests/appearance-schema/
glib-compile-schemas --strict tests/appearance-schema

%build

%install
install -d %{buildroot}%{python3_sitelib}/luma_depot
install -m 0644 luma_depot/*.py %{buildroot}%{python3_sitelib}/luma_depot/
install -D -m0644 data/depot.css %{buildroot}%{_datadir}/luma-depot/depot.css
install -d %{buildroot}%{python3_sitelib}/luma_installer
install -m 0644 luma_installer/*.py %{buildroot}%{python3_sitelib}/luma_installer/
install -D -m0644 data/org.projectluma.AppPreferences.Read.gschema.xml %{buildroot}%{_datadir}/glib-2.0/schemas/org.projectluma.AppPreferences.Read.gschema.xml
glib-compile-schemas --strict %{buildroot}%{_datadir}/glib-2.0/schemas
# glib2's installed schema trigger owns the shared compiled cache.
rm %{buildroot}%{_datadir}/glib-2.0/schemas/gschemas.compiled
install -D -m 0755 bin/luma-installer %{buildroot}%{_bindir}/luma-installer
ln -s luma-installer %{buildroot}%{_bindir}/luma-install
install -D -m 0755 bin/luma-depot %{buildroot}%{_bindir}/luma-depot
install -D -m 0644 data/org.projectluma.Depot.desktop %{buildroot}%{_datadir}/applications/org.projectluma.Depot.desktop
install -D -m 0644 data/depot-catalog.json %{buildroot}%{_datadir}/luma/installer/depot-catalog.json
install -D -m 0644 data/depot-catalog-4.json %{buildroot}%{_datadir}/luma/installer/depot-catalog-4.json
# The catalogue key and the Luma remote's .flatpakrepo come from the
# distribution workstream. Until they are committed Depot reads its seed and
# reports the Luma remote as not set up; it never ships a stand-in key.
printf '%%%%dir %%%%{_datadir}/luma/installer\n' > depot-trust.files
for trust in depot-catalog.pub luma.flatpakrepo; do
  if [ -f "data/$trust" ]; then
    install -D -m 0644 "data/$trust" "%{buildroot}%{_datadir}/luma/installer/$trust"
    printf '%%%%{_datadir}/luma/installer/%s\n' "$trust" >> depot-trust.files
  fi
done
# Luma's firmware block list, signed with the catalogue key (depot_firmware.py).
install -D -m 0644 data/energy-flag-exceptions.txt %{buildroot}%{_datadir}/luma/energy-flag-exceptions.txt
install -D -m 0644 data/firmware-blocklist.json %{buildroot}%{_datadir}/luma/firmware/blocklist.json
install -D -m 0644 data/firmware-blocklist.json.minisig %{buildroot}%{_datadir}/luma/firmware/blocklist.json.minisig
install -D -m 0644 data/org.projectluma.Depot.service \
  %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.Depot.service
install -D -m 0644 data/org.projectluma.SoftwareUpdate.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.SoftwareUpdate.desktop
install -D -m 0644 data/luma-depot-provision.service %{buildroot}%{_userunitdir}/luma-depot-provision.service
install -D -m 0644 data/luma-depot-app-updates.service %{buildroot}%{_userunitdir}/luma-depot-app-updates.service
install -D -m 0644 data/luma-depot-app-updates.timer %{buildroot}%{_userunitdir}/luma-depot-app-updates.timer
install -d %{buildroot}%{_userunitdir}/graphical-session.target.wants
ln -s ../luma-depot-provision.service \
  %{buildroot}%{_userunitdir}/graphical-session.target.wants/luma-depot-provision.service
ln -s ../luma-depot-app-updates.timer \
  %{buildroot}%{_userunitdir}/graphical-session.target.wants/luma-depot-app-updates.timer
install -d %{buildroot}%{_userpresetdir}
printf 'enable luma-depot-provision.service\nenable luma-depot-app-updates.timer\n' > %{buildroot}%{_userpresetdir}/60-luma-depot.preset
install -D -m 0755 bin/luma-appctl %{buildroot}%{_bindir}/luma-appctl
install -D -m 0755 bin/luma-icon-plates %{buildroot}%{_bindir}/luma-icon-plates
install -D -m 0644 data/luma-icon-plates.service %{buildroot}%{_userunitdir}/luma-icon-plates.service
install -D -m 0644 data/luma-icon-plates.path %{buildroot}%{_userunitdir}/luma-icon-plates.path
install -d %{buildroot}%{_userpresetdir}
printf 'enable luma-icon-plates.service\nenable luma-icon-plates.path\n' > %{buildroot}%{_userpresetdir}/60-luma-icon-plates.preset
install -D -m 0755 bin/luma-installer-service %{buildroot}%{_bindir}/luma-installer-service
install -D -m 0755 bin/luma-capsule-open %{buildroot}%{_libexecdir}/luma-capsule-open
install -D -m 0755 bin/luma-capsule-launch %{buildroot}%{_bindir}/luma-capsule-launch
install -D -m 0755 bin/luma-app-data-broker %{buildroot}%{_libexecdir}/luma-app-data-broker
install -D -m 0755 bin/luma-seed-app-baseline %{buildroot}%{_bindir}/luma-seed-app-baseline
install -D -m 0644 data/luma-app-baseline.service %{buildroot}%{_unitdir}/luma-app-baseline.service
install -D -m 0644 data/luma-app-baseline.conf %{buildroot}%{_tmpfilesdir}/luma-app-baseline.conf
install -d %{buildroot}%{_unitdir}/graphical.target.wants
ln -s ../luma-app-baseline.service %{buildroot}%{_unitdir}/graphical.target.wants/luma-app-baseline.service
install -D -m 0644 data/org.projectluma.AppData1.service %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.AppData1.service
install -D -m 0644 data/org.projectluma.DepotHost1.service %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.DepotHost1.service
install -D -m 0644 data/org.projectluma.ApplicationDirectory1.service %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.ApplicationDirectory1.service
install -D -m 0755 bin/luma-installer-system %{buildroot}%{_libexecdir}/luma-installer-system
install -D -m 0755 bin/luma-snap-mount-generator \
  %{buildroot}%{_prefix}/lib/systemd/system-generators/luma-snap-mount-generator
install -D -m 0644 data/org.projectluma.ApplicationInstaller.desktop \
  %{buildroot}%{_datadir}/applications/org.projectluma.ApplicationInstaller.desktop
install -D -m 0644 data/io.luma.Valet.desktop %{buildroot}%{_datadir}/applications/io.luma.Valet.desktop
install -D -m 0644 data/io.luma.Install.desktop %{buildroot}%{_datadir}/applications/io.luma.Install.desktop
install -D -m 0644 data/org.projectluma.ApplicationInstaller2.service \
  %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.ApplicationInstaller2.service
install -D -m 0644 data/luma-application-packages.xml \
  %{buildroot}%{_datadir}/mime/packages/luma-application-packages.xml
install -D -m 0644 data/org.projectluma.application-installer.policy \
  %{buildroot}%{_datadir}/polkit-1/actions/org.projectluma.application-installer.policy
install -D -m 0644 data/luma-installer-reconcile.service \
  %{buildroot}%{_unitdir}/luma-installer-reconcile.service
install -D -m 0644 data/luma-depot-appstream.service \
  %{buildroot}%{_unitdir}/luma-depot-appstream.service
install -D -m 0644 data/luma-depot-appstream.timer \
  %{buildroot}%{_unitdir}/luma-depot-appstream.timer
install -d %{buildroot}%{_unitdir}/multi-user.target.wants
ln -s ../luma-installer-reconcile.service \
  %{buildroot}%{_unitdir}/multi-user.target.wants/luma-installer-reconcile.service
install -d %{buildroot}%{_unitdir}/timers.target.wants
ln -s ../luma-depot-appstream.timer \
  %{buildroot}%{_unitdir}/timers.target.wants/luma-depot-appstream.timer

install -D -m 0644 data/selinux/luma_application.cil %{buildroot}%{_datadir}/selinux/packages/luma_application.cil

%check
PYTHONPATH=. python3 -c 'from luma_installer.app_host_compatibility import SUPPORTED_INSTALLER_RELEASE; assert SUPPORTED_INSTALLER_RELEASE == int("%{release}".split(".luma.", 1)[1].split(".", 1)[0]), "Installer host declaration differs from its actual package release"'
PYTHONPATH=. python3 -m unittest discover -s tests -p "test_*.py" -v
PYTHONPATH=. dbus-run-session -- python3 -B tests/runtime_app_preferences.py
PYTHONPATH=. dbus-run-session -- python3 -B tests/runtime_app_native_owner.py
desktop-file-validate %{buildroot}%{_datadir}/applications/*.desktop
# Depot notifies when first-boot apps finish installing, so Settings must list
# it under Notifications.
grep -qx 'X-GNOME-UsesNotifications=true' \
  %{buildroot}%{_datadir}/applications/org.projectluma.Depot.desktop
python3 -m py_compile %{buildroot}%{python3_sitelib}/luma_installer/*.py
PYTHONPATH=%{buildroot}%{python3_sitelib} python3 tests/smoke.py
# Real widgets must retain scrolling while installation progress arrives.
GSETTINGS_SCHEMA_DIR=$PWD/tests/appearance-schema PYTHONPATH=%{buildroot}%{python3_sitelib} LUMA_DEPOT_STYLE_PATH=%{buildroot}%{_datadir}/luma-depot/depot.css GSK_RENDERER=cairo timeout --kill-after=5s 60s xvfb-run -a -s '-screen 0 1920x1080x24' dbus-run-session -- python3 tests/native_depot_preview.py
grep -qx 'NoDisplay=true' %{buildroot}%{_datadir}/applications/org.projectluma.SoftwareUpdate.desktop

%post
/usr/sbin/semodule -i %{_datadir}/selinux/packages/luma_application.cil
update-mime-database %{_datadir}/mime >/dev/null 2>&1 || :
update-desktop-database %{_datadir}/applications >/dev/null 2>&1 || :

%postun
if [ "$1" -eq 0 ]; then /usr/sbin/semodule -r luma_application; fi
update-mime-database %{_datadir}/mime >/dev/null 2>&1 || :
update-desktop-database %{_datadir}/applications >/dev/null 2>&1 || :

%files -f depot-trust.files
%{_libexecdir}/luma-capsule-open
%{_datadir}/selinux/packages/luma_application.cil
%{_datadir}/applications/io.luma.Valet.desktop
%{_datadir}/applications/io.luma.Install.desktop
%license LICENSE.md
%{_bindir}/luma-installer
%{_bindir}/luma-install
%{_bindir}/luma-depot
%{_datadir}/applications/org.projectluma.Depot.desktop
%{_datadir}/luma/installer/depot-catalog.json
%dir %{_datadir}/luma/firmware
%{_datadir}/luma/energy-flag-exceptions.txt
%{_datadir}/luma/firmware/blocklist.json
%{_datadir}/luma/firmware/blocklist.json.minisig
%{_datadir}/luma/installer/depot-catalog-4.json
%{_datadir}/dbus-1/services/org.projectluma.Depot.service
%{_datadir}/applications/org.projectluma.SoftwareUpdate.desktop
%{_userunitdir}/luma-depot-provision.service
%{_userunitdir}/luma-depot-app-updates.service
%{_userunitdir}/luma-depot-app-updates.timer
%{_userunitdir}/graphical-session.target.wants/luma-depot-provision.service
%{_userunitdir}/graphical-session.target.wants/luma-depot-app-updates.timer
%{_userpresetdir}/60-luma-depot.preset
%{_bindir}/luma-appctl
%{_bindir}/luma-icon-plates
%{_userunitdir}/luma-icon-plates.service
%{_userunitdir}/luma-icon-plates.path
%{_userpresetdir}/60-luma-icon-plates.preset
%{_bindir}/luma-installer-service
%{_bindir}/luma-capsule-launch
%{_libexecdir}/luma-app-data-broker
%{_bindir}/luma-seed-app-baseline
%{_unitdir}/luma-app-baseline.service
%{_unitdir}/graphical.target.wants/luma-app-baseline.service
%{_tmpfilesdir}/luma-app-baseline.conf
%{_datadir}/dbus-1/services/org.projectluma.AppData1.service
%{_datadir}/dbus-1/services/org.projectluma.DepotHost1.service
%{_datadir}/dbus-1/services/org.projectluma.ApplicationDirectory1.service
%{_libexecdir}/luma-installer-system
%{_prefix}/lib/systemd/system-generators/luma-snap-mount-generator
%{python3_sitelib}/luma_installer/
%{_datadir}/glib-2.0/schemas/org.projectluma.AppPreferences.Read.gschema.xml
%{python3_sitelib}/luma_depot/
%{_datadir}/luma-depot/
%{_datadir}/applications/org.projectluma.ApplicationInstaller.desktop
%{_datadir}/dbus-1/services/org.projectluma.ApplicationInstaller2.service
%{_datadir}/mime/packages/luma-application-packages.xml
%{_datadir}/polkit-1/actions/org.projectluma.application-installer.policy
%{_unitdir}/luma-installer-reconcile.service
%{_unitdir}/multi-user.target.wants/luma-installer-reconcile.service
%{_unitdir}/luma-depot-appstream.service
%{_unitdir}/luma-depot-appstream.timer
%{_unitdir}/timers.target.wants/luma-depot-appstream.timer

%changelog
* Thu Oct 08 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.68.creator20261008.1
- Restore real application rollback, update channels and automatic settings in Luma Updates

* Thu Oct 08 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.67.creator20261008.1
- Record signed Depot lifecycle through its authenticated host service

* Thu Oct 08 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.66.creator20261008.1
- Admit signed Messages first launch without copying native conversation state

* Wed Oct 07 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.65.creator20261007.1
- Explain independent application updates separately from system updates

* Tue Oct 06 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.51.creator20261006.1
- Bind sandbox update consent to exact target and compared installed commits
- Retain filesystem scope/mode and fail closed on missing sandbox metadata
- Reject changed displayed/planned/prepared builds; bulk and retry do not approve permissions
- Notify deferred and completed app updates without reporting failed or cancelled jobs as success
- Exercise provider, controller, and prepared-transaction boundaries in package source tests

* Mon Oct 05 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.48.creator20261005.1
- Regenerate the seed with accurate baseline and optional collection metadata.
- Render Opening feedback before launching; coalesce clicks and cancel on close.
- Verify true Light/Dark native geometry and the completed feedback frame.
- Keep install progress on existing Depot controls and retain allocated scroll.
- Let installed rows and shelves measure their content and use shared buttons.
- Keep Software Update available through Depot without a duplicate launcher.
- Use shared inset panels in Valet.

* Sun Oct 04 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.45.creator20261004.2
- Keep newly launched application capsules under app.slice so their payloads
  participate in the existing swap exhaustion memory guard.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.45.creator20261004.1
- Depot's Updates says which added packages an update removes or keeps.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.43.lumaui20260928.1
- Depot and Valet on LumaUI; capsule apps reap their child processes.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.42.lumaui20260926.2
- Rebuilt from the latest Depot and Valet on LumaUI; keeps the capsule reaper.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.42.lumaui20260926.1
- Depot and Valet on LumaUI; capsule apps reap their child processes.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.41.reaper20260926.1
- Fix: apps no longer stop being able to start new processes after running
  for a while. Each app now runs under a small init that cleans up finished
  processes, instead of leaving them to pile up until the app hits its limit.

* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.40.lumaui20260926.1
- LumaUI port: the app is rebuilt on LumaUI to match the approved design. (Depot and Valet)

* Tue Sep 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.38.preview20260922.1
- Valet's uninstall window names the application and shows its icon, even for
  an application that is part of Luma. For those it says, in one sentence that
  wraps, that the application is part of Luma and can't be uninstalled, and
  offers only Close: no Uninstall button and no data choice.
- Keep settings and data is checked every time the window opens. Unchecking it
  turns the button into Uninstall and Delete Data and says what will be lost.
- The Shell, Settings, Filer, Terminal, Depot, Valet and Software Update are
  refused however they were installed, in the window and in luma-appctl.
  (.37 left unused: reported as taken while this release was prepared.)

* Sun Sep 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.36.preview20260920.1
- An application that will not start with Luma's display and video settings is
  now started again immediately the way its publisher does, rather than on its
  next launch. If that works, the person sees their application open a beat
  later and is told nothing at all, because they should not have to learn that
  a flag existed in order to get their work done. If it fails again, the real
  failure is reported rather than the one we went looking for.
- What Luma changes about how an application starts can be read in words:
  `luma-appctl energy` lists every application Luma adds switches to, what each
  one does in a sentence, which have been withdrawn and why, and how to undo or
  restore any of them.
- The rules about overriding a publisher's command line now run in %check, so a
  build cannot ship without them passing. They were in the repository's own
  test suite, which the package's %check does not read.

* Sun Sep 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.35.preview20260920.1
- Luma starts some applications with switches their publisher did not choose,
  so there is now always a way out of that. An application that fails while
  opening with those switches applied is started the publisher's own way next
  time, and the decision is written where the person can read and undo it.
  LUMA_ENERGY_FLAGS=off skips them for one launch without recording anything,
  and a list at /usr/share/luma/energy-flag-exceptions.txt, /etc/luma or in
  the person's own configuration turns them off for named applications,
  individually or altogether.

* Sun Sep 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.34.preview20260920.1
- Video plays on the graphics chip. Chromium leaves accelerated decode off on
  Linux, so an installed application decoded every frame on the processor even
  where the machine had a media engine, the VA-API driver and the kernel
  driver all present and open: the decode engine's own counter read zero
  cycles while two renderers spent half a processor on one stream. An
  application Luma installs is now started with the decode features named, in
  the single --enable-features switch, since a repeated switch keeps only its
  last value. An unknown feature name is ignored and an unsupported codec
  still falls back to software, so the worst case is no change.
- An application is recognised as Chromium by more of the files only Chromium
  ships. At least one Electron application matched none of the old five, so it
  was started with no switches at all and fell to the X11 default.

* Sat Sep 19 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.33.preview20260919.1
- Claude installs in one click from Anthropic's signed APT repository: the
  repository key must match the fingerprint pinned in the catalogue, InRelease
  must verify with it and be unexpired, the package list and .deb must hash to
  what the signed index says; the verified package then goes through Valet's
  Debian capsule. Update installs a newer build and carries the app's capsule
  data over; Remove removes every build
- Apps Depot installed from Flathub show their source instead of "Review
  removal in Valet"
- Requires gnupg2 and slirp4netns, which publisher repositories and Debian
  capsules already used
- Valet: installing an app's icon no longer fails the whole install on a
  system without the GdkPixbuf typelib (UnboundLocalError on GLib)
* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.32.preview20260918.1
- Depot installs listings from their publisher's official channel instead of
  sending people to a website: a verified publisher's strictly confined snap
  (through snapd, the Snap Store must still name and verify the pinned
  publisher) or the publisher's signed RPM repository (key checked against the
  fingerprint pinned in the signed catalogue, package added with rpm-ostree and
  applied live), through new luma-installer-system commands snap-install,
  snap-refresh, snap-remove, repo-install and repo-remove behind the existing
  polkit action. Progress, a password prompt, plain-language errors with
  Details and Try Again; Open after install; Remove
- Listings show where an app comes from ("From Flathub · Verified", "From
  NordVPN’s snap · Verified"), the remaining publisher-only listings say why
  with Visit Website or Open in Browser, snaps and repository packages say how
  they are sandboxed and how they update, and age ratings show their age
- App artwork is drawn full size without a tile behind it; screenshots keep
  their own shape and are decoded into plain textures every renderer draws
- The seed carries the new rich listings: icons of at least 256 px,
  screenshots, descriptions, releases, sizes and permissions for every app;
  7-Zip and Ollama (command-line tools) leave the schema 4 catalogue
- Permission keys may contain a hyphen; system.ssh_agent and system.gpg_agent
  are accepted as the catalogue's spelling of the hyphenated keys
- Includes the Sticky Notes withdrawal from the catalogue
* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260918.3
- Hardware updates: before Install, Depot lists what fwupd and the computer
  still need (charger, battery level, lid, device problems, EFI partition
  space, Secure Boot updater, stale metadata with Refresh) and a connected
  modem's disconnect. A failure is explained from fwupd's error code, message
  and phase: "Nothing was changed on your device" before writing, urgent
  recovery steps while writing, a restart after it; Try Again, Restart and
  Details with fwupd's journal. Never "unspecified error"
- Failures that changed nothing are recorded per device, firmware, plugin and
  fwupd build: a second holds the offer for a day, a third pauses it until
  fwupd or the firmware changes. Built-in known issues (fwupd 2.1.7 PCIe
  firehose modems) and the signed block list, which now also matches a plugin
  and fwupd builds, pause offers with their reason instead of hiding them
- Every attempt, failure, hold and unmet requirement is journaled with
  structured fields under MESSAGE_ID 5f0c3e8a92d44b6c8e17a4d2b9f06c31 for
  Luma Vitals

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260918.2
- Assembles preview20260917.3 to .7 below, each cut separately from
  preview20260917.2, on top of preview20260918.1, into one build

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260918.1
- "What's new" on a system update opens Depot's own sheet instead of a
  browser on the raw notes JSON: the release's name and date, then its
  changes under New features, Improvements, Fixes and Security, each with a
  headline and details. The package list sits in a collapsed Technical details
  disclosure. The notes are verified against the update graph keys luma-update
  trusts before they are read; a failure shows an inline message with Retry.
  The update card, "This computer" and the preview all use the one sheet

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260917.7
- Every application in Depot draws an icon. An AppStream cached icon is a file
  name, not an icon theme name, and a theme name nothing answers to draws the
  toolkit's missing-image glyph with no error raised: a shelf of empty boxes.
  The decision now lives in luma_installer.depot_icons, the icon theme is
  asked before any name is used, and anything left over is the application's
  monogram rather than a blank
- Icons load at the display's scale. They go through the icon theme's loader
  as a GIcon instead of a fixed texture from Gtk.Image.new_from_file, so a
  40pt tile on a 1.25x display gets the pixels it needs, and the AppStream
  cache's 128px artwork is preferred over its 64px artwork where it matters
- Download the application metadata Depot draws from. It is a Flatpak remote's
  AppStream branch under /var, which an image cannot carry, so a freshly
  installed computer had none: luma-depot-appstream.timer fetches it after
  boot and every six hours, Depot asks for it itself when it finds none, and
  the window redraws when it lands instead of waiting to be reopened. The
  unit cannot fail a boot with no network
- A catalogue image that failed to download is retried after 45 seconds and
  at once when the machine reports a route again, instead of staying missing
  for as long as the window is open

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260917.6
- A browser names an installed application by reading Name from whichever
  desktop entry holds the link's scheme, and prints its own opener command when
  nothing holds it. Every declared link scheme is now held by the application's
  own launcher, for deb, RPM, AppImage, archive and Flatpak installs alike,
  including the schemes an application claims for itself on first run inside
  its capsule. The session's own links -- http, https, mailto and the rest --
  are never taken by an application that merely offers to open them.

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260917.5
- The accessibility bus is bound into both capsule kinds: the address a toolkit
  reads from the session bus named a socket that did not exist inside the
  sandbox, so every application began with "Couldn't connect to accessibility
  bus" and no assistive tool could reach it
- A sandboxed application reads the desktop's font, text scale, theme and
  colour scheme through the settings portal (GTK_USE_PORTAL), as an application
  in a package capsule already did. The settings store is not on the capsule's
  bus, so a toolkit fell back to its built-in defaults and an application drew
  in a font the desktop does not use

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260917.4
- A launch that cannot build the window shows "Depot could not open" with the
  reason and Try Again, instead of nothing at all
- Every launch is written to the journal for Luma Vitals (asked, shown with
  how long it took, slow after 12 s, degraded, failed:
  MESSAGE_ID=9d41c6b70f2e4a58bd3e7c19a06f5b24), and a native crash leaves a
  traceback there through faulthandler
- The update agent and the firmware check can no longer keep the window off
  the screen: an unreachable service leaves its own section empty
- Release gate: tests/os/gate/depot-opens.sh opens Depot from its desktop
  entry in a headless Shell on a fresh install, with fwupd running and while
  first-boot provisioning works, and fails if no window appears

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260917.3
- Depot declares X-GNOME-UsesNotifications, so Settings lists it under
  Notifications and its per-app switches, including Badge App Icon, can be
  reached. It notifies when first-boot apps finish installing.

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260917.2
- Never close when fwupd is running: libfwupd 2.1.7 aborted in g_mutex_clear
  when a FwupdClient made on a worker thread was finalized from a queued
  notification while the window was busy drawing. fwupd is now reached only
  through luma_depot.firmware_probe in a process of its own; a check that
  fails, hangs (90 s) or crashes shows the firmware section's own error state
  with Try Again, and is logged to the journal

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260917.1
- Depot shows release names on the Updates page and the system update card
  ("Luma (Prairie, Beta 0, Nightly 20260917) is ready", "you have Luma
  (Prairie, Beta 0, Nightly 20260916)") from luma-update's new *Name
  properties. For an older luma-update it names versions by the same rules:
  the booted os-release PRETTY_NAME, else derived from the version. "This
  computer" adds a Build row with the machine version.

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260916.6
- Updates page, attention first: one "Needs your attention" section at the
  top for a system update to download or restart for, hardware updates, app
  updates that failed, ask for more or wait for you, paused apps and errors,
  each with its action; otherwise "Everything is up to date" with when it was
  last checked and Check Now. Then Recently updated, this computer and its
  channel, and automatic updates, with long explanations behind disclosures.
  The sidebar badge counts exactly the attention items
- Apps update automatically by default (switch on the Updates page): every
  six hours, not on metered connections, power saving or a low battery (30%);
  open apps keep running and use the new version next time
- Recently updated: 90 days of app updates, automatic or not, with what's new
- Go Back: within 30 days, the latest update of an app can be taken back to the
  build it replaced, through luma-installer-system (polkit) for system
  Flatpaks; a build the source no longer offers is said plainly. Going back
  pauses automatic updates for that app until something newer appears, with
  Resume
- Hardware updates: plain titles and summaries from the device kind (startup
  protection, firmware, modem, ...), what they need, Details with maker, part,
  versions and release notes; security ones get one reminder; never installed
  automatically. Luma's signed firmware block list hides listed releases
  (an empty, signed list ships at /usr/share/luma/firmware/blocklist.json)
- Every automatic action and every app change is journaled for Luma Vitals

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260916.5
- App updates in Depot follow the remote's newest commit instead of naming
  one: flatpak's system helper refuses a pinned commit from anyone but root,
  so every update of a system-installed Flatpak (Discord on the ThinkPad)
  failed with "Can't update to a specific commit without root permissions".
  The resolved commit is still checked before the transaction runs
- Install, update and removal failures say what happened in plain words
  (out of space, no connection, not allowed, not verified), keep the original
  error behind Details, offer Try again on Updates, and write a structured
  journal entry (MESSAGE_ID c4d1a8e27b3f4f6a9e05d6b2a1f7c389) for Luma Vitals
- Canvas ships only with Luma: its Flatpak listing leaves the Depot catalog

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260916.4
- Updates, on a computer that follows no channel: before Start Following,
  say what adopting does. The chosen channel's release becomes the system,
  replaced packages take the channel's versions, added packages stay, and
  the running system is kept to go back to. Missing trust now points at
  updating luma-update (luma-os-remote) rather than an administrator

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260916.3
- Updates: Official, Beta and Nightly are one public choice, with no account
  and no sign-in. Choosing Beta or Nightly says what it means and switches,
  shows progress, then checks for that channel's newest release at once
- A computer that follows no channel starts following any of them the same way
- A computer that still has an early-updates credential says who set it up
  and can remove it while keeping its channel; leaving says Official, not "stable"
- Plain errors when Luma Connect is signed out (SignInRequired) or the update
  service is too old for the request
- Depot: a developer's verified Flatpak listing can say Luma's image already
  ships the same app (Viola), and apps on the Luma remote may take their
  runtimes from Flathub (catalog agent, e29fb914 and 2a8942ad)

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260916.2
- My apps lists "System tools you installed" (packages added with sudo dnf
  install) with Remove, through luma-install-commands' polkit helper (ADR-038).

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260916.1
- An installed app that fails while opening no longer leaves a spinner: the
  launch's startup sequence ends at once, one notification says the app
  couldn't open and why in plain words, and the specifics (missing library,
  signal, exit status, last error lines) go to the journal for Luma Vitals
- AppImages get the runtime's ARGV0 and OWD: the common AppRun picked no
  program without ARGV0 (PrusaSlicer 2.8.1 exited with "Is a directory")
- An application's private home starts with .config, .cache, .local/share and
  .local/state (OrcaSlicer 2.4.2 exited creating ~/.config/OrcaSlicer)
- Requires luma-appimage-compat, so AppImages built on Debian and Ubuntu find
  libbz2.so.1.0, FUSE 2, libcrypt.so.1 and libnsl.so.1
* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260915.6
- Never a dead end: the Official / Beta / Nightly picker is on the Updates
  page in every state, each channel saying what it is, how often it
  changes, how much testing it has had and what it needs
- Say what choosing a channel would do before it happens, and that a
  release which does not start properly puts itself back
- A computer following no channel explains why in one line and offers
  Start Following, or says exactly what it would take when it cannot
- Preferences always render: a control that cannot act is shown off and
  disabled with the reason and what would re-enable it, never as a bare
  "unavailable"; the same for the firmware and apps rows
* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260915.5
- Depot's Updates page is the one place for everything about updates: the
  version this computer runs, its channel, when it last checked and why,
  where updates are hosted (read from the agent, never hard-coded) and
  whether their metadata verified
- Choose Official, Beta or Nightly there, with preview enrolment inline
  through Luma Connect and a clear way to leave
- Download updates automatically, on by default, with what it does said
  plainly; Ignore this version, and the way back from it
- The Updates badge stops counting a version the person ignored

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260915.4
- List every first-party app Luma's image ships (ADR-031): read schema 4 sources and preinstalled_on_luma
- Show Installed with Luma, Removed with Restore, and Restart to Finish from the booted rpm-ostree deployments
- Remove and restore image apps through the system helper's new override-remove and override-reset
- Never offer the Flatpak of an app whose image package is present, and list each app once
- Restart to finish through the session manager, using logind only without one when the update agent's Apply() does not apply
- Carry the luma-update review fixes of preview20260915.3

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260915.3
- Restart to finish updating without a timeout, and never through the
  update agent after the person cancels or the session refuses; only a
  missing session manager falls back to Apply()
- Show errors from the update agent (the reply callback no longer fails)
- Show Cancel while the system update downloads, Download for every
  available update, and a waiting barrier release instead of up to date
- Say honestly that counts arrive from this computer's network address and,
  on early updates, could be connected to the Luma account

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260915.2
- Rebuild from the launch integration branch with the Depot distribution
  tooling and the update agent merged; the client's source is unchanged, so
  the build does not share a NEVRA with the earlier preview20260915.1 file

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.31.preview20260915.1
- Read the signed schema 4 catalogue, verified with the shipped minisign key, falling back to the last verified copy and then the seed
- Install, update and remove apps from the Luma remote with the same commit pinning as Flathub, and show Flatpak app updates
- Show tiers, verified developers, permissions computed from sandbox metadata, permission changes on update and sign-in disclosure
- Show verified screenshots, ratings and reviews, and write reviews with this computer's Luma Connect sign-in
- Open luma-depot:// and appstream:// links, and collections on Home
- Count installs and active computers anonymously, with both switches in Settings
- Install the apps chosen during installation on first login, in the background
- Make the Updates tab the one place for updates: the system update from Luma's update agent, firmware from fwupd, and apps
- Update apps every six hours in the background, holding any update that asks for more until it is reviewed
- Add a Software Update entry that opens Depot's Updates tab

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.30.preview20260914.17
- Keep each application's SELinux categories between launches so its data is not relabelled every time
- Let applications sandbox their own tools with bubblewrap inside their capsule

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.30.preview20260914.16
- Find an AppImage's application where its runtime ends, validating the SquashFS superblock, instead of at the first matching bytes
- Unpack DwarFS and type 1 AppImages with their own runtime in a sandbox with no network

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.30.preview20260914.15
- Share the person's Documents, Downloads and other folders with every application at their real paths, so saving from a file chooser writes where the person chose

* Fri Sep 11 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.30.preview20260912.13
- Reach a console application by name from the shell, not only from an icon
- Keep a package's Terminal declaration instead of launching it into a closed pipe
- Refuse to publish a command name that would shadow one the user already has
- Pass arguments through the capsule boundary to the payload

* Fri Sep 04 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.17
- The application installer is the Application Kit's window

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.16
- Run boot reconciliation even when interruption occurred before receipt creation

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.15
- Serialize identical user transactions and all authenticated system transactions
- Reconcile abandoned user partials only after proving their process lock is free
- Remove root-owned interrupted transaction directories during boot reconciliation

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.14
- Reject staging and capsule transactions before exhausting their destination filesystem
- Extract AppImages through a private partial directory and remove incomplete payloads
- Remove newly committed capsule images when launcher publication cannot complete

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.13
- Route each newly opened package into a fresh review window

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.12
- Activate snapd-owned mounts during normal boot across immutable deployments
- Reconcile completed system-RPM removals after the deployment switch

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.11
- Reconstruct snapd-owned mount target dependencies after immutable /etc merges

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.10
- Route an already-installed Flatpak reference through Flatpak update semantics

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.9
- Record Flatpak, Snap, and system-RPM transactions alongside local capsules
- Add ecosystem-preserving list, update, and removal lifecycle commands
- Preserve the conventional /home account contract on Fedora Atomic images

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.8
- Validate privileged inputs against the owning account's canonical home path
- Accept Fedora Atomic's canonical /var/home staging location without weakening isolation

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.7
- Keep Snap state below /var and remove the unsupported immutable-root /snap payload

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.6
- Stop completed rootless install containers before committing their image

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.5
- Map desktop sockets into a fixed private runtime path inside each capsule
- Add canonical rootless backend acceptance fixtures for AppImage, DEB, and RPM

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- Distinguish RPM publisher signatures from payload integrity digests
- Describe application-RPM scriptlets at their actual rootless capsule boundary

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Include Fedora's sbin locations in the bounded package-tool search path

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Persist Snap's Silverblue mount boundary only when the first Snap is installed
- Reject AppImage icon symlinks that escape the extracted application payload

* Sat Aug 29 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Add one review and transaction boundary for native and compatibility packages
- Preserve Flatpak, Snap, RPM, DEB, AppImage, Android, and Windows semantics
- Add bounded static inspection, architecture checks, fingerprinted staging,
  rootless Debian capsules, sandboxed AppImages, and a narrow polkit helper
