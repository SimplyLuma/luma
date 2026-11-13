# SPDX-License-Identifier: Apache-2.0
Name:           luma-fp6-platform
Version:        0.1.0
Release:        1.luma.20%{?dist}
Summary:        Fairphone 6 platform services for Luma
License:        Apache-2.0
URL:            https://github.com/SimplyLuma/luma
Source0:        luma-fp6-platform.tar.gz
Source1:        LICENSE.md

BuildArch:      noarch
BuildRequires:  systemd-rpm-macros
Requires:       ModemManager >= 1.25.95
Requires:       NetworkManager
Requires:       alsa-utils
Requires:       bash
Requires:       bluez
Requires:       coreutils
Requires:       dconf
Requires:       findutils
Requires:       gawk
Requires:       grep
Requires:       iproute
Requires:       luma-fp6-carrier-adapter >= 0.1.0-1.luma.13
Requires:       luma-imsd
Requires:       psmisc
Requires:       python3
Requires:       python3-gobject-base
Requires:       qbootctl
Requires:       rmtfs
Requires:       systemd
Requires:       tqftpserv
Requires:       util-linux
Requires:       wireplumber

%description
Fairphone 6 platform services on the shared Luma ARM image: cellular link,
resume and safe modem shutdown, call audio routing and the sound card's UCM
profile, the Bluetooth address from the persist partition, the camera GPU
guard, the wall clock kept across shutdown, A/B boot-slot success marking,
and the handheld device class with the shared handheld defaults. Installed
from the Fairphone 6 device repository as a client-side layer.

%prep
%autosetup -n luma-fp6-platform
cp %{SOURCE1} LICENSE.md

%build

%install
cp -a root/. %{buildroot}/
install -D -m 0644 luma-shell-handheld.dconf %{buildroot}%{_sysconfdir}/dconf/db/luma.d/10-luma-handheld
install -D -m 0644 luma-greeter-handheld.dconf %{buildroot}%{_sysconfdir}/dconf/db/gdm.d/10-luma-handheld
# Enabled by the package itself: a preset applies only on first install, and
# rmtfs and tqftpserv come from other packages.
install -d %{buildroot}%{_unitdir}/multi-user.target.wants %{buildroot}%{_unitdir}/sysinit.target.wants \
  %{buildroot}%{_unitdir}/timers.target.wants
for unit in luma-fp6-audio-route luma-fp6-bluetooth-address luma-fp6-boot-success luma-fp6-camera-gpu-guard \
            luma-fp6-cellular-link luma-fp6-modem-quiesce luma-fp6-primary-gw-session luma-fp6-imsd \
            rmtfs tqftpserv; do
  ln -s ../$unit.service %{buildroot}%{_unitdir}/multi-user.target.wants/$unit.service
done
ln -s ../luma-clock-restore.service %{buildroot}%{_unitdir}/sysinit.target.wants/luma-clock-restore.service
ln -s ../luma-clock-save.timer %{buildroot}%{_unitdir}/timers.target.wants/luma-clock-save.timer

%check
grep -Fxq 'LUMA_DEVICE_CLASS=handheld' %{buildroot}%{_prefix}/lib/environment.d/90-luma-handheld.conf
grep -Fxq 'automatic_download = false' %{buildroot}%{_sysconfdir}/luma/update.conf
grep -Fxq 'ConditionFirmware=device-tree-compatible(fairphone,fp6)' %{buildroot}%{_unitdir}/luma-fp6-boot-success.service
test ! -s %{buildroot}%{_unitdir}/boot.automount
grep -Fxq 'wifi.powersave=2' %{buildroot}%{_prefix}/lib/NetworkManager/conf.d/50-luma-fp6-wifi.conf
grep -Fxq 'MemorySleepMode=s2idle' %{buildroot}%{_prefix}/lib/systemd/sleep.conf.d/50-luma-fp6.conf
grep -Fq 'ACTION=="add|bind", SUBSYSTEM=="platform", KERNEL=="890000.serial", ATTR{power/wakeup}="disabled"' %{buildroot}%{_prefix}/lib/udev/rules.d/60-luma-fp6-wakeup.rules
# The login screen keeps the signed-in session's power defaults.
for key in power-button-action sleep-inactive-ac-type sleep-inactive-battery-type color-scheme; do
  line=$(grep -x "$key='[a-z-]*'" %{buildroot}%{_sysconfdir}/dconf/db/luma.d/10-luma-handheld)
  grep -Fxq "$line" %{buildroot}%{_sysconfdir}/dconf/db/gdm.d/10-luma-handheld
done
grep -Fxq 'power-static=@as []' %{buildroot}%{_sysconfdir}/dconf/db/gdm.d/10-luma-handheld
if grep -rIl -e greetd -e phosh -e stevia %{buildroot}%{_unitdir} %{buildroot}%{_libexecdir}; then exit 1; fi
test ! -e %{buildroot}%{_prefix}/lib/firmware
test ! -e %{buildroot}%{_prefix}/lib/greenboot

%posttrans
dconf update || :

%files
%license LICENSE.md
%doc README.md
%config(noreplace) %{_sysconfdir}/luma-device-class
%config(noreplace) %{_sysconfdir}/luma/fp6-cellular-link.conf
%config(noreplace) %{_sysconfdir}/luma/update.conf
%config(noreplace) %{_sysconfdir}/dconf/db/luma.d/10-luma-handheld
%config(noreplace) %{_sysconfdir}/dconf/db/gdm.d/10-luma-handheld
%config(noreplace) %{_sysconfdir}/systemd/user/wireplumber.service.d/50-luma-fp6-camera.conf
%config(noreplace) %{_sysconfdir}/wireplumber/wireplumber.conf.d/51-luma-fp6-audio.conf
%{_prefix}/lib/environment.d/90-luma-handheld.conf
%{_prefix}/lib/NetworkManager/conf.d/50-luma-fp6-wifi.conf
%{_prefix}/lib/systemd/system-preset/60-luma-fp6-platform.preset
%{_prefix}/lib/systemd/sleep.conf.d/50-luma-fp6.conf
%{_prefix}/lib/systemd/system-sleep/90-luma-fp6-cellular
%{_prefix}/lib/tmpfiles.d/luma-fp6-clock.conf
%{_prefix}/lib/udev/rules.d/60-luma-fp6-wakeup.rules
%{_unitdir}/boot.automount
%{_unitdir}/luma-clock-restore.service
%{_unitdir}/luma-clock-save.service
%{_unitdir}/luma-clock-save.timer
%{_unitdir}/luma-fp6-*.service
%{_unitdir}/multi-user.target.wants/*.service
%{_unitdir}/sysinit.target.wants/luma-clock-restore.service
%{_unitdir}/timers.target.wants/luma-clock-save.timer
%{_libexecdir}/luma-fp6-*
%{_datadir}/alsa/ucm2/Fairphone/
"%{_datadir}/alsa/ucm2/conf.d/milos/Fairphone (Gen. 6).conf"
%{_datadir}/dbus-1/system-services/org.projectluma.AudioRoute1.service
%{_datadir}/dbus-1/system.d/org.projectluma.AudioRoute1.conf

%changelog
* Fri Nov 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.20
- The cellular link requests a dual-stack data bearer (ip-type=ipv4v6) and
  configures each IP family the network grants; an IPv6-only bearer gets an
  IPv6 link with its default route instead of being ignored.
- It leaves the data link to NetworkManager while NetworkManager's own GSM
  connection is activating or active.
- A failed command logs the mmcli option and ModemManager's error.
- Requires luma-fp6-carrier-adapter >= 0.1.0-1.luma.13 (data APN from the
  carrier database).

* Fri Nov 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.19
- The login screen leaves the power key to the Shell (media-keys
  power-static empty), so a short press blanks and wakes it.

* Wed Nov 11 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.18
- The cellular link follows the mobile data switch: NetworkManager's WWAN
  state and the Mobile Network panel's per-SIM data setting. Off disconnects
  only the data bearer; on by default.

* Wed Nov 11 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.17
- The Bluetooth UART wakeup rule also matches the coldplug add event: on a
  normal boot the driver binds before udev starts, so the rule never ran.

* Tue Nov 10 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.16
- System suspend works: s2idle by default (the firmware returns at once from
  deep), and the Bluetooth UART RX line is no longer a wakeup source (it
  aborted every suspend attempt).

* Tue Nov 10 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.15
- Wi-Fi power save off: the link stopped receiving after a few minutes idle.

* Tue Nov 10 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.14
- The login screen uses the handheld default appearance (dark), as the
  signed-in session does.

* Tue Nov 10 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.13
- The login screen gets the handheld power defaults (gdm dconf profile): no
  system suspend when idle and no power-off dialog on a short power press.

* Wed Nov 04 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.12
- For the shared ARM image: installed from the Fairphone 6 device repository
  as a client-side layer. Drops the Phosh, greetd and Stevia session pieces,
  the luma greenboot A/B marker and the firmware links into /var (firmware is
  luma-fp6-firmware in /usr/lib/firmware).
- Units enabled by wants links in /usr, rmtfs and tqftpserv included.
- luma-fp6-boot-success.service marks the boot slot successful (qbootctl -m).
- Handheld device class (/etc/luma-device-class, environment.d) and the shared
  handheld dconf defaults; updates on request only.
- luma-clock-restore runs before the display manager instead of greetd.

* Mon Oct 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.11
- Fairphone 6 Alpha platform services.
