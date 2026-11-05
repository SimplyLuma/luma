# SPDX-License-Identifier: Apache-2.0
# Kernel modules of the Fairphone 6 kernel (SimplyLuma/linux tag
# luma-fp6-alpha1-7.1.2-luma-fp-ims1.gmu2.selinux1, configuration
# luma/fp6/alpha1/config-7.1.2-luma-fp-ims1.gmu2.selinux1), the build whose
# Image and DTB the Fairphone 6 boot image carries. Source0 is that build's
# /usr/lib/modules/<release> without vmlinuz and the boot images
# (scripts/packages/build-kernel-modules-luma-fp6.sh). The directory holds no
# vmlinuz, so rpm-ostree keeps the base image's kernel as the deployment's;
# the Fairphone 6 boots its own kernel from the boot partition and loads these.
%global kver 7.1.2-luma-fp-ims1.gmu2.selinux1
%global debug_package %{nil}
%global __strip /bin/true
%global _build_id_links none

Name:           kernel-modules-luma-fp6
Version:        7.1.2
Release:        1.luma.ims1.gmu2.selinux1.1%{?dist}
Summary:        Kernel modules for the Fairphone 6 kernel
License:        GPL-2.0-only
URL:            https://github.com/SimplyLuma/linux/tree/luma-fp6-alpha1-7.1.2-luma-fp-ims1.gmu2.selinux1
Source0:        fp6-kernel-modules.tar
ExclusiveArch:  aarch64
AutoReqProv:    no

%description
Loadable modules (and their modules.* indexes) for the Fairphone 6 kernel
%{kver}, which boots from the phone's boot partition. Installed from the
Fairphone 6 device repository.

%prep
%setup -q -c -T
tar -xf %{SOURCE0}
test -f usr/lib/modules/%{kver}/modules.dep

%build

%install
install -d %{buildroot}/usr/lib/modules
cp -a usr/lib/modules/%{kver} %{buildroot}/usr/lib/modules/

%check
m=%{buildroot}/usr/lib/modules/%{kver}
test ! -e $m/vmlinuz && test ! -e $m/initramfs.img && test ! -e $m/aboot.img
test -f $m/kernel/fs/erofs/erofs.ko.zst && test -f $m/kernel/fs/overlayfs/overlay.ko.zst
test "$(find $m/kernel -name '*.ko.zst' | wc -l)" -gt 300

%files
%dir /usr/lib/modules/%{kver}
/usr/lib/modules/%{kver}/kernel/
/usr/lib/modules/%{kver}/modules.*

%changelog
* Wed Nov 04 2026 Project Luma <maintainers@projectluma.org> - 7.1.2-1.luma.ims1.gmu2.selinux1.1
- Modules of the Fairphone 6 Alpha 1 kernel (Luma 1.0.0-nightly.20261015.25),
  for the shared ARM image.
