# SPDX-License-Identifier: Apache-2.0
# Fairphone 6 firmware for the shared ARM image: the 267 files of
# devices/fairphone/fp6/firmware.manifest, as postmarketOS packages them for the
# Fairphone 6 (edge image 20261002-1221, fairphone-fp6/phosh): the remote
# processors and GPU zap shader under qcom/milos/fairphone, the WCN6750 Wi-Fi
# and Bluetooth files, and the Adreno gen8 GMU and SQE microcode. Source0 is
# that set as a tar of usr/lib/firmware (scripts/packages/build-luma-fp6-firmware.sh
# checks it against the manifest). Installed in /usr/lib/firmware, not in /var.
# regulatory.db comes from wireless-regdb in the base image; the GMU and SQE
# files move from postmarketos/ to the top level, where the msm driver looks.
Name:           luma-fp6-firmware
Version:        20261002
Release:        1.luma.1%{?dist}
Summary:        Firmware for the Fairphone 6
License:        LicenseRef-Fedora-Firmware
URL:            https://gitlab.postmarketos.org/postmarketOS/pmaports/-/tree/master/device/testing/firmware-fairphone-fp6
Source0:        fp6-firmware.tar
Source1:        firmware.manifest
BuildArch:      noarch
Requires:       wireless-regdb

%description
Firmware the Fairphone 6 kernel loads: modem, audio, compute and sensor DSPs,
GPU microcode and zap shader, Wi-Fi and Bluetooth. Installed from the
Fairphone 6 device repository.

%prep
%setup -q -c -T
tar -xf %{SOURCE0}
test -d usr/lib/firmware/qcom/milos/fairphone

%build

%install
install -d %{buildroot}/usr/lib/firmware
cp -a usr/lib/firmware/. %{buildroot}/usr/lib/firmware/
rm -f %{buildroot}/usr/lib/firmware/regulatory.db %{buildroot}/usr/lib/firmware/regulatory.db.p7s
mv %{buildroot}/usr/lib/firmware/postmarketos/gen80300_gmu.bin %{buildroot}/usr/lib/firmware/postmarketos/gen80300_sqe.fw \
  %{buildroot}/usr/lib/firmware/
rmdir %{buildroot}/usr/lib/firmware/postmarketos

%check
# Every file in the manifest, at its installed path, with its digest.
fw=%{buildroot}/usr/lib/firmware
files=0
while IFS="$(printf '\t')" read -r kind mode digest relative target; do
  case "$kind" in
    F) ;;
    L) test "$(readlink "$fw/$relative")" = "$target"; continue ;;
    *) continue ;;
  esac
  case "$relative" in
    regulatory.db|regulatory.db.p7s) continue ;;
    postmarketos/*) relative=${relative#postmarketos/} ;;
  esac
  echo "$digest  $fw/$relative" | sha256sum -c --quiet -
  files=$((files + 1))
done < %{SOURCE1}
test "$files" = 265
test "$(find "$fw" -type f | wc -l)" = 265

%files
/usr/lib/firmware/ath11k/
/usr/lib/firmware/qca/msbtfw12.mbn
/usr/lib/firmware/qca/msnv12.bin
/usr/lib/firmware/qcom/milos/
/usr/lib/firmware/gen80300_gmu.bin
/usr/lib/firmware/gen80300_sqe.fw

%changelog
* Wed Nov 04 2026 Project Luma <maintainers@projectluma.org> - 20261002-1.luma.1
- The Fairphone 6 firmware set (devices/fairphone/fp6/firmware.manifest) as a
  package in /usr/lib/firmware, replacing the store the Alpha image seeded
  into /var/lib/luma/device-firmware.
