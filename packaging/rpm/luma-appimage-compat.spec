# SPDX-License-Identifier: MPL-2.0

# Only a symlink and dependencies are shipped; the test program stays in %check.
%global debug_package %{nil}
# brp-ldconfig runs ldconfig on the build root, where libbz2.so.1 does not
# exist, and drops the link as dangling. %%check runs ldconfig against the real
# library instead and requires the link to survive it.
%global __brp_ldconfig %{nil}

Name:           luma-appimage-compat
Version:        0.1.0
Release:        1.luma.1%{?dist}
Summary:        The host libraries AppImages expect, under the names they expect
License:        MPL-2.0
URL:            https://projectluma.org/applications
Source0:        luma-appimage-compat.tar.gz

BuildRequires:  bzip2-libs
BuildRequires:  binutils
BuildRequires:  gcc
BuildRequires:  glibc
# AppImages built on Debian and Ubuntu need libbz2.so.1.0. It is the same
# library: upstream bzip2 builds libbz2.so.1.0.8 with soname libbz2.so.1.0 and
# Debian ships both names for one file; Fedora names it libbz2.so.1. The
# exported symbols are identical and neither build uses symbol versions.
Requires:       bzip2-libs%{?_isa}
Provides:       libbz2.so.1.0()(64bit)
# The classic AppImage type-2 runtime dlopens libfuse.so.2 and mounts with
# fusermount, for anyone who runs an .AppImage file directly.
Requires:       fuse-libs%{?_isa}
Requires:       fuse
# libcrypt.so.1 and libnsl.so.1: glibc-era libraries AppImages built on older
# distributions link, still shipped by Fedora with the same ABI.
Requires:       libxcrypt-compat%{?_isa}
Requires:       libnsl%{?_isa}
ExclusiveArch:  x86_64 aarch64

%description
Luma runs AppImages against the system's own libraries for everything the
AppImage specification leaves to the host (the excludelist: glibc, graphics
and audio drivers, fontconfig, and the like). This package fills the gaps
between that baseline and a Fedora-based system: the libbz2.so.1.0 name that
Debian and Ubuntu builds link against, FUSE 2 for the classic AppImage runtime,
and the libcrypt.so.1 and libnsl.so.1 compatibility libraries. Only names whose
Fedora library is ABI-identical are provided.

%prep
%autosetup -n luma-appimage-compat

%build
gcc %{optflags} -shared -fPIC -Wl,-soname,libbz2.so.1.0 -o tests/libbz2.so.1.0 tests/stub.c
gcc %{optflags} -o tests/debian-soname tests/debian-soname.c -Ltests -l:libbz2.so.1.0 %{build_ldflags}

%install
install -d %{buildroot}%{_libdir}
ln -s libbz2.so.1 %{buildroot}%{_libdir}/libbz2.so.1.0

%check
test "$(readlink %{buildroot}%{_libdir}/libbz2.so.1.0)" = libbz2.so.1
readelf -d tests/debian-soname | grep -F 'Shared library: [libbz2.so.1.0]'
readelf -d %{_libdir}/libbz2.so.1 | grep -F 'Library soname: [libbz2.so.1]'
# The loader follows the packaged name to Fedora's library, and it works.
mkdir -p shim && ln -sf %{_libdir}/$(readlink %{buildroot}%{_libdir}/libbz2.so.1.0) shim/libbz2.so.1.0
LD_LIBRARY_PATH=$PWD/shim ./tests/debian-soname | grep -Eq '^1\.0\.[0-9]+'
LD_LIBRARY_PATH=$PWD/shim ldd tests/debian-soname | grep -F "libbz2.so.1.0 => $PWD/shim/libbz2.so.1.0"
# ldconfig, run by every library transaction, keeps the name.
mkdir -p ldroot && cp -P %{_libdir}/libbz2.so.1* ldroot/ && cp -P %{buildroot}%{_libdir}/libbz2.so.1.0 ldroot/
ldconfig -n ldroot && test "$(readlink ldroot/libbz2.so.1.0)" = libbz2.so.1
LD_LIBRARY_PATH=$PWD/ldroot ./tests/debian-soname | grep -Eq '^1\.0\.[0-9]+'
# Without it the program fails the way the AppImage did.
if ./tests/debian-soname 2>/dev/null; then exit 1; fi

%files
%license LICENSES/MPL-2.0.txt
%{_libdir}/libbz2.so.1.0

%changelog
* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First release: libbz2.so.1.0, FUSE 2, libcrypt.so.1 and libnsl.so.1 for
  AppImages; Creality Print 7.2.1 no longer fails with libbz2.so.1.0 missing
