# SPDX-License-Identifier: Apache-2.0
# Candidate image package. Admission also requires the image provenance and
# native startup/upgrade/rollback gates; an RPM alone is not qualification.
# Avoid spending a full build interval recompressing multi-gigabyte images.
%global _binary_payload w9.zstdio
Name:           luma-android-images
Version:        20.0
Release:        1.luma.9.creator20261008.1%{?dist}
Summary:        Pinned offline Android application support images
License:        LicenseRef-Waydroid-Android-Image
URL:            https://waydro.id/
ExclusiveArch:  x86_64
Source0:        android-system.zip
Source1:        android-vendor.zip
Source2:        images.env
Source3:        ANDROID-IMAGES.md
Source4:        package-images.py
Source5:        source-materials.tar.gz
Source6:        system-NOTICE.xml.gz
Source7:        vendor-NOTICE.xml.gz
Source8:        Figtree-OFL.txt
Source9:        TERMINAL-PRODUCER.json
Source10:       IMAGE-MEMBERS.json
Source11:       verify-packaged-image-provenance.py
Source12:       test_image_pair_admission.py
BuildRequires:  python3
Requires:       luma-android-runtime >= 0.1.0-1.luma.79

%description
The exact architecture-matched Waydroid system and vendor image pair. These
files use Waydroid's preinstalled image interface. No image download occurs
when opening or installing an Android application. Native Luma applications
remain independent of this optional compatibility package.

%prep
mkdir -p images
python3 %{SOURCE4} %{SOURCE2} %{SOURCE0} %{SOURCE1} images %{_arch}

%build
# Current native qualification packet. Do not describe this archive of Luma
# deltas/upstream exact refs as a qualified complete public GPL source offer.
python3 %{SOURCE11} images %{SOURCE9} %{SOURCE10} %{SOURCE5} %{SOURCE6} %{SOURCE7} %{SOURCE8} %{_arch}

%install
install -d %{buildroot}%{_datadir}/waydroid-extra/images
install -m0644 images/system.img images/vendor.img images/luma-images.json \
  %{buildroot}%{_datadir}/waydroid-extra/images/
install -D -m0644 %{SOURCE3} %{buildroot}%{_datadir}/doc/%{name}/ANDROID-IMAGES.md

install -D -m0644 %{SOURCE5} %{buildroot}%{_datadir}/doc/%{name}/source-materials.tar.gz
install -D -m0644 %{SOURCE10} %{buildroot}%{_datadir}/doc/%{name}/IMAGE-MEMBERS.json
install -D -m0644 %{SOURCE9} %{buildroot}%{_datadir}/doc/%{name}/TERMINAL-PRODUCER.json
install -d %{buildroot}%{_datadir}/licenses/%{name}
install -m0644 %{SOURCE6} %{SOURCE7} %{SOURCE8} %{buildroot}%{_datadir}/licenses/%{name}/

%check
python3 -B -I %{SOURCE12}
python3 %{SOURCE11} %{buildroot}%{_datadir}/waydroid-extra/images %{SOURCE9} %{SOURCE10} %{SOURCE5} %{SOURCE6} %{SOURCE7} %{SOURCE8} %{_arch}
python3 %{SOURCE4} --verify \
  %{buildroot}%{_datadir}/waydroid-extra/images %{_arch}

%files
%{_datadir}/waydroid-extra/images/system.img
%{_datadir}/waydroid-extra/images/vendor.img
%{_datadir}/waydroid-extra/images/luma-images.json
%doc %{_datadir}/doc/%{name}/ANDROID-IMAGES.md

%doc %{_datadir}/doc/%{name}/source-materials.tar.gz
%doc %{_datadir}/doc/%{name}/TERMINAL-PRODUCER.json
%license %{_datadir}/licenses/%{name}/system-NOTICE.xml.gz
%license %{_datadir}/licenses/%{name}/vendor-NOTICE.xml.gz
%license %{_datadir}/licenses/%{name}/Figtree-OFL.txt
%doc %{_datadir}/doc/%{name}/IMAGE-MEMBERS.json
