# SPDX-License-Identifier: Apache-2.0
# Bluetooth patch RAM for the Broadcom/Cypress combo chips on Raspberry Pi
# boards. linux-firmware and Fedora do not carry these; Raspberry Pi
# publishes them in RPi-Distro/bluez-firmware (Debian's bluez-firmware takes
# the same files). Raspberry Pi states the linux-firmware LICENCE.cypress
# applies (Phil Elwell, 2021-09-06, recorded in that repo's debian/copyright):
# object-code redistribution for use with Cypress chips, the licence Fedora's
# brcmfmac-firmware already ships the Wi-Fi half of the same chips under.
%global commit cdf61dc691a49ff01a124752bd04194907f0f9cd

Name:           luma-rpi-bluetooth-firmware
Version:        0
Release:        1.20251002git%(c=%{commit}; echo ${c:0:7}).luma.1%{?dist}
Summary:        Bluetooth firmware for Raspberry Pi boards (CYW43455, CYW43438)
License:        LicenseRef-Fedora-Firmware
URL:            https://github.com/RPi-Distro/bluez-firmware
Source0:        https://github.com/RPi-Distro/bluez-firmware/archive/%{commit}/bluez-firmware-%{commit}.tar.gz
Source1:        LicenseRef-Cypress-Wireless-Firmware.txt
BuildArch:      noarch

%description
Bluetooth patch RAM (.hcd) that hci_bcm loads into the Broadcom/Cypress
Wi-Fi/Bluetooth chips of Raspberry Pi 3 B+/A+, 4, 400, 5, 500, CM4 and CM5,
with the board-named aliases the kernel asks for first.

%prep
%setup -q -n bluez-firmware-%{commit}
cp %{SOURCE1} LICENCE.cypress

%build

%install
fw=%{buildroot}/usr/lib/firmware/brcm
install -d "$fw"
for f in BCM4345C0 BCM4345C5 BCM43430A1 BCM43430B0 BCM4343A2; do
  install -m 0644 debian/firmware/broadcom/$f.hcd "$fw/$f.hcd"
done
# Board aliases (debian/bluez-firmware.links), Cypress-licensed files only.
while read -r target link; do
  case "$target" in /usr/lib/firmware/brcm/*) ;; *) continue ;; esac
  ln -s "${target##*/}" "%{buildroot}$link"
done <debian/bluez-firmware.links

%check
test -s %{buildroot}/usr/lib/firmware/brcm/BCM4345C0.hcd
test -L "%{buildroot}/usr/lib/firmware/brcm/BCM4345C0.raspberrypi,5-model-b.hcd"
echo '51c45e77ddad91a19e96dc8fb75295b2087c279940df2634b23baf71b6dea42c  %{buildroot}/usr/lib/firmware/brcm/BCM4345C0.hcd' | sha256sum -c -

%files
%license LICENCE.cypress
/usr/lib/firmware/brcm/*.hcd

%changelog
* Mon Nov 02 2026 Project Luma <maintainers@projectluma.org> - 0-1.20251002gitcdf61dc.luma.1
- RPi-Distro/bluez-firmware pios/trixie at cdf61dc.
