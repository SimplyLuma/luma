# SPDX-License-Identifier: Apache-2.0
#
# Userspace services every Qualcomm phone needs, built from upstream source as
# one package and shipped in the device repositories of Qualcomm phones
# (devices/<vendor>/<codename>/device.toml [device_repo] install), never in the
# generic ARM image. Built by scripts/packages/build-luma-qcom-userspace.sh,
# which checks each source against the SHA-256 below.

%global use_source_date_epoch_as_buildtime 1

%global qbootctl_version 0.2.2
%global qbootctl_sha256 e66b361f86fac413f6a96974b4045a1fbc385f9eb6cee7fa8a26e0ca77c74a51
%global tqftpserv_version 1.2
%global tqftpserv_sha256 a3850c34e48f23b7bc360d70f1e91700de2f8c60af003eda93e14182f8cf6af1

Name:           luma-qcom-userspace
Version:        1
Release:        1.luma.1%{?dist}
Summary:        Qualcomm phone userspace services: qbootctl and tqftpserv
License:        GPL-3.0-or-later AND BSD-3-Clause AND GPL-2.0-or-later AND Apache-2.0
URL:            https://github.com/linux-msm
Source0:        https://github.com/linux-msm/qbootctl/archive/%{qbootctl_version}/qbootctl-%{qbootctl_version}.tar.gz
Source1:        https://github.com/linux-msm/tqftpserv/archive/v%{tqftpserv_version}/tqftpserv-%{tqftpserv_version}.tar.gz
# qbootctl: fail closed on unreadable slot attributes (0001); swap the type
# GUIDs toward the target slot and arm seven boot retries (0002).
Patch0:         0001-native-query-errors.patch
Patch1:         0002-native-slot-guid-retries.patch
Source2:        active-controls.c
Source3:        query-controls.c
Source4:        check-source-controls.py
Source5:        check-slot-transition.py

BuildRequires:  gcc
BuildRequires:  kernel-headers
BuildRequires:  meson
BuildRequires:  ninja-build
BuildRequires:  patch
BuildRequires:  pkgconfig(libzstd)
BuildRequires:  pkgconfig(qrtr)
BuildRequires:  pkgconfig(systemd)
BuildRequires:  python3
BuildRequires:  systemd-rpm-macros

%description
Qualcomm phone userspace services from linux-msm, built and signed by Luma.

%package -n qbootctl
Version:        %{qbootctl_version}
Release:        4.luma.1%{?dist}
Summary:        Qualcomm A/B boot slot control
License:        GPL-3.0-or-later AND BSD-3-Clause AND GPL-2.0-or-later AND Apache-2.0
URL:            https://github.com/linux-msm/qbootctl

%description -n qbootctl
Reads and sets the A/B boot slot attributes in the GPT of Qualcomm phones.
Failed attribute queries exit non-zero instead of reporting slot 0.

%package -n tqftpserv
Version:        %{tqftpserv_version}
Release:        2.luma.1%{?dist}
Summary:        TFTP server over AF_QIPCRTR
License:        BSD-3-Clause
URL:            https://github.com/linux-msm/tqftpserv

%description -n tqftpserv
Serves files from the Linux file system to the modem and other remote
processors of Qualcomm SoCs over QRTR.

%prep
%setup -q -c -T -a 0 -a 1
patch -d qbootctl-%{qbootctl_version} -p1 --fuzz=0 <%{PATCH0}
patch -d qbootctl-%{qbootctl_version} -p1 --fuzz=0 <%{PATCH1}

%build
cd qbootctl-%{qbootctl_version}
%meson
%meson_build
cd ../tqftpserv-%{tqftpserv_version}
%meson
%meson_build

%install
cd qbootctl-%{qbootctl_version}
%meson_install
cd ../tqftpserv-%{tqftpserv_version}
%meson_install

%check
cd qbootctl-%{qbootctl_version}
python3 %{SOURCE5} .
python3 %{SOURCE4} . %{SOURCE2} %{SOURCE3}
cc -std=c11 -Wall -Wextra -Werror %{SOURCE2} -o active-controls
./active-controls
cc -std=c11 -Wall -Wextra -Werror %{SOURCE3} -o query-controls
./query-controls
grep -Fqx 'ExecStart=/usr/bin/tqftpserv' %{buildroot}%{_unitdir}/tqftpserv.service

%post -n tqftpserv
%systemd_post tqftpserv.service

%preun -n tqftpserv
%systemd_preun tqftpserv.service

%postun -n tqftpserv
%systemd_postun_with_restart tqftpserv.service

%files -n qbootctl
%license qbootctl-%{qbootctl_version}/LICENSE
%doc qbootctl-%{qbootctl_version}/README.md
%{_bindir}/qbootctl

%files -n tqftpserv
%license tqftpserv-%{tqftpserv_version}/LICENSE
%doc tqftpserv-%{tqftpserv_version}/README.md
%{_bindir}/tqftpserv
%{_unitdir}/tqftpserv.service

%changelog
* Tue Nov 17 2026 Project Luma <maintainers@projectluma.org> - 1-1.luma.1
- One package for qbootctl 0.2.2 (the Fairphone 6 patches 0001-0002) and
  tqftpserv 1.2, from pinned upstream archives.
