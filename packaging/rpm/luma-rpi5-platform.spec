# SPDX-License-Identifier: Apache-2.0
Name:           luma-rpi5-platform
Version:        0.3.0
Release:        1.luma.1%{?dist}
Summary:        Raspberry Pi 5 platform support for Luma
License:        Apache-2.0
URL:            https://github.com/SimplyLuma/luma
Source0:        luma-rpi5-platform.tar.gz
Source1:        LICENSE.md

BuildArch:      noarch
BuildRequires:  python3
BuildRequires:  systemd-rpm-macros
BuildRequires:  systemd-udev
# Target of the Wi-Fi NVRAM alias.
Requires:       brcmfmac-firmware
# grub2-editenv for boot-next.
Requires:       grub2-tools-minimal
# The Raspberry Pi 5 update stream (update.conf).
Requires:       luma-update >= 1.0.0-1.luma.27
Requires:       util-linux

%description
Raspberry Pi 5 support for the Luma Pi image: the Wi-Fi NVRAM alias D0
boards ask for, a V3D runtime-PM delay that keeps the GPU awake between
frames, GTK's GL renderer, a tool that chooses the SD card boot menu entry
for the next boot, a service that copies the kernel's device trees to the
card, and the Pi image's update stream. Everything except the update stream
is gated on the Pi 5 device tree.

%prep
%autosetup -n luma-rpi5-platform
cp %{SOURCE1} LICENSE.md

%build

%install
install -D -m 0644 70-luma-rpi5-v3d.rules %{buildroot}%{_udevrulesdir}/70-luma-rpi5-v3d.rules
install -D -m 0755 63-luma-rpi5-gsk-renderer \
  %{buildroot}%{_prefix}/lib/systemd/user-environment-generators/63-luma-rpi5-gsk-renderer
install -D -m 0755 luma-rpi5-boot-next %{buildroot}%{_bindir}/luma-rpi5-boot-next
install -D -m 0644 update.conf %{buildroot}%{_sysconfdir}/luma/update.conf
install -D -m 0755 luma-rpi5-firmware-sync %{buildroot}%{_libexecdir}/luma-rpi5-firmware-sync
install -D -m 0644 luma-rpi5-firmware-sync.service %{buildroot}%{_unitdir}/luma-rpi5-firmware-sync.service
# Enabled by the package itself: a preset applies only on first install.
install -d %{buildroot}%{_unitdir}/multi-user.target.wants
ln -s ../luma-rpi5-firmware-sync.service \
  %{buildroot}%{_unitdir}/multi-user.target.wants/luma-rpi5-firmware-sync.service
install -d %{buildroot}%{_prefix}/lib/firmware/brcm
ln -s brcmfmac43455-sdio.raspberrypi,5-model-b.txt.xz \
  "%{buildroot}%{_prefix}/lib/firmware/brcm/brcmfmac43455-sdio.raspberrypi,5-model-b-d0.txt.xz"

%check
%{python3} -B -m unittest discover -s tests -p 'test_*.py' -v
grep -Fqx 'ConditionFirmware=|device-tree-compatible(raspberrypi,5-model-b)' \
  %{buildroot}%{_unitdir}/luma-rpi5-firmware-sync.service
test "$(readlink '%{buildroot}%{_prefix}/lib/firmware/brcm/brcmfmac43455-sdio.raspberrypi,5-model-b-d0.txt.xz')" = \
  'brcmfmac43455-sdio.raspberrypi,5-model-b.txt.xz'
udevadm verify --no-style %{buildroot}%{_udevrulesdir}/70-luma-rpi5-v3d.rules

%files
%license LICENSE.md
%doc README.md
%{_udevrulesdir}/70-luma-rpi5-v3d.rules
%{_prefix}/lib/systemd/user-environment-generators/63-luma-rpi5-gsk-renderer
%{_bindir}/luma-rpi5-boot-next
%config(noreplace) %{_sysconfdir}/luma/update.conf
%{_libexecdir}/luma-rpi5-firmware-sync
%{_unitdir}/luma-rpi5-firmware-sync.service
%{_unitdir}/multi-user.target.wants/luma-rpi5-firmware-sync.service
"%{_prefix}/lib/firmware/brcm/brcmfmac43455-sdio.raspberrypi,5-model-b-d0.txt.xz"

%changelog
* Tue Nov 03 2026 Project Luma <maintainers@projectluma.org> - 0.3.0-1.luma.1
- Drop the Shell defaults service: backdrop blur is cheap enough on the Pi 5
  now, so it keeps the stock Shell defaults.
- Follow the Raspberry Pi 5 update stream (/etc/luma/update.conf).

* Mon Nov 02 2026 Project Luma <maintainers@projectluma.org> - 0.2.0-1.luma.1
- luma-rpi5-firmware-sync: copy the Raspberry Pi kernel's device trees and
  overlays to the boot partition after a good boot.

* Sun Nov 01 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Wi-Fi NVRAM alias for D0 boards, V3D autosuspend delay, Shell defaults
  without live blur, and SD card boot-next tool.
