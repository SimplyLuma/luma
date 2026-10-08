#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build Atlas installer media that carry a channel's current release
# (ADR-030 section 9: staff media default to nightly and carry the payload).
#
#   build-staff-media.sh --atlas-source DIR --atlas-rpms DIR --output ISO
#                        [--channel nightly|beta]
#                        [--test-candidate BUILD_ID | --candidate BUILD_ID]
#                        [--preview-credential-file FILE]
#
# --preview-credential-file (nightly and beta) puts a root-only preview
# credential on the medium: Atlas writes the credential-checked repository URL
# into the installed system's root-only mirror list. The credential is never
# printed; the .release record, this build's log and Atlas's leak checks must
# not contain it, and the build fails if they do.
#
# --candidate builds the release medium from a build's exported, gated
# candidate before it is published, so a nightly's update and its medium can
# be published together (docs/os/release-process.md); the medium is the
# normal one and the .release record names candidate_build.
#
# --test-candidate builds TEST media from a build's exported candidate commit
# (signed by the release key, not published on the channel) for installer
# testing before the release has passed every gate stage: the volume id says
# TEST, and the .release record names the build and "test=true". Such media
# never go to anyone but the people testing the installer.
#
# 1. A payload repository is made from the channel head: the commit and its
#    content only (no deltas, no other refs), the Luma collection id, and a
#    summary signed with the Luma OS Release key, so the medium is exactly what
#    an online install of that release verifies.
# 2. Atlas's own media builder (scripts/install/atlas-iso/build-installer-iso.sh
#    from the Atlas source) composes the ISO with that payload and the release
#    public key as the only trust anchor.
# 3. The ISO's sha256 and the release it carries are written beside it.
#
# Everything is written below LUMA_OS_ROOT; containers use the pipeline's
# storage.

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"

atlas=
rpms=
output=
channel=nightly
test_build=
candidate_build=
credential_file=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --atlas-source) atlas=$(realpath "${2:?}"); shift 2 ;;
    --atlas-rpms) rpms=$(realpath "${2:?}"); shift 2 ;;
    --output) output=${2:?}; shift 2 ;;
    --channel) channel=${2:?}; shift 2 ;;
    --test-candidate) test_build=${2:?}; shift 2 ;;
    --candidate) candidate_build=${2:?}; shift 2 ;;
    --preview-credential-file) credential_file=$(realpath "${2:?}"); shift 2 ;;
    *) printf 'usage: %s --atlas-source DIR --atlas-rpms DIR --output ISO [--channel nightly|beta]\n' "$0" >&2; exit 2 ;;
  esac
done
[ -n "$atlas" ] && [ -n "$rpms" ] && [ -n "$output" ] || { printf 'missing arguments\n' >&2; exit 2; }
luma_os_require_root
luma_os_check_host
luma_os_check_space 25
ref=$(luma_os_channel_ref "$channel")
[ -z "$test_build" ] || [ -z "$candidate_build" ] || luma_os_die '--test-candidate and --candidate exclude each other'
if [ -n "$test_build$candidate_build" ]; then
  luma_os_load_env "$LUMA_OS_ROOT/builds/${test_build:-$candidate_build}/export.env"
  boot_rpms="$LUMA_OS_ROOT/builds/${test_build:-$candidate_build}/packages/Packages"
  boot_manifest="$LUMA_OS_ROOT/builds/${test_build:-$candidate_build}/luma-packages.manifest"
  source_repo="$LUMA_OS_ROOT/ostree/candidate-repo"
  head=$LUMA_EXPORT_CANDIDATE
  [ "$LUMA_EXPORT_REF" = "$ref" ] || luma_os_die "build ${test_build:-$candidate_build} was exported for $LUMA_EXPORT_REF, not $ref"
  ostree show --repo="$source_repo" "$head" >/dev/null || luma_os_die "candidate $head of ${test_build:-$candidate_build} is not in the candidate repository"
else
  source_repo=$(luma_os_channel_repo "$channel")
  head=$(ostree rev-parse --repo="$source_repo" "$ref")
fi
version=$(python3 "$luma_os_repo_root/scripts/os/lib/ostree_metadata.py" version "$source_repo" "$head")
if [ -z "$test_build$candidate_build" ]; then
  boot_rpms="$LUMA_OS_ROOT/rpms/pool"
  boot_manifest="$source_repo/luma/releases/$version/luma-packages.manifest"
fi
[ -f "$boot_manifest" ] || luma_os_die "payload boot-package manifest missing: $boot_manifest"
. "$atlas/config/install/atlas/runtime-kernel.env"
if [ -n "$test_build$candidate_build" ]; then
  [ "$LUMA_EXPORT_KERNEL" = "$ATLAS_RUNTIME_KERNEL_NVR.$ATLAS_RUNTIME_KERNEL_ARCH" ] ||
    luma_os_die "installer runtime kernel does not match payload: $ATLAS_RUNTIME_KERNEL_NVR.$ATLAS_RUNTIME_KERNEL_ARCH versus $LUMA_EXPORT_KERNEL"
fi
[ -x "$atlas/scripts/install/atlas-iso/build-installer-iso.sh" ] || luma_os_die "not an Atlas source tree: $atlas"

work="$LUMA_OS_ROOT/media/work-$channel"
payload="$LUMA_OS_ROOT/media/payload-$channel/repo"
rm -rf "$LUMA_OS_ROOT/media/payload-$channel"
install -d -m 0755 "$(dirname "$payload")" "$work" "$(dirname "$output")"
ostree init --repo="$payload" --mode=archive --collection-id="$LUMA_OS_COLLECTION_ID"
ostree pull-local --repo="$payload" "$source_repo" "$head" >/dev/null
ostree refs --repo="$payload" --create="$ref" "$head"
luma_os_gpg_unlock
trap luma_os_gpg_lock EXIT
ostree summary --repo="$payload" --update \
  --gpg-sign="$(luma_os_gpg_fingerprint)" --gpg-homedir="$(luma_os_gpg_home)"
luma_os_gpg_lock
trap - EXIT
# Same check a device makes, with only the public key.
client=$(mktemp -d "$LUMA_OS_ROOT/tmp/media-verify.XXXXXX")
ostree init --repo="$client/repo" --mode=archive >/dev/null
ostree remote add --repo="$client/repo" --gpg-import="$LUMA_OS_KEYS/luma-os-release.gpg" \
  --set=gpg-verify=true --set=gpg-verify-summary=true --collection-id="$LUMA_OS_COLLECTION_ID" \
  medium "file://$payload" "$ref"
ostree pull --repo="$client/repo" --commit-metadata-only medium "$ref" >/dev/null
[ "$(ostree rev-parse --repo="$client/repo" "medium:$ref")" = "$head" ] || luma_os_die 'payload verification failed'
rm -rf "$client"
luma_os_log "payload: $version ($head), $(du -sh "$payload" | awk '{ print $1 }')"

credential_args=()
if [ -n "$credential_file" ]; then
  luma_os_channel_is_preview "$channel" || luma_os_die 'a preview credential is only for nightly and beta media'
  [ -r "$credential_file" ] && [ "$(stat -c %a "$credential_file")" = 600 ] ||
    luma_os_die "preview credential file must be readable and mode 0600: $credential_file"
  credential_args=(--preview-credential-file "$credential_file")
fi

# The medium names the release it installs exactly as the release names itself
# (os-release PRETTY_NAME, ADR-040): boot menu titles and, for release media,
# the volume label ("Luma-Prairie-Beta-0-20260916"). Test media keep a TEST label.
display_name=$(ostree cat --repo="$source_repo" "$head" /usr/lib/os-release 2>/dev/null |
  sed -n 's/^PRETTY_NAME=//p' | tr -d '"' | tail -n 1)
case "$display_name" in "Luma ("*")") ;; *) display_name= ;; esac
media_args=()
[ -z "$display_name" ] || media_args+=(--display-name "$display_name")
[ -z "$test_build" ] || media_args+=(--volid "Luma-TEST-${channel^}-x86_64")

CONTAINERS_STORAGE_CONF="$LUMA_OS_ROOT/etc/storage.conf" \
CONTAINERS_CONF="$LUMA_OS_ROOT/etc/containers.conf" \
TMPDIR="$LUMA_OS_ROOT/tmp" \
  "$atlas/scripts/install/atlas-iso/build-installer-iso.sh" \
    --rpms "$rpms" --boot-rpms "$boot_rpms" --boot-manifest "$boot_manifest" \
    --work "$work" --output "$output" \
    --channel "$channel" --payload-repo "$payload" \
    --release-key "$LUMA_OS_KEYS/luma-os-release.asc" \
    "${credential_args[@]}" "${media_args[@]}"

sha256sum "$output" | awk '{ print $1 }' >"$output.sha256"
cat >"$output.release" <<EOF
channel=$channel
ref=$ref
commit=$head
version=$version
atlas_source_revision=$(git -c "safe.directory=$atlas" -C "$atlas" rev-parse HEAD 2>/dev/null || echo unknown)
release_key_fingerprint=$(luma_os_gpg_fingerprint)
built_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
test=${test_build:+true}
test_build=$test_build
candidate_build=$candidate_build
EOF
preview_credential=${credential_file:+carried}
sed -i "s/^test_build=.*/&\npreview_credential=${preview_credential:-none}/" "$output.release"
if [ -n "$credential_file" ]; then
  # The sidecars and this job's own output must not carry the credential.
  secret=$(tr -d '\n' <"$credential_file")
  for file in "$output.release" "$output.sha256" "$output.media" "$work/logs"/* ${LUMA_OS_JOB_LOG:+"$LUMA_OS_JOB_LOG"}; do
    [ -f "$file" ] || continue
    if grep -Fq -- "$secret" "$file"; then
      unset secret
      luma_os_die "the preview credential leaked into $file"
    fi
  done
  unset secret
fi
luma_os_log "media: $output ($(cat "$output.sha256"))"
