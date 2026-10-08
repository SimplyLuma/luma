#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Install the exact Luma-configured FP6 speaker fixes with recoverable backups.
# This script does not load modules, restart services, reboot, or access the
# bootloader/partitions. A normal reboot is required before they take effect.

set -euo pipefail
umask 022

bundle=${1:?usage: install-fp6-audio-modules.sh BUNDLE_DIR}
backup_dir=/var/lib/luma/fp6-audio-lab/original-audio-modules-7.1.2
kernel_release=7.1.2

[ "$(id -u)" -eq 0 ] || { printf 'error: run as root\n' >&2; exit 1; }
[ "$(uname -r)" = "$kernel_release" ] || {
  printf 'error: running kernel is not %s\n' "$kernel_release" >&2
  exit 1
}
[ "$(tr -d '\000' </proc/device-tree/model)" = 'The Fairphone (Gen. 6)' ] || {
  printf 'error: device is not the exact Fairphone 6 target\n' >&2
  exit 1
}
for tool in awk depmod install mktemp rm sha256sum tr unzstd zstd; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: missing tool: %s\n' "$tool" >&2
    exit 1
  }
done

preflight_one() {
  local name=$1 expected_new=$2 target=$3 expected_old=$4
  [ -f "$bundle/$name.ko" ] || {
    printf 'error: missing candidate module: %s\n' "$name" >&2
    exit 1
  }
  [ "$(sha256sum "$bundle/$name.ko" | awk '{print $1}')" = "$expected_new" ] || {
    printf 'error: candidate hash differs for %s\n' "$name" >&2
    exit 1
  }
  [ "$(sha256sum "$target" | awk '{print $1}')" = "$expected_old" ] || {
    printf 'error: installed module differs for %s\n' "$name" >&2
    exit 1
  }
  [ ! -e "$backup_dir/$name.ko.zst" ] || {
    printf 'error: backup already exists for %s\n' "$name" >&2
    exit 1
  }
}

install_one() {
  local name=$1 expected_new=$2 target=$3 expected_old=$4
  local actual_new actual_old packed roundtrip
  actual_new=$(sha256sum "$bundle/$name.ko" | awk '{print $1}')
  [ "$actual_new" = "$expected_new" ] || {
    printf 'error: candidate hash differs for %s\n' "$name" >&2
    exit 1
  }
  actual_old=$(sha256sum "$target" | awk '{print $1}')
  if [ "$actual_old" != "$expected_old" ]; then
    printf 'error: installed module differs for %s: %s\n' "$name" "$actual_old" >&2
    exit 1
  fi
  install -d -m 0700 "$backup_dir"
  [ ! -e "$backup_dir/$name.ko.zst" ] || {
    printf 'error: backup already exists for %s\n' "$name" >&2
    exit 1
  }
  install -m 0600 "$target" "$backup_dir/$name.ko.zst"

  packed=$(mktemp "/var/tmp/$name.ko.zst.XXXXXX")
  roundtrip=$(mktemp "/var/tmp/$name.ko.XXXXXX")
  zstd -q -19 -f "$bundle/$name.ko" -o "$packed"
  unzstd -q -f "$packed" -o "$roundtrip"
  [ "$(sha256sum "$roundtrip" | awk '{print $1}')" = "$expected_new" ] || {
    printf 'error: compressed module round-trip differs for %s\n' "$name" >&2
    exit 1
  }
  install -m 0644 "$packed" "$target"
  rm -f "$packed" "$roundtrip"
}

# Complete every identity/hash/backup preflight before the first write.
preflight_one q6apm-lpass-dais \
  13e17e6c9f7a29698e0ef511c26f80eee0f46a07833ccdf4731b3da08da01cca \
  /usr/lib/modules/7.1.2/kernel/sound/soc/qcom/qdsp6/q6apm-lpass-dais.ko.zst \
  62204eddab093588e997d94cf691de63ef8dfd492bbe401c9aa998d4e79fbd30
preflight_one snd-soc-aw88261 \
  89a2de47023e48800ab20609e950c8a1cc997ec684f1507010d12dfa6892c0bb \
  /usr/lib/modules/7.1.2/kernel/sound/soc/codecs/snd-soc-aw88261.ko.zst \
  102cff5f97f3ca534abdff79435f5d3f44261ca11018df06713efe7d336979c5
preflight_one snd-soc-sc8280xp \
  1be797474b17625282c070ac8e3bb478ff5805b99a3363f3ffcaee3b80e0bb7c \
  /usr/lib/modules/7.1.2/kernel/sound/soc/qcom/snd-soc-sc8280xp.ko.zst \
  e9bc25efd7b8037fa04d809e941afe03f13ba23d3e6c82a88890a77eeeedd00a

install_one q6apm-lpass-dais \
  13e17e6c9f7a29698e0ef511c26f80eee0f46a07833ccdf4731b3da08da01cca \
  /usr/lib/modules/7.1.2/kernel/sound/soc/qcom/qdsp6/q6apm-lpass-dais.ko.zst \
  62204eddab093588e997d94cf691de63ef8dfd492bbe401c9aa998d4e79fbd30
install_one snd-soc-aw88261 \
  89a2de47023e48800ab20609e950c8a1cc997ec684f1507010d12dfa6892c0bb \
  /usr/lib/modules/7.1.2/kernel/sound/soc/codecs/snd-soc-aw88261.ko.zst \
  102cff5f97f3ca534abdff79435f5d3f44261ca11018df06713efe7d336979c5
install_one snd-soc-sc8280xp \
  1be797474b17625282c070ac8e3bb478ff5805b99a3363f3ffcaee3b80e0bb7c \
  /usr/lib/modules/7.1.2/kernel/sound/soc/qcom/snd-soc-sc8280xp.ko.zst \
  e9bc25efd7b8037fa04d809e941afe03f13ba23d3e6c82a88890a77eeeedd00a

depmod "$kernel_release"

printf 'installed fixed FP6 audio modules; backups: %s\n' "$backup_dir"
printf 'modules_loaded=false\nservices_restarted=false\nrebooted=false\npartitions_written=false\n'
