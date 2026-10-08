#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Install the complete matched module tree for the unique matched-CMA kernel.
# The accepted 7.1.2 rollback tree is never modified.  The archive is expanded
# on the same filesystem and renamed into place only after every identity gate
# passes, so loss of the SSH connection cannot expose a partial module tree.

set -Eeuo pipefail
umask 077

archive=/tmp/luma-fp6-fingerprint-matched-cma-v14-modules.tar.zst
release=7.1.2-luma-fp-cma1
module_root=/usr/lib/modules
target=$module_root/$release
staging=$module_root/.luma-fp-cma1-stage.$$
expected_archive_sha=afa92aa49698e6c99cef241880ce148ea56cbc69f4f127297a00107d4350a153
expected_archive_size=170002536
expected_module_count=418
expected_qsee_sha=047100c255b64b14ec1565cfc089f9bd0c5eb3a28e67b259f206bc930af39f5b
expected_focal_sha=be860585db2c68ca80b3ee9e5e4929413c0d7207f9524ba489f974c46e26d051

installed=false

fail() {
  printf 'event=matched_cma_modules_failed reason=%s\n' "$1" >&2
  exit 1
}

cleanup() {
  local status=$?
  set +e
  if [[ $installed != true && -d $staging && ! -L $staging ]]; then
    find "$staging" -depth -delete
  fi
  rm -f -- "$archive"
  printf 'event=matched_cma_modules_cleanup installed=%s archive_present=%s\n' \
    "$installed" "$([[ -e $archive ]] && printf true || printf false)"
  return "$status"
}
trap cleanup EXIT INT TERM HUP

[[ $(id -u) -eq 0 ]] || fail not_root
[[ $(tr -d '\0' </sys/firmware/devicetree/base/model) == 'The Fairphone (Gen. 6)' ]] || fail model
grep -qw 'androidboot.slot_suffix=_b' /proc/cmdline || fail slot
[[ $(uname -r) == 7.1.2 ]] || fail running_kernel
[[ -d $module_root/7.1.2 && ! -L $module_root/7.1.2 ]] || fail rollback_tree
[[ ! -e $target && ! -e $staging ]] || fail target_preexists
[[ -f $archive && ! -L $archive ]] || fail archive_identity
[[ $(stat -c %s "$archive") == "$expected_archive_size" ]] || fail archive_size
[[ $(sha256sum "$archive" | cut -d' ' -f1) == "$expected_archive_sha" ]] || fail archive_hash
[[ $(df --output=avail -B1 "$module_root" | tail -1 | tr -d ' ') -gt 1073741824 ]] || fail free_space

mkdir -m 0700 "$staging"
tar --zstd --no-same-owner --no-same-permissions -xf "$archive" -C "$staging"
source_tree=$staging/lib/modules/$release
[[ -d $source_tree && ! -L $source_tree ]] || fail archive_layout
[[ $(find "$staging" -type l | wc -l) -eq 0 ]] || fail archive_symlink
[[ $(find "$source_tree" -type f \( -name '*.ko' -o -name '*.ko.zst' -o -name '*.ko.xz' -o -name '*.ko.gz' \) | wc -l) -eq "$expected_module_count" ]] || fail module_count

qsee=$source_tree/kernel/drivers/tee/qseecom/qseecomtee.ko.zst
focal=$source_tree/kernel/drivers/input/finger/focal_finger/focaltech_fp.ko.zst
[[ -f $qsee && -f $focal ]] || fail fingerprint_modules_missing
[[ $(sha256sum "$qsee" | cut -d' ' -f1) == "$expected_qsee_sha" ]] || fail qsee_module_hash
[[ $(sha256sum "$focal" | cut -d' ' -f1) == "$expected_focal_sha" ]] || fail focal_module_hash
[[ $(modinfo -F vermagic "$qsee" | cut -d' ' -f1) == "$release" ]] || fail qsee_vermagic
[[ $(modinfo -F vermagic "$focal" | cut -d' ' -f1) == "$release" ]] || fail focal_vermagic
for required in \
  kernel/drivers/media/platform/qcom/camss/qcom-camss.ko.zst \
  kernel/drivers/media/i2c/imx896.ko.zst \
  kernel/drivers/media/i2c/s5kkd1sp.ko.zst \
  kernel/drivers/media/i2c/ov13b10.ko.zst \
  kernel/drivers/nfc/s3fwrn5/s3fwrn5_i2c.ko.zst \
  kernel/drivers/iio/imu/inv_icm42600/inv-icm42600-spi.ko.zst \
  kernel/drivers/iio/magnetometer/qmc6308.ko.zst \
  kernel/sound/soc/codecs/snd-soc-wcd9378-sdw.ko.zst; do
  [[ -f $source_tree/$required ]] || fail "hardware_continuity_${required//\//_}"
done
for metadata in modules.dep modules.dep.bin modules.alias modules.alias.bin \
  modules.symbols modules.symbols.bin modules.builtin modules.builtin.modinfo \
  modules.order; do
  [[ -f $source_tree/$metadata ]] || fail "metadata_${metadata}"
done

chmod -R u=rwX,go=rX "$source_tree"
mv "$source_tree" "$target"
installed=true
find "$staging" -depth -delete
sync -f "$module_root"

[[ -d $target && ! -L $target ]] || fail installed_tree
[[ $(find "$target" -type f \( -name '*.ko' -o -name '*.ko.zst' -o -name '*.ko.xz' -o -name '*.ko.gz' \) | wc -l) -eq "$expected_module_count" ]] || fail installed_module_count
[[ -d $module_root/7.1.2 ]] || fail rollback_tree_lost

printf 'event=matched_cma_modules_installed release=%s modules=%s rollback_tree=true\n' \
  "$release" "$expected_module_count"
