# SPDX-License-Identifier: GPL-2.0-only
# Device RPM around upstream Linux's supported kernel/module install targets.
# The source bundle includes the pinned upstream inputs and all patch notices.
%global debug_package %{nil}
%global _missing_build_ids_terminate_build 0
%global __provides_exclude_from ^/usr/lib/modules/.*$
%global __requires_exclude_from ^/usr/lib/modules/.*$
# modules_install strips before signing/compressing; RPM must not strip again.
%global __spec_install_post %{nil}
%global kver 7.3.0-1.luma.surface12.fc44.aarch64
Name: kernel-surface-pro12
Version: 7.3.0
Release: 2.luma.surface12%{?dist}
Summary: Linux for Surface Pro 12-inch first edition Snapdragon
License: GPL-2.0-only AND BSD-3-Clause AND Apache-2.0
URL: https://www.kernel.org
ExclusiveArch: aarch64
Source0: luma-surface-sources.tar.gz
Source1: linux-7.3.tar.xz
Source2: surface-overlay-c570db8.tar.gz
Source3: fedora-base.config
BuildRequires: bash bc binutils bison dwarves elfutils-libelf-devel flex gcc
BuildRequires: make openssl-devel perl python3 rsync rpm-build kmod file
Requires: kmod
Provides: kernel = %{version}-%{release}
Provides: kernel-core = %{version}-%{release}
Provides: kernel-modules = %{version}-%{release}
Provides: kernel-modules-core = %{version}-%{release}
Provides: kernel-modules-extra = %{version}-%{release}
Provides: kernel-modules-internal = %{version}-%{release}
Provides: kernel-uname-r = %{kver}
Provides: kernel-core-uname-r = %{kver}
Provides: kernel-modules-uname-r = %{kver}
Provides: kernel-modules-core-uname-r = %{kver}
Provides: kernel-modules-extra-uname-r = %{kver}
Provides: kernel-modules-internal-uname-r = %{kver}
# Snap accepts native squashfs as a kernel capability, including built-ins.
# The actual installed config and built-in inventory are verified below.
Provides: kmod(squashfs.ko)

%description
Upstream Linux with a scoped Surface Pro 12-inch first edition patch set,
modules, matching device tree, configuration and source-input manifest.
This package is selected only by that experimental device profile. It does
not obsolete Fedora kernels or establish physical hardware qualification.

%prep
%setup -q -n luma-surface-sources
%if ! 0%{?_luma_prepared_source:1}
python3 scripts/kernel/prepare-source.py --offline \
    --lock config/kernel/surface-pro12-7.3.json \
    --cache %{_sourcedir} --output prepared
%endif

%build
export ARCH=arm64 CROSS_COMPILE="%{?_luma_cross_compile}"
export KBUILD_BUILD_USER=luma KBUILD_BUILD_HOST=builder KBUILD_BUILD_VERSION=1
export KBUILD_BUILD_TIMESTAMP='2026-10-22 00:00:00 UTC'
source_dir="%{?_luma_prepared_source}%{!?_luma_prepared_source:$PWD/prepared/linux/linux-7.3}"
output_dir="%{?_luma_prepared_output}%{!?_luma_prepared_output:$PWD/kernel-build}"
# Optional prepared paths are a build cache, not alternate source inputs.
# The package driver verifies their source/config receipt before setting them.
%if 0%{?_luma_prepared_source:1}
python3 scripts/kernel/build-cache.py verify --repository "$PWD" --build "$output_dir" --base-config %{SOURCE3}
%else
bash scripts/kernel/configure-surface-pro12.sh "$source_dir" "$output_dir" %{SOURCE3}
make -C "$source_dir" O="$output_dir" %{?_smp_mflags} Image modules qcom/x1p42100-microsoft-sp12in.dtb
python3 scripts/kernel/build-cache.py record --repository "$PWD" --build "$output_dir" --base-config %{SOURCE3}
%endif

%install
export ARCH=arm64 CROSS_COMPILE="%{?_luma_cross_compile}"
source_dir="%{?_luma_prepared_source}%{!?_luma_prepared_source:$PWD/prepared/linux/linux-7.3}"
output_dir="%{?_luma_prepared_output}%{!?_luma_prepared_output:$PWD/kernel-build}"
make -C "$source_dir" O="$output_dir" %{?_smp_mflags} \
    INSTALL_MOD_PATH=%{buildroot}/usr INSTALL_MOD_STRIP=1 DEPMOD=true modules_install
install -m 0644 "$output_dir/arch/arm64/boot/Image" %{buildroot}/usr/lib/modules/%{kver}/vmlinuz
install -m 0644 "$output_dir/.config" %{buildroot}/usr/lib/modules/%{kver}/config
install -m 0644 "$output_dir/System.map" %{buildroot}/usr/lib/modules/%{kver}/System.map
install -D -m 0644 "$output_dir/arch/arm64/boot/dts/qcom/x1p42100-microsoft-sp12in.dtb" \
    %{buildroot}/usr/lib/modules/%{kver}/dtb/qcom/x1p42100-microsoft-sp12in.dtb
# A single devicetree takes precedence over the generic DTB directory in
# libostree, generating an explicit devicetree BLS entry on every deployment.
install -m 0644 "$output_dir/arch/arm64/boot/dts/qcom/x1p42100-microsoft-sp12in.dtb" \
    %{buildroot}/usr/lib/modules/%{kver}/devicetree
rm -f %{buildroot}/usr/lib/modules/%{kver}/build %{buildroot}/usr/lib/modules/%{kver}/source
install -D -m 0644 config/kernel/surface-pro12-7.3.json \
    %{buildroot}/usr/share/luma/kernel/source-lock.json

# Carry upstream license texts in the binary as well as corresponding source.
install -d -m 0755 %{buildroot}/usr/share/licenses/%{name}
install -m 0644 "$source_dir/COPYING" "$source_dir/LICENSES/preferred/GPL-2.0" \
    "$source_dir/LICENSES/exceptions/Linux-syscall-note" \
    LICENSES/Apache-2.0.txt LICENSES/BSD-3-Clause.txt \
    %{buildroot}/usr/share/licenses/%{name}/

%check
grep -Fxq 'CONFIG_SQUASHFS=y' %{buildroot}/usr/lib/modules/%{kver}/config
grep -Fxq 'kernel/fs/squashfs/squashfs.ko' %{buildroot}/usr/lib/modules/%{kver}/modules.builtin
file %{buildroot}/usr/lib/modules/%{kver}/vmlinuz | grep -E 'ARM64|Aarch64|aarch64'
cmp %{buildroot}/usr/lib/modules/%{kver}/devicetree \
    %{buildroot}/usr/lib/modules/%{kver}/dtb/qcom/x1p42100-microsoft-sp12in.dtb
# Inspect the actual installed module signatures after stripping/compression.
python3 - '%{buildroot}/usr/lib/modules/%{kver}' <<'PY'
import pathlib,subprocess,sys
root=pathlib.Path(sys.argv[1])
modules=sorted(p for p in root.rglob('*') if p.is_file() and '.ko' in p.name)
if not modules: raise SystemExit('No kernel modules packaged')
for p in modules:
    signer=subprocess.check_output(['modinfo','-F','signer',str(p)],text=True).strip()
    if not signer: raise SystemExit('Unsigned installed module: '+str(p))
print('Verified module signatures:',len(modules))
PY

%post
/usr/sbin/depmod %{kver} || exit $?

%files
%defattr(0644,root,root,0755)
%license /usr/share/licenses/%{name}
/usr/lib/modules/%{kver}
/usr/share/luma/kernel/source-lock.json
