#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Fill the verified Luma package pool with the RPMs pinned in
# config/desktop/packages.txt.
#
# The pool ($LUMA_OS_ROOT/rpms/pool) holds exactly one file per pinned NEVRA,
# recorded in pool.manifest as "NEVRA sha256 header-sha256". A pin is admitted
# from the search directories only when every candidate file with that name has
# the same header digest, or when --reference names the header digest a
# qualified deployment's rpmdb recorded for it. A pool entry never changes
# silently: if a later candidate differs from the admitted file, the collection
# fails and names both.
#
#   collect-packages.sh [--reference NEVRA-HEADER-LIST] [--dry-run]
#                       [--allow-unverified-drop] DIR...
#
# NEVRA-HEADER-LIST lines are "NEVRA sha256-header", for example the output of
#   rpm --dbpath=<deployment>/usr/lib/sysimage/rpm -qa --qf '%{NEVRA} %{SHA256HEADER}\n'

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"

reference=
dry_run=0
verify_drops=1
dirs=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --reference) reference=${2:?}; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    --allow-unverified-drop) verify_drops=0; shift ;;
    -*) printf 'usage: %s [--reference FILE] [--dry-run] [--allow-unverified-drop] DIR...\n' "$0" >&2; exit 2 ;;
    *) dirs+=("$1"); shift ;;
  esac
done

luma_os_require_tools rpm sha256sum find
# A drop is checked before anything is taken from it: every NEVRA it claims
# must be carried by a file whose RPM header says so. File names and
# packages.txt are both text, and a build script with a stale release variable
# kept them saying a new release while the copy step shipped the previous
# build, unnoticed for three releases. Only the header is evidence.
# --allow-unverified-drop exists for the rare case that needs it and says so
# in the log.
if [ "$verify_drops" -eq 1 ] && [ "${#dirs[@]}" -gt 0 ]; then
  if ! "$(dirname -- "$0")/verify-drop.sh" "${dirs[@]}"; then
    luma_os_die 'a drop does not deliver what it claims; fix the drop, or pass --allow-unverified-drop to collect from it anyway'
  fi
elif [ "${#dirs[@]}" -gt 0 ]; then
  luma_os_log 'collecting without the drop check (--allow-unverified-drop)'
fi
pool="$LUMA_OS_ROOT/rpms/pool"
manifest="$pool/pool.manifest"
install -d -m 0755 "$pool"
touch "$manifest"

declare -A want_header=()
if [ -n "$reference" ]; then
  while read -r nevra header; do
    [ -n "$nevra" ] && want_header[$nevra]=$header
  done <"$reference"
fi

mapfile -t pins < <(sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' \
  "$luma_os_repo_root/config/desktop/packages.txt")
mapfile -t -O "${#pins[@]}" pins < <(sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' \
  "$luma_os_repo_root/config/os/fedora-packages.txt" | grep -E '\.(x86_64|aarch64|noarch)$' || true)

missing=0
for nevra in "${pins[@]}"; do
  admitted=$(awk -v n="$nevra" '$1 == n { print $3 }' "$manifest")
  if [ -n "$admitted" ] && [ -f "$pool/$nevra.rpm" ]; then
    actual=$(rpm -qp --nosignature --qf '%{SHA256HEADER}' "$pool/$nevra.rpm")
    [ "$actual" = "$admitted" ] || luma_os_die "pool file for $nevra no longer matches its manifest"
    continue
  fi
  mapfile -t candidates < <(
    [ "${#dirs[@]}" -gt 0 ] && find "${dirs[@]}" -type f -name "$nevra.rpm" 2>/dev/null | LC_ALL=C sort
  )
  chosen=
  declare -A headers=()
  for candidate in "${candidates[@]}"; do
    header=$(rpm -qp --nosignature --qf '%{SHA256HEADER}' "$candidate" 2>/dev/null) || continue
    headers[$header]=$candidate
  done
  if [ -n "${want_header[$nevra]:-}" ]; then
    chosen=${headers[${want_header[$nevra]}]:-}
    [ -n "$chosen" ] || { printf 'missing: %s (no candidate matches the reference header)\n' "$nevra" >&2; missing=1; unset headers; continue; }
  elif [ "${#headers[@]}" -eq 1 ]; then
    chosen=${candidates[0]}
  elif [ "${#headers[@]}" -gt 1 ]; then
    printf 'ambiguous: %s has %d different builds with the same NEVRA:\n' "$nevra" "${#headers[@]}" >&2
    printf '  %s\n' "${headers[@]}" >&2
    missing=1; unset headers; continue
  else
    printf 'missing: %s\n' "$nevra" >&2
    missing=1; unset headers; continue
  fi
  unset headers
  header=$(rpm -qp --nosignature --qf '%{SHA256HEADER}' "$chosen")
  if [ "$dry_run" -eq 1 ]; then
    printf 'would admit %s from %s\n' "$nevra" "$chosen"
    continue
  fi
  install -m 0644 "$chosen" "$pool/$nevra.rpm.partial"
  mv "$pool/$nevra.rpm.partial" "$pool/$nevra.rpm"
  printf '%s %s %s\n' "$nevra" "$(luma_os_sha256 "$pool/$nevra.rpm")" "$header" >>"$manifest"
  printf 'admitted %s\n' "$nevra"
done
[ "$missing" -eq 0 ] || luma_os_die 'the package pool is incomplete for the current pins'
printf 'package pool complete: %d pins\n' "${#pins[@]}"
