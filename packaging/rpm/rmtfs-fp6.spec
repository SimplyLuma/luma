# SPDX-License-Identifier: BSD-3-Clause

%global use_source_date_epoch_as_buildtime 1

Name:           rmtfs
Version:        1.1.1
Release:        3.luma.1%{?dist}
Summary:        Qualcomm Remote Filesystem Service Implementation

License:        BSD-3-Clause
URL:            https://github.com/linux-msm/rmtfs/
Source:         %{url}/archive/v%{version}/%{name}-%{version}.tar.gz
Patch0:         0001-storage-add-fp6-modem-study.patch
Patch1:         0003-fp6-persist-modemst-only.patch

BuildRequires:  gcc
BuildRequires:  make
BuildRequires:  qrtr-devel
BuildRequires:  systemd-devel
BuildRequires:  systemd-rpm-macros

Requires:       qrtr

%description
Qualcomm Remote Filesystem Service Implementation.

This Project Luma build carries upstream commit 27b3a6f, maps the Fairphone 6
modem's /boot/modem_study request, and adds a bounded recovery mode capable of
persisting only modemst1/modemst2. The installed service keeps every partition
on the read-only shadow path.

%prep
%autosetup -p1

%build
export CFLAGS="%{build_cflags} -ffile-prefix-map=%{_builddir}=. -fdebug-prefix-map=%{_builddir}=. -frandom-seed=rmtfs-%{version}-%{release}"
%make_build prefix="%{_prefix}"

%install
%make_install prefix="%{_prefix}"

%check
grep -Fq '"/boot/modem_study", "modem_study", "study"' storage.c
strings rmtfs | grep -Fqx '/boot/modem_study'
strings rmtfs | grep -Fqx -- '-W requires -r -P and forbids -o'
grep -Fqx 'ExecStart=/usr/bin/rmtfs -r -P -s' rmtfs.service
! grep -Eq 'ExecStart=.*[[:space:]]-W([[:space:]]|$)' rmtfs.service

%post
%systemd_post rmtfs.service rmtfs-dir.service

%preun
%systemd_preun rmtfs.service rmtfs-dir.service

%postun
%systemd_postun rmtfs.service rmtfs-dir.service

%files
%license LICENSE
%{_bindir}/%{name}
%{_unitdir}/rmtfs.service
%{_unitdir}/rmtfs-dir.service

%changelog
* Wed Aug 26 2026 Project Luma <project-luma@localhost> - 1.1.1-3.luma.1
- Add a bounded mode that persists only guarded modemst1/modemst2 state
- Keep the normal service read-only; never enable persistence at boot
- Keep fsg, fsc, study, tuning, and every other partition read-only

* Wed Aug 12 2026 Project Luma <project-luma@localhost> - 1.1.1-2.luma.1
- Backport upstream FP6 modem_study partition mapping
