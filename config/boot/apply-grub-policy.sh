#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

# A real loader policy, never a GSettings preference or splash countdown.
choice_timeout=5
print_policy=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --timeout)
      [[ $# -ge 2 && $2 =~ ^([0-9]|10)$ ]] || {
        echo 'error: --timeout must be an integer from 0 to 10 seconds' >&2; exit 2;
      }
      choice_timeout=$2; shift 2 ;;
    --print-policy) print_policy=1; shift ;;
    *) echo 'usage: apply-grub-policy.sh [--timeout 0..10] [--print-policy]' >&2; exit 2 ;;
  esac
done
timeout_style=menu
if [[ $choice_timeout == 0 ]]; then timeout_style=hidden; fi
if [[ $print_policy == 1 ]]; then
  printf 'timeout_style=%s\ntimeout=%s\n' "$timeout_style" "$choice_timeout"
  exit 0
fi

# This policy delegates an OSTree-owned dynamic BLS invocation. A mutable
# Workstation grub.cfg is generated and must remain under Fedora's ownership.
[[ -e /run/ostree-booted ]] || {
  printf 'error: Luma advanced GRUB policy requires an OSTree boot\n' >&2
  exit 1
}

grub_cfg=/boot/grub2/grub.cfg
grub_dir=${grub_cfg%/*}
theme_dir=$grub_dir/themes/luma
custom_cfg=$grub_dir/luma.cfg

# Atomic Fedora's OSTree generator owns grub.cfg. Do not rewrite that generated
# file with grub2-mkconfig: composefs is intentionally not a canonical root for
# grub2-probe. Its generated header loads GRUB's persistent environment after
# establishing the fallback timeout, which is the supported policy boundary.
grep -Fq 'load_env' "$grub_cfg"
grep -Eq '(^|[[:space:]])blscfg([[:space:]]|$)|source \$prefix/luma.cfg' "$grub_cfg"

install -D -m 0644 /usr/share/luma/boot/grub-theme/theme.txt \
  "$theme_dir/theme.txt"
for font in prairie.pf2 prairie-12.pf2 prairie-22.pf2; do
  install -D -m 0644 "/usr/share/luma/boot/grub-theme/$font" "$theme_dir/$font"
done
install -D -m 0644 /usr/share/luma/boot/luma.cfg "$custom_cfg"

# The Atomic-generated configuration remains the owner of deployment data.
# Replace only its dynamic `blscfg` invocation with Luma's dynamic wrapper;
# every BLS file is still read live at boot and no deployment entry is copied.
if ! grep -Fq 'source $prefix/luma.cfg' "$grub_cfg"; then
  if [[ ! -e $grub_cfg.pre-luma-advanced ]]; then
    cp --reflink=auto --preserve=all "$grub_cfg" \
      "$grub_cfg.pre-luma-advanced"
  fi
  tmp_cfg=$(mktemp "$grub_dir/grub.cfg.luma.XXXXXX")
  awk '
    /^[[:space:]]*blscfg([[:space:]].*)?$/ {
      print "source $prefix/luma.cfg"
      inserted = 1
      next
    }
    { print }
    END { if (!inserted) exit 1 }
  ' "$grub_cfg" >"$tmp_cfg"
  chmod --reference="$grub_cfg" "$tmp_cfg"
  chown --reference="$grub_cfg" "$tmp_cfg"
  mv -f "$tmp_cfg" "$grub_cfg"
fi

grub2-editenv - set "timeout_style=$timeout_style" "timeout=$choice_timeout"

grub2-editenv list | grep -Fxq "timeout_style=$timeout_style"
grub2-editenv list | grep -Fxq "timeout=$choice_timeout"
grep -Fq 'source $prefix/luma.cfg' "$grub_cfg"
