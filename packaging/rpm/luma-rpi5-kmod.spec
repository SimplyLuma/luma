# SPDX-License-Identifier: GPL-2.0-only
# Raspberry Pi 5 kernel modules for one Fedora kernel. Rebuilt for every
# kernel the ARM image ships (config/kernel/rpi5-kmod.env); the package
# requires that exact kernel, so an image cannot pair them wrongly.
%{!?kernel_nvr: %global kernel_nvr 7.2.11-200.fc44}
%global kver %{kernel_nvr}.aarch64
%global kernel_rel %(echo %{kernel_nvr} | tr - _)
%global debug_package %{nil}
%global __provides_exclude_from ^/usr/lib/modules/.*$
%global __requires_exclude_from ^/usr/lib/modules/.*$
# The modules are stripped and compressed in %%install.
%global __spec_install_post %{nil}

Name:           luma-rpi5-kmod
Version:        0.1.0
Release:        1.luma.1.k%{kernel_rel}
Summary:        Raspberry Pi 5 kernel modules for Linux %{kver}
License:        GPL-2.0-only AND GPL-2.0-or-later
URL:            https://github.com/SimplyLuma/luma
ExclusiveArch:  aarch64
Source0:        luma-rpi5-kmod.tar.gz

BuildRequires:  elfutils-libelf-devel
BuildRequires:  gcc
BuildRequires:  kernel-devel-uname-r = %{kver}
BuildRequires:  kmod
BuildRequires:  make
BuildRequires:  xz
Requires:       kernel-uname-r = %{kver}
Requires(post): kmod
Requires(postun): kmod
Provides:       luma-rpi5-kmod-uname-r = %{kver}

%description
HDMI audio for BCM2712 boards (Raspberry Pi 5, Pi 500, Compute Module 5) on
the Fedora kernel %{kver}: bcm2712-dma, Raspberry Pi's BCM2712 DMA engine
driver, and vc4 rebuilt from this kernel's own source with the BCM2712 HDMI
audio address fix. The modules go to updates/, ahead of the in-tree vc4.
They do nothing until the device tree describes the BCM2712 DMA controller,
which the Luma Pi 5 SD card's device trees carry (luma-rpi5-hdmi-audio).

%prep
%setup -q -n luma-rpi5-kmod

%build
make -C %{_usrsrc}/kernels/%{kver} M=$PWD %{?_smp_mflags} modules

%install
dest=%{buildroot}/usr/lib/modules/%{kver}/updates/luma-rpi5
install -d "$dest"
for module in drivers/dma/bcm2712-dma.ko drivers/gpu/drm/vc4/vc4.ko; do
  install -m 0644 "$module" "$dest/"
  strip --strip-debug "$dest/${module##*/}"
  xz --check=crc32 --lzma2=dict=1MiB -T1 "$dest/${module##*/}"
done

%check
for module in %{buildroot}/usr/lib/modules/%{kver}/updates/luma-rpi5/*.ko.xz; do
  test "$(modinfo -F vermagic "$module" | cut -d' ' -f1)" = %{kver}
done
modinfo -F alias %{buildroot}/usr/lib/modules/%{kver}/updates/luma-rpi5/bcm2712-dma.ko.xz |
  grep -qx 'of:N\*T\*Cbrcm,bcm2712-dma'
# Never claims the in-tree bcm2835-dma devices.
! modinfo -F alias %{buildroot}/usr/lib/modules/%{kver}/updates/luma-rpi5/bcm2712-dma.ko.xz |
  grep -q 'bcm2835-dma\|bcm2711-dma'

%post
depmod -a %{kver} >/dev/null 2>&1 || :

%postun
depmod -a %{kver} >/dev/null 2>&1 || :

%files
%doc README.md SOURCES.manifest
/usr/lib/modules/%{kver}/updates/luma-rpi5/

%changelog
* Mon Nov 02 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- bcm2712-dma and vc4 with BCM2712 HDMI audio for the Fedora kernel.
