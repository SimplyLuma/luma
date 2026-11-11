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
# Source2 adds the six files of packaging/rpm/luma-fp6-firmware-extra.manifest
# (provenance in devices/fairphone/fp6/device.toml): the AudioReach topology and AW88261 amplifier
# profile the sound card needs, the modem platform, AT&T and T-Mobile
# configurations luma-fp6-carrier-pdc loads, and the WCN6750 board file with
# the Fairphone 6 calibration.
Name:           luma-fp6-firmware
Version:        20261002
Release:        1.luma.4%{?dist}
Summary:        Firmware for the Fairphone 6
License:        LicenseRef-Fedora-Firmware AND BSD-3-Clause
URL:            https://gitlab.postmarketos.org/postmarketOS/pmaports/-/tree/master/device/testing/firmware-fairphone-fp6
Source0:        fp6-firmware.tar
Source1:        firmware.manifest
Source2:        fp6-firmware-extra.tar
Source3:        firmware-extra.manifest
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
tar -xf %{SOURCE2}

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
while IFS="$(printf '\t')" read -r kind mode digest relative target; do
  test "$kind" = F || continue
  echo "$digest  $fw/$relative" | sha256sum -c --quiet -
  files=$((files + 1))
done < %{SOURCE3}
test "$files" = 271
test "$(find "$fw" -type f | wc -l)" = 271

%files
/usr/lib/firmware/ath11k/
/usr/lib/firmware/qca/msbtfw12.mbn
/usr/lib/firmware/qca/msnv12.bin
/usr/lib/firmware/qcom/milos/
/usr/lib/firmware/gen80300_gmu.bin
/usr/lib/firmware/gen80300_sqe.fw
%dir /usr/lib/firmware/luma
/usr/lib/firmware/luma/fp6-carrier-pdc/

%changelog
* Tue Nov 10 2026 Project Luma <maintainers@projectluma.org> - 20261002-1.luma.4
- Add ath11k/WCN6750/hw1.0/board-2.bin with the Fairphone 6 calibration
  (variant Fairphone_Gen_6); with the generic board data the radio could not
  transmit on 5 GHz or 6 GHz

* Mon Nov 09 2026 Project Luma <maintainers@projectluma.org> - 20261002-1.luma.3
- Add the T-Mobile commercial modem configuration luma-fp6-carrier-pdc loads
  for tmo-qrel1695

* Mon Nov 09 2026 Project Luma <maintainers@projectluma.org> - 20261002-1.luma.2
- Add the AudioReach topology and AW88261 amplifier profile; without the
  topology the sound card does not instantiate
- Add the DSDS platform and AT&T modem configurations luma-fp6-carrier-pdc
  loads from /usr/lib/firmware/luma/fp6-carrier-pdc

* Wed Nov 04 2026 Project Luma <maintainers@projectluma.org> - 20261002-1.luma.1
- The Fairphone 6 firmware set (devices/fairphone/fp6/firmware.manifest) as a
  package in /usr/lib/firmware, replacing the store the Alpha image seeded
  into /var/lib/luma/device-firmware.
