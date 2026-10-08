# SPDX-License-Identifier: Apache-2.0

%global use_source_date_epoch_as_buildtime 1

Name:           luma-mod-recovery-drill
Version:        0.1.0
Release:        1.luma.1%{?dist}
Summary:        Test-only fault payload for Project Luma rollback verification
License:        Apache-2.0
URL:            https://github.com/SimplyLuma/luma
BuildArch:      noarch

Source0:        90-luma-recovery-drill.conf
Source1:        LICENSE.md

%description
Intentionally prevents a candidate Luma Mods deployment from being promoted.
This package exists only for the controlled automatic-rollback acceptance test
and must never be included in a product image or package manifest.

%prep
cp %{SOURCE1} LICENSE.md

%build

%install
install -D -m 0644 %{SOURCE0} \
  %{buildroot}%{_unitdir}/luma-mod-boot-promote.service.d/90-luma-recovery-drill.conf

%files
%license LICENSE.md
%{_unitdir}/luma-mod-boot-promote.service.d/90-luma-recovery-drill.conf

%changelog
* Sat Aug 22 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Add the bounded test-only promotion failure for physical rollback proof
