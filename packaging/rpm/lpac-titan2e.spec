Name:           lpac-titan2e
Version:        2.3.0
Release:        7.luma1%{?dist}
Summary:        Local profile assistant for the Unihertz Titan 2 Elite eUICC
License:        AGPL-3.0-only AND LGPL-2.1-only AND MIT AND CC0-1.0
URL:            https://github.com/estkme-group/lpac
Source0:        lpac-3ff35594ec15062a3ed10c3da1c26eb0a13390b8.tar.gz
Patch0:         0001-profile-download-read-activation-code-from-stdin.patch
Patch1:         0002-profile-enable-read-identifier-from-stdin.patch
Patch2:         0003-profile-enable-accept-19-digit-iccid.patch
Patch3:         0004-profile-management-protected-selectors.patch
Source1:        luma_mm_fd.c
Source2:        luma-owned-apdu-wire.h
Patch4:         0005-luma-owned-fd-driver.patch
BuildRequires:  cmake >= 3.23
BuildRequires:  cjson-devel
BuildRequires:  gcc
BuildRequires:  libcurl-devel
BuildRequires:  make
BuildRequires:  pkgconfig
Provides:       lpac = %{version}-%{release}
Provides:       luma-lpac-protected-profile-selectors = 1
Provides:       luma-lpac-owned-mm-fd = 1
Conflicts:      lpac
Conflicts:      lpac-fp6
Obsoletes:      lpac-fp6 < 2.3.1
%global debug_package %{nil}

%description
Pinned lpac for Titan 2 Elite using Luma's inherited native-owner APDU
descriptor and HTTPS. The modem daemon owns the AT port and eUICC switch.
Activation codes and profile selectors use protected stdin, never arguments.

%prep
%autosetup -n lpac-3ff35594ec15062a3ed10c3da1c26eb0a13390b8 -p1

install -m 0644 %{SOURCE1} driver/apdu/luma_mm_fd.c
install -m 0644 %{SOURCE2} driver/apdu/luma-owned-apdu-wire.h

%build
%cmake \
  -DUSE_SYSTEM_DEPS=ON \
  -DLPAC_DYNAMIC_LIBEUICC=ON \
  -DLPAC_WITH_APDU_PCSC=OFF \
  -DLPAC_WITH_APDU_AT=OFF \
  -DLPAC_WITH_APDU_LUMA_MM_FD=ON \
  -DLPAC_WITH_APDU_GBINDER=OFF \
  -DLPAC_WITH_APDU_QMI=OFF \
  -DLPAC_WITH_APDU_QMI_QRTR=OFF \
  -DLPAC_WITH_APDU_UQMI=OFF \
  -DLPAC_WITH_APDU_MBIM=OFF \
  -DLPAC_WITH_HTTP_CURL=ON \
  -DLPAC_WITH_HTTP_WINHTTP=OFF
%cmake_build

%install
%cmake_install
rm -rf %{buildroot}%{_includedir} %{buildroot}%{_libdir}/pkgconfig
rm -f \
  %{buildroot}%{_libdir}/libeuicc.so \
  %{buildroot}%{_libdir}/libeuicc-driver-loader.so \
  %{buildroot}%{_libdir}/libeuicc-drivers.so

%check
%{_vpath_builddir}/src/lpac version | grep -F '"2.3.0"' >/dev/null
test -e %{buildroot}%{_libdir}/lpac/driver/driver_apdu_luma_mm_fd.so

%files
%defattr(-,root,root,0755)
%license LICENSES REUSE.toml
%doc README.md docs/ENVVARS.md docs/USAGE.md
%{_bindir}/lpac
%{_libdir}/libeuicc.so.*
%{_libdir}/libeuicc-driver-loader.so.*
%{_libdir}/libeuicc-drivers.so.*
%{_libdir}/liblpac-utils.so
%dir %{_libdir}/lpac
%dir %{_libdir}/lpac/driver
%{_libdir}/lpac/driver/driver_apdu_luma_mm_fd.so
%{_libdir}/lpac/driver/driver_apdu_stdio.so
%{_libdir}/lpac/driver/driver_http_curl.so
%{_libdir}/lpac/driver/driver_http_stdio.so

%changelog
* Thu Oct 22 2026 Project Luma - 2.3.0-7.luma1
- Directories are 0755 whatever the build tree's mode (6 shipped 2750 dirs).

* Thu Oct 22 2026 Project Luma - 2.3.0-6.luma1
- Use the protected native-owner descriptor instead of direct AT access.
- Replace the equivalent FP6 helper atomically when migrating a Titan image.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 2.3.0-1.luma1
- lpac for the Titan 2 Elite: AT backend with optional init/exit commands.
