#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Prove a Luma IBus RPM set installs as an upgrade of Fedora's IBus on a
# system that has Fedora's IBus engines, and that every engine stays.
#
#   check-ibus-upgrade.sh RPMDIR
#
# RPMDIR holds the binary RPMs to ship (a drop's RPMS/, flattened or not).
# In a clean Fedora 44 container: install Fedora's ibus 1.5.34-4 set plus the
# engines a Luma image carries, then `dnf install` the Luma RPMs. Fails if the
# transaction fails, if any engine or IBus package is removed, or if any
# installed IBus package is not from RPMDIR afterwards.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

[ "$#" -eq 1 ] || { printf 'usage: %s RPMDIR\n' "$0" >&2; exit 2; }
rpm_dir=$(realpath "$1")
rpm_count=$(find "$rpm_dir" -name '*.rpm' ! -name '*.src.rpm' ! -name '*-debug*' | wc -l)
[ "$rpm_count" -gt 0 ] || { printf 'error: no binary RPMs in %s\n' "$rpm_dir" >&2; exit 1; }

# The RPMs go in on stdin: no bind mount, so no SELinux relabelling of the
# (possibly shared) directory they live in.
find "$rpm_dir" -name '*.rpm' ! -name '*.src.rpm' ! -name '*-debug*' -printf '%P\0' |
  tar -C "$rpm_dir" --null -T - -cf - |
  podman run --rm -i --network=host "$FEDORA_RPM_BUILD_CONTAINER" bash -euo pipefail -c '
  mkdir /rpms && tar -xf - -C /rpms
  fail() { printf "FAIL: %s\n" "$1" >&2; exit 1; }
  # What a Luma image and the owner machine have: Fedora IBus and its engines.
  fedora_set="ibus-1.5.34-4.fc44 ibus-libs-1.5.34-4.fc44 ibus-gtk3-1.5.34-4.fc44
    ibus-gtk4-1.5.34-4.fc44 ibus-setup-1.5.34-4.fc44 python3-ibus-1.5.34-4.fc44"
  engines="ibus-anthy ibus-anthy-python ibus-chewing ibus-hangul ibus-libpinyin
    ibus-m17n ibus-typing-booster"
  dnf5 -y -q install $fedora_set $engines >/dev/null 2>&1 || fail "Fedora IBus and engines did not install"
  rpm -q $engines >/dev/null || fail "Fedora engines did not install"
  printf "before: %s\n" "$(rpm -q ibus python3-ibus | tr "\n" " ")"

  mapfile -t luma < <(find /rpms -name "*.rpm" ! -name "*.src.rpm" ! -name "*-debug*" | sort)
  printf "installing %s Luma RPMs\n" "${#luma[@]}"
  [ "${#luma[@]}" -gt 0 ] || fail "no Luma RPMs reached the container"
  rpm -qa --qf "%{NAME}\n" | sort > /tmp/before
  dnf5 -y install "${luma[@]}" > /tmp/upgrade.log 2>&1 || { grep -v "^\[" /tmp/upgrade.log | tail -20; fail "dnf install of the Luma set failed"; }
  grep -E "^(Upgrading|Installing|Removing|Downgrading):" -A12 /tmp/upgrade.log | grep -E "^ " | head -20 || :
  rpm -qa --qf "%{NAME}\n" | sort > /tmp/after

  removed=$(comm -23 /tmp/before /tmp/after)
  [ -z "$removed" ] || fail "removed by the upgrade: $removed"
  rpm -q $engines >/dev/null || fail "an engine is missing after the upgrade"
  # Every installed IBus package is now exactly the file that was offered.
  checked=0
  for f in "${luma[@]}"; do
    nevra=$(rpm -qp --qf "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}" "$f")
    name=$(rpm -qp --qf "%{NAME}" "$f")
    rpm -q "$name" >/dev/null 2>&1 || continue
    [ "$(rpm -q "$name")" = "$nevra" ] || fail "$name is $(rpm -q "$name"), expected $nevra"
    checked=$((checked + 1))
  done
  [ "$checked" -ge 6 ] || fail "only $checked Luma IBus packages ended up installed, expected 6"
  leftover=$(rpm -qa "ibus*" "python3-ibus*" | grep -E "^(python3-)?ibus(-libs|-gtk[34]|-setup)?-1\.5\.34-4\.fc44" || :)
  [ -z "$leftover" ] || fail "Fedora IBus still installed: $leftover"
  # The daemon and an engine still start their binaries (no missing libraries).
  ibus version >/dev/null || fail "ibus does not run"
  python3 -c "import gi; gi.require_version(\"IBus\", \"1.0\"); from gi.repository import IBus; print(\"python3 IBus\", IBus.MAJOR_VERSION, IBus.MINOR_VERSION, IBus.MICRO_VERSION)"
  printf "after: %s\n" "$(rpm -q ibus ibus-libs ibus-gtk3 ibus-gtk4 ibus-setup python3-ibus | tr "\n" " ")"
  printf "engines kept: %s\n" "$(rpm -q $engines | tr "\n" " ")"
  printf "IBus upgrade install: PASS (%s Luma packages installed)\n" "$checked"
'
