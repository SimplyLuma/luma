# SPDX-License-Identifier: Apache-2.0
Name:           luma-rpi5-platform
Version:        0.1.0
Release:        1.luma.1%{?dist}
Summary:        Raspberry Pi 5 platform support for Luma
License:        Apache-2.0
URL:            https://github.com/SimplyLuma/luma
Source0:        luma-rpi5-platform.tar.gz
Source1:        LICENSE.md

BuildArch:      noarch
BuildRequires:  glib2
BuildRequires:  python3
BuildRequires:  systemd-rpm-macros
BuildRequires:  systemd-udev
# Target of the Wi-Fi NVRAM alias.
Requires:       brcmfmac-firmware
# glib-compile-schemas for the Shell defaults, grub2-editenv for boot-next.
Requires:       glib2
Requires:       grub2-tools-minimal
Requires:       util-linux

%description
Raspberry Pi 5 support for the generic Luma ARM image: the Wi-Fi NVRAM alias
D0 boards ask for, a V3D runtime-PM delay that keeps the GPU awake between
frames, Shell defaults suited to the Pi's GPU (no live backdrop blur), and a
tool that chooses the SD card boot menu entry for the next boot. Everything
is gated on the Pi 5 device tree and is inert on other hardware.

%prep
%autosetup -n luma-rpi5-platform
cp %{SOURCE1} LICENSE.md

%build

%install
install -D -m 0644 70-luma-rpi5-v3d.rules %{buildroot}%{_udevrulesdir}/70-luma-rpi5-v3d.rules
install -D -m 0644 rpi5.gschema.override %{buildroot}%{_datadir}/luma-rpi5/rpi5.gschema.override
install -D -m 0755 luma-rpi5-shell-defaults %{buildroot}%{_libexecdir}/luma-rpi5-shell-defaults
install -D -m 0644 luma-rpi5-shell-defaults.service %{buildroot}%{_unitdir}/luma-rpi5-shell-defaults.service
# Enabled by the package itself: a preset applies only on first install.
install -d %{buildroot}%{_unitdir}/sysinit.target.wants
ln -s ../luma-rpi5-shell-defaults.service \
  %{buildroot}%{_unitdir}/sysinit.target.wants/luma-rpi5-shell-defaults.service
install -D -m 0755 62-luma-rpi5-schemas \
  %{buildroot}%{_prefix}/lib/systemd/user-environment-generators/62-luma-rpi5-schemas
%{_prefix}/lib/systemd/user-environment-generators/63-luma-rpi5-gsk-renderer
install -D -m 0755 63-luma-rpi5-gsk-renderer \
  %{buildroot}%{_prefix}/lib/systemd/user-environment-generators/63-luma-rpi5-gsk-renderer
install -D -m 0755 luma-rpi5-boot-next %{buildroot}%{_bindir}/luma-rpi5-boot-next
install -d %{buildroot}%{_prefix}/lib/firmware/brcm
ln -s brcmfmac43455-sdio.raspberrypi,5-model-b.txt.xz \
  "%{buildroot}%{_prefix}/lib/firmware/brcm/brcmfmac43455-sdio.raspberrypi,5-model-b-d0.txt.xz"

%check
%{python3} -B -m unittest discover -s tests -p 'test_*.py' -v
# Never a global override: the Pi defaults must not reach other hardware.
test -z "$(find %{buildroot}%{_datadir}/glib-2.0 -name '*.gschema.override' 2>/dev/null)"
grep -Fqx 'ConditionFirmware=|device-tree-compatible(raspberrypi,5-model-b)' \
  %{buildroot}%{_unitdir}/luma-rpi5-shell-defaults.service
test "$(readlink '%{buildroot}%{_prefix}/lib/firmware/brcm/brcmfmac43455-sdio.raspberrypi,5-model-b-d0.txt.xz')" = \
  'brcmfmac43455-sdio.raspberrypi,5-model-b.txt.xz'
udevadm verify --no-style %{buildroot}%{_udevrulesdir}/70-luma-rpi5-v3d.rules

%files
%license LICENSE.md
%doc README.md
%{_udevrulesdir}/70-luma-rpi5-v3d.rules
%{_datadir}/luma-rpi5/
%{_libexecdir}/luma-rpi5-shell-defaults
%{_unitdir}/luma-rpi5-shell-defaults.service
%{_unitdir}/sysinit.target.wants/luma-rpi5-shell-defaults.service
%{_prefix}/lib/systemd/user-environment-generators/62-luma-rpi5-schemas
%{_bindir}/luma-rpi5-boot-next
"%{_prefix}/lib/firmware/brcm/brcmfmac43455-sdio.raspberrypi,5-model-b-d0.txt.xz"

%changelog
* Sun Nov 01 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Wi-Fi NVRAM alias for D0 boards, V3D autosuspend delay, Shell defaults
  without live blur, and SD card boot-next tool.
