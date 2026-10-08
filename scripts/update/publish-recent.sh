#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: publish-recent.sh --image PATH --source-repo PATH \
  --build-report PATH --repo PATH \
  --gpg-key FINGERPRINT --gpg-homedir PATH [--deployment-json PATH]
EOF
  exit 2
}

image=
source_repo=
build_report=
publish_repo=
gpg_key=
gpg_homedir=
deployment_json=
stage_remote=${LUMA_STAGE_REMOTE:-origin}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --image) image=${2:-}; shift 2 ;;
    --source-repo) source_repo=${2:-}; shift 2 ;;
    --build-report) build_report=${2:-}; shift 2 ;;
    --repo) publish_repo=${2:-}; shift 2 ;;
    --gpg-key) gpg_key=${2:-}; shift 2 ;;
    --gpg-homedir) gpg_homedir=${2:-}; shift 2 ;;
    --deployment-json) deployment_json=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done
for value in image source_repo build_report publish_repo gpg_key gpg_homedir; do
  [ -n "${!value}" ] || usage
done
[ -n "$deployment_json" ] || deployment_json="$image.deployment.json"

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

for tool in git python3 sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || fail "required publishing tool is missing: $tool"
done
[ "$(uname -s)" = Linux ] || fail 'Recent publishing requires the Linux release builder'
[ -f "$image" ] || fail "desktop image is missing: $image"
[ -f "$source_repo/config" ] || fail "source OSTree repository is invalid: $source_repo"
[ -f "$build_report" ] || fail "build report is missing: $build_report"
[ -f "$deployment_json" ] || fail "deployment identity is missing: $deployment_json"

report_reader="$repo_root/scripts/update/read-build-report-value.sh"
[ -x "$report_reader" ] || fail "build-report reader is unavailable: $report_reader"
source_revision=$("$report_reader" "$build_report" source_revision)
source_state=$("$report_reader" "$build_report" source_state)
expected_image_sha256=$("$report_reader" "$build_report" output_sha256)
[ "$source_state" = clean ] || fail 'only a clean source build may enter Recent'
actual_image_sha256=$(sha256sum "$image" | awk '{print $1}')
[ "$actual_image_sha256" = "$expected_image_sha256" ] ||
  fail 'desktop image does not match its build report'

[[ "$source_revision" =~ ^[0-9a-f]{40}$ ]] || fail 'invalid source revision in build report'
[[ "$stage_remote" =~ ^[A-Za-z0-9._-]+$ ]] || fail 'invalid Stage Git remote name'
git -C "$repo_root" cat-file -e "$source_revision^{commit}" 2>/dev/null ||
  fail 'build report source revision is not present in this repository'
git -C "$repo_root" merge-base --is-ancestor \
  "$source_revision" "$stage_remote/stage" ||
  fail "build report revision is not published on $stage_remote/stage"

deployment_reader="$repo_root/scripts/update/read-booted-deployment-checksum.py"
[ -x "$deployment_reader" ] ||
  fail "deployment identity reader is unavailable: $deployment_reader"
commit=$("$deployment_reader" "$deployment_json")

"$repo_root/scripts/update/promote-ostree-commit.sh" \
  --source-repo "$source_repo" \
  --commit "$commit" \
  --repo "$publish_repo" \
  --build-report "$build_report" \
  --image-sha256 "$actual_image_sha256" \
  --source-revision "$source_revision" \
  --gpg-key "$gpg_key" \
  --gpg-homedir "$gpg_homedir"
