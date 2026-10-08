# SPDX-License-Identifier: MIT
# Packaging follows the Fedora Rust guidelines for a non-crates.io application
# with vendored dependencies: %%cargo_prep -v, %%cargo_license, and a
# cargo-vendor.txt manifest that generates bundled(crate(...)) Provides.

%global forgeurl https://github.com/intel/crashlog

Name:           intel-crashlog
Version:        1.2.0
Release:        1.luma.1%{?dist}
Summary:        Extract, decode and triage Intel Crash Log records

SourceLicense:  MIT
# iclg is MIT. The statically linked crates, from %%cargo_license_summary for
# the Linux targets: (MIT OR Apache-2.0), Unicode-3.0, MIT, (Unlicense OR MIT).
# The summary also lists MPL-2.0 for ucs2, which only the UEFI target links.
License:        MIT AND (MIT OR Apache-2.0) AND Unicode-3.0 AND (Unlicense OR MIT)
URL:            %{forgeurl}
Source0:        %{forgeurl}/archive/v%{version}/crashlog-%{version}.tar.gz
# cargo vendor-filterer --platform x86_64-unknown-linux-gnu
#   --platform aarch64-unknown-linux-gnu --manifest-path app/Cargo.toml
#   --sync lib/Cargo.toml --versioned-dirs vendor
# (scripts/packages/build-intel-crashlog.sh)
Source1:        crashlog-%{version}-vendor.tar.xz

ExclusiveArch:  %{rust_arches}

BuildRequires:  cargo-rpm-macros >= 26
BuildRequires:  gcc

%description
iclg is Intel's command-line tool for Crash Log, the record that Intel
processors and platform controllers leave in firmware when the machine resets
itself after a hang (for example a core timeout or a platform watchdog). It
extracts records from the ACPI Boot Error Record Table (BERT) or Intel PMT,
decodes them into JSON with the register definitions built in for supported
products, and triages them into short cause tags such as
CORE_TIMEOUT.MULTIPLE_STUCK_TRANSACTIONS.

%prep
%autosetup -n crashlog-%{version} -a1
# One vendored source directory serves both the application and the library
# tests; .cargo/config.toml is found from either subdirectory.
%cargo_prep -v vendor

%build
cd app
%cargo_build
%{cargo_license_summary}
%{cargo_license} > ../LICENSE.dependencies
%{cargo_vendor_manifest}
# The in-tree library crate is not vendored; keep only registry crates so the
# bundled(crate(...)) generator can read every line.
grep -v ' (/' cargo-vendor.txt > ../cargo-vendor.txt
rm cargo-vendor.txt

%install
cd app
%cargo_install

%check
# Upstream's own decoder tests, against its sample Crash Log records.
cd lib
%cargo_test
cd ..
triage=$(%{buildroot}%{_bindir}/iclg triage lib/tests/samples/three_strike_timeout.crashlog)
printf '%s\n' "$triage"
printf '%s\n' "$triage" | grep -q '^CORE_TIMEOUT\.'
%{buildroot}%{_bindir}/iclg decode lib/tests/samples/dummy.bert >/dev/null

%files
%license LICENSE LICENSE.dependencies cargo-vendor.txt
%doc README.md
%{_bindir}/iclg

%changelog
* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 1.2.0-1.luma.1
- Package Intel's Crash Log tool 1.2.0 for Luma Vitals crash evidence
