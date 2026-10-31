# SPDX-License-Identifier: Apache-2.0
Name:           luma-apk-metadata
Version:        0.1.0
Release:        1.luma.2.android20261030.1%{?dist}
Summary:        Read-only Android package identity decoder
License:        Apache-2.0 AND MIT AND Unicode-3.0
Source0:        luma-apk-metadata.tar.gz
Source1:        LICENSE.md
BuildRequires:  cargo >= 1.93
BuildRequires:  rust >= 1.93
BuildRequires:  gcc
BuildRequires:  acl

%description
A bounded resource decoder for Valet. It reads manifest labels and artwork
references without Android, network access, package execution or privileges.

%prep
%setup -q -n luma-apk-metadata
cp %{SOURCE1} LICENSE.md

%build
cargo build --frozen --offline --release -j2

%check
cargo test --frozen --offline -j2

%install
install -Dm0755 target/release/luma-apk-metadata %{buildroot}%{_libexecdir}/luma-apk-metadata
# Shared build storage may inherit restrictive default ACLs and setgid modes.
# RPM directory permissions must describe the installed system, not the builder.
find %{buildroot} -type d -exec setfacl -b -k {} +
find %{buildroot} -type d -exec chmod 0755 {} +

%files
%license LICENSE.md THIRD_PARTY_LICENSES.txt
%{_libexecdir}/luma-apk-metadata
