# SPDX-License-Identifier: Apache-2.0
# Candidate image package. Admission also requires the image provenance and
# native startup/upgrade/rollback gates; an RPM alone is not qualification.
# Avoid spending a full build interval recompressing multi-gigabyte images.
%global _binary_payload w9.zstdio
Name:           luma-android-images
Version:        20.0
Release:        1.luma.3.creator20261006.1%{?dist}
Summary:        Pinned offline Android application support images
License:        LicenseRef-Waydroid-Android-Image
URL:            https://waydro.id/
ExclusiveArch:  x86_64 aarch64
Source0:        android-system.zip
Source1:        android-vendor.zip
Source2:        images.env
Source3:        ANDROID-IMAGES.md
Source4:        package-images.py
BuildRequires:  python3
Requires:       luma-android-runtime >= 0.1.0-1.luma.73

%description
The exact architecture-matched Waydroid system and vendor image pair. These
files use Waydroid's preinstalled image interface. No image download occurs
when opening or installing an Android application. Native Luma applications
remain independent of this optional compatibility package.

%prep
mkdir -p images
python3 %{SOURCE4} %{SOURCE2} %{SOURCE0} %{SOURCE1} images %{_arch}

%build

%install
install -d %{buildroot}%{_datadir}/waydroid-extra/images
install -m0644 images/system.img images/vendor.img images/luma-images.json \
  %{buildroot}%{_datadir}/waydroid-extra/images/
install -D -m0644 %{SOURCE3} %{buildroot}%{_datadir}/doc/%{name}/ANDROID-IMAGES.md

%check
python3 %{SOURCE4} --verify \
  %{buildroot}%{_datadir}/waydroid-extra/images %{_arch}

%files
%{_datadir}/waydroid-extra/images/system.img
%{_datadir}/waydroid-extra/images/vendor.img
%{_datadir}/waydroid-extra/images/luma-images.json
%doc %{_datadir}/doc/%{name}/ANDROID-IMAGES.md
