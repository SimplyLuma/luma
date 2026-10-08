#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
patch=$repo_root/patches/rmtfs/0001-storage-add-fp6-modem-study.patch
spec=$repo_root/packaging/rpm/rmtfs-fp6.spec
builder=$repo_root/scripts/mobile/build-rmtfs-fp6.sh
smoke=$repo_root/scripts/mobile/smoke-rmtfs-fp6.sh

grep -Fq '27b3a6f00f121a0f8195e25c40faf97b57e2cec1' "$patch"
grep -Fq '{ "/boot/modem_study", "modem_study", "study" },' "$patch"
grep -Fq 'Patch0:         0001-storage-add-fp6-modem-study.patch' "$spec"
grep -Fq 'Release:        2.luma.1%{?dist}' "$spec"
grep -Fq '%global use_source_date_epoch_as_buildtime 1' "$spec"
grep -Fq -- '-ffile-prefix-map=%{_builddir}=.' "$spec"
grep -Fq -- '-frandom-seed=rmtfs-%{version}-%{release}' "$spec"
grep -Fq 'strings rmtfs' "$spec"
grep -Fq 'native AArch64' "$builder"
grep -Fq 'SOURCE_SHA512=' "$builder"
grep -Fq 'SPEC_SHA256=' "$builder"
grep -Fq '.rmtfs-build-fixed' "$builder"
grep -Fq 'PHONE_ACCESSED=false' "$builder"
grep -Fq 'PACKAGE_INSTALLED=false' "$builder"
grep -Fq 'RPM_SIGNATURE=false' "$builder"
grep -Fq 'RUNTIME_BINARY_SHA256=' "$builder"
grep -Fq 'Unsigned evidence only' "$smoke"

if grep -Eq '(^|[[:space:]])(adb|fastboot|flash|erase|wipe|reboot)([[:space:]]|$)' \
  "$builder" "$smoke"; then
  printf 'error: phone or mutation command found in offline rmtfs tooling\n' >&2
  exit 1
fi

printf 'Fedora FP6 rmtfs source contract smoke test: PASS\n'
