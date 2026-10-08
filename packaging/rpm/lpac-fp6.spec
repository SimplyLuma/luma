Name:           lpac-fp6
Version:        2.3.0
Release:        3.luma1%{?dist}
Summary:        eUICC local profile agent with the FP6 QRTR transport
License:        AGPL-3.0-only AND LGPL-2.1-only AND MIT AND CC0-1.0
URL:            https://github.com/estkme-group/lpac
Source0:        lpac-3ff35594ec15062a3ed10c3da1c26eb0a13390b8.tar.gz
Patch0:         0001-profile-download-read-activation-code-from-stdin.patch
Patch1:         0002-profile-enable-read-identifier-from-stdin.patch
Patch2:         0003-profile-enable-accept-19-digit-iccid.patch

BuildRequires:  cmake >= 3.23
BuildRequires:  cjson-devel
BuildRequires:  gcc
BuildRequires:  libcurl-devel
BuildRequires:  libqmi-devel >= 1.36.0
BuildRequires:  libqrtr-glib-devel
BuildRequires:  make
BuildRequires:  pkgconfig

Requires:       libqmi%{?_isa} >= 1.36.0
Requires:       libqrtr-glib%{?_isa}
Provides:       lpac = %{version}-%{release}
Conflicts:      lpac

%global debug_package %{nil}

%description
An exact-source lpac build for Fairphone 6. It contains only the HTTPS and
QMI-over-QRTR backends required to manage the embedded eUICC. The Luma patch
adds protected stdin paths so bearer secrets and profile identifiers need not
appear in the process argument vector.

%prep
%autosetup -n lpac-3ff35594ec15062a3ed10c3da1c26eb0a13390b8 -p1

%build
%cmake \
  -DUSE_SYSTEM_DEPS=ON \
  -DLPAC_DYNAMIC_LIBEUICC=ON \
  -DLPAC_WITH_APDU_PCSC=OFF \
  -DLPAC_WITH_APDU_AT=OFF \
  -DLPAC_WITH_APDU_GBINDER=OFF \
  -DLPAC_WITH_APDU_QMI=OFF \
  -DLPAC_WITH_APDU_QMI_QRTR=ON \
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

%files
%license LICENSES REUSE.toml
%doc README.md docs/ENVVARS.md docs/USAGE.md docs/backends/qmi.md
%{_bindir}/lpac
%{_libdir}/libeuicc.so.*
%{_libdir}/libeuicc-driver-loader.so.*
%{_libdir}/libeuicc-drivers.so.*
%{_libdir}/liblpac-utils.so
%dir %{_libdir}/lpac
%dir %{_libdir}/lpac/driver
%{_libdir}/lpac/driver/driver_apdu_qmi_qrtr.so
%{_libdir}/lpac/driver/driver_apdu_stdio.so
%{_libdir}/lpac/driver/driver_http_curl.so
%{_libdir}/lpac/driver/driver_http_stdio.so

%changelog
* Wed Aug 26 2026 Project Luma <codex@project-luma.local> - 2.3.0-3.luma1
- Accept protected 19-digit ICCID selectors used by operational eSIMs

* Wed Aug 26 2026 Project Luma <codex@project-luma.local> - 2.3.0-2.luma1
- Add a protected standard-input selector for profile enablement

* Wed Aug 26 2026 Project Luma <codex@project-luma.local> - 2.3.0-1.luma1
- Pin lpac and enable the Fairphone-authored QMI-over-QRTR backend
- Add a bounded stdin path for activation-code secrets
