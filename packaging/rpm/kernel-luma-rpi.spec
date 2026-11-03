# SPDX-License-Identifier: GPL-2.0-only
# Luma Raspberry Pi kernel: Raspberry Pi's kernel tree (github.com/raspberrypi/
# linux, branch in config/kernel/rpi-7.2.json) with Fedora's configuration for
# the same series plus Raspberry Pi's bcm2711_defconfig options (Pi 3, 4, 400,
# 5, 500, CM4, CM5; 4K pages). Built for the Raspberry Pi image only; every
# other aarch64 device keeps Fedora's kernel.
%global debug_package %{nil}
%global _missing_build_ids_terminate_build 0
%global __provides_exclude_from ^/usr/lib/modules/.*$
%global __requires_exclude_from ^/usr/lib/modules/.*$
# modules_install strips, signs and compresses; RPM must not strip again.
%global __spec_install_post %{nil}
%global rpi_rev dbb0de2b7b0496028bf81d9acdac93bf7ec036b1
%global kver %{version}-%{release}.aarch64

Name: kernel-luma-rpi
Version: 7.2.15
Release: 1.luma.rpi%{?dist}
Summary: Linux for Raspberry Pi boards, from Raspberry Pi's kernel tree
License: GPL-2.0-only AND GPL-2.0-or-later AND (GPL-2.0-only OR MIT) AND (GPL-2.0-or-later OR MIT) AND (GPL-2.0-only OR BSD-2-Clause) AND (GPL-2.0-or-later OR BSD-3-Clause)
URL: https://github.com/raspberrypi/linux
ExclusiveArch: aarch64
Source0: rpi-linux-%{rpi_rev}.tar.gz
Source1: luma-rpi-kernel-sources.tar.gz
Source2: fedora-base.config
BuildRequires: bash bc binutils bison dwarves elfutils-libelf-devel flex gcc
BuildRequires: make openssl openssl-devel perl python3 rsync rpm-build kmod file dtc
BuildRequires: rust rust-src bindgen-cli clang
Requires: kmod
Provides: kernel = %{version}-%{release}
Provides: kernel-core = %{version}-%{release}
Provides: kernel-modules = %{version}-%{release}
Provides: kernel-modules-core = %{version}-%{release}
Provides: kernel-modules-extra = %{version}-%{release}
Provides: kernel-uname-r = %{kver}
Provides: kernel-core-uname-r = %{kver}
Provides: kernel-modules-uname-r = %{kver}
Provides: kernel-modules-core-uname-r = %{kver}
Provides: kernel-modules-extra-uname-r = %{kver}

%description
Raspberry Pi's Linux tree (rpi-7.2.y) built with Fedora's kernel configuration
plus every option of Raspberry Pi's bcm2711_defconfig: RP1 (USB, Ethernet,
GPIO, PWM, DSI/DPI, camera front end), CYW43455 Bluetooth on the BCM2712 UART,
the HEVC decoder (V4L2 stateless), HDMI audio and CEC, PiSP camera back end,
fan, RTC, power button and the HAT/overlay drivers. Ships Raspberry Pi's
device trees and overlays (dtb/broadcom, dtb/overlays) for the board firmware.

%prep
%setup -q -c -n luma-rpi-kernel -a 1
mv linux-%{rpi_rev} linux

%build
export ARCH=arm64 CROSS_COMPILE="%{?_luma_cross_compile}"
export KBUILD_BUILD_USER=luma KBUILD_BUILD_HOST=builder
export KBUILD_BUILD_TIMESTAMP='2026-11-02 00:00:00 UTC'
source_dir="%{?_luma_prepared_source}%{!?_luma_prepared_source:$PWD/linux}"
output_dir="%{?_luma_prepared_output}%{!?_luma_prepared_output:$PWD/kernel-build}"
%if ! 0%{?_luma_prepared_output:1}
bash scripts/kernel/configure-rpi.sh "$source_dir" "$output_dir" %{SOURCE2}
make -C "$source_dir" O="$output_dir" %{?_smp_mflags} all
%endif
test "$(make -s -C "$source_dir" O="$output_dir" kernelrelease)" = %{kver}

%install
export ARCH=arm64 CROSS_COMPILE="%{?_luma_cross_compile}"
source_dir="%{?_luma_prepared_source}%{!?_luma_prepared_source:$PWD/linux}"
output_dir="%{?_luma_prepared_output}%{!?_luma_prepared_output:$PWD/kernel-build}"
moddir=%{buildroot}/usr/lib/modules/%{kver}
make -C "$source_dir" O="$output_dir" %{?_smp_mflags} \
    INSTALL_MOD_PATH=%{buildroot}/usr INSTALL_MOD_STRIP=1 DEPMOD=true modules_install
make -C "$source_dir" O="$output_dir" INSTALL_DTBS_PATH="$moddir/dtb" dtbs_install
# Only Raspberry Pi boards: the tree builds every vendor's DTBs.
find "$moddir/dtb" -mindepth 1 -maxdepth 1 -type d ! -name broadcom ! -name overlays -exec rm -rf {} +
install -m 0644 "$output_dir/arch/arm64/boot/vmlinuz.efi" "$moddir/vmlinuz"
install -m 0644 "$output_dir/.config" "$moddir/config"
install -m 0644 "$output_dir/System.map" "$moddir/System.map"
rm -f "$moddir/build" "$moddir/source"
install -d -m 0755 %{buildroot}/usr/share/luma/kernel
install -m 0644 config/kernel/rpi-7.2.json %{buildroot}/usr/share/luma/kernel/rpi-source-lock.json
install -m 0644 "$output_dir/luma-config-exceptions.txt" %{buildroot}/usr/share/luma/kernel/rpi-config-exceptions.txt
install -d -m 0755 %{buildroot}/usr/share/licenses/%{name}
install -m 0644 "$source_dir/COPYING" "$source_dir/LICENSES/preferred/GPL-2.0" \
    "$source_dir/LICENSES/exceptions/Linux-syscall-note" %{buildroot}/usr/share/licenses/%{name}/

%check
moddir=%{buildroot}/usr/lib/modules/%{kver}
file "$moddir/vmlinuz" | grep -q 'for EFI (application)'
for f in broadcom/bcm2712-rpi-5-b.dtb broadcom/bcm2712d0-rpi-5-b.dtb broadcom/bcm2712-rpi-500.dtb \
         broadcom/bcm2712-rpi-cm5-cm5io.dtb broadcom/bcm2711-rpi-4-b.dtb broadcom/bcm2711-rpi-400.dtb \
         overlays/overlay_map.dtb overlays/hifiberry-dacplus.dtbo overlays/vc4-kms-v3d-pi5.dtbo; do
  test -s "$moddir/dtb/$f" || { echo "missing dtb/$f" >&2; exit 1; }
done
test "$(find "$moddir/dtb/overlays" -name '*.dtbo' | wc -l)" -gt 300
for m in kernel/drivers/media/platform/raspberrypi/hevc_dec/rpi-hevc-dec.ko.xz \
         kernel/drivers/media/platform/raspberrypi/pisp_be/pisp-be.ko.xz \
         kernel/drivers/hwmon/pwm-fan.ko.xz kernel/drivers/gpu/drm/vc4/vc4.ko.xz; do
  test -s "$moddir/$m" || { echo "missing $m" >&2; exit 1; }
done
for b in 8250_bcm7271 rtc-rpi; do
  grep -q "/$b.ko\$" "$moddir/modules.builtin" || { echo "not built in: $b" >&2; exit 1; }
done
grep -Fxq 'CONFIG_SECURITY_SELINUX=y' "$moddir/config"
grep -Fxq 'CONFIG_ARM64_4K_PAGES=y' "$moddir/config"

%post
/usr/sbin/depmod %{kver} || exit $?

%files
%defattr(0644,root,root,0755)
%license /usr/share/licenses/%{name}
/usr/lib/modules/%{kver}
/usr/share/luma/kernel/rpi-source-lock.json
/usr/share/luma/kernel/rpi-config-exceptions.txt

%changelog
* Mon Nov 02 2026 Project Luma <maintainers@projectluma.org> - 7.2.15-1.luma.rpi
- Raspberry Pi rpi-7.2.y at dbb0de2b7b04 with Fedora 7.2.11's configuration.
