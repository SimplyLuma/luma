#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Finish a keyless native export on the authorized release signing host.
# Incoming build evidence must occupy $LUMA_OS_ROOT/builds/ID. The receipt
# digest/source/image arguments come from the authenticated native builder.
set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"
build_id= prepared_repo= receipt_sha= expected_source= expected_image=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --build-id) build_id=${2:?}; shift 2 ;;
    --prepared-repo) prepared_repo=$(realpath "${2:?}"); shift 2 ;;
    --receipt-sha256) receipt_sha=${2:?}; shift 2 ;;
    --source-revision) expected_source=${2:?}; shift 2 ;;
    --image-id) expected_image=${2:?}; shift 2 ;;
    *) printf 'usage: %s --build-id ID --prepared-repo REPO --receipt-sha256 SHA --source-revision SHA --image-id SHA\n' "$0" >&2; exit 2 ;;
  esac
done
[[ "$build_id" =~ ^[0-9]{8}\.[0-9]+$ && "$expected_source" =~ ^[0-9a-f]{40}$ && "$expected_image" =~ ^[0-9a-f]{64}$ ]] || luma_os_die 'invalid native export identity'
luma_os_require_root
luma_os_require_tools ostree gpg python3 flock
luma_os_check_host
luma_os_check_space 20
build_dir="$LUMA_OS_ROOT/builds/$build_id"
[ ! -e "$build_dir/export.env" ] || luma_os_die 'build already has a signed export'
# Validate the incoming environment's whitelist before the shell loads it:
# an artifact must never choose signing paths, keys or source directories.
python3 -B - "$luma_os_repo_root/scripts/os/lib" "$build_dir" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from prepared_export import validate_files, require_private_inputs
require_private_inputs(Path(sys.argv[2]))
validate_files(Path(sys.argv[2]))
PY
luma_os_load_build_env "$build_dir/build.env"
[ "$LUMA_BUILD_ID" = "$build_id" ] || luma_os_die 'build id differs from selected native export'
ref=$(luma_os_channel_ref "$LUMA_OS_CHANNEL")
published_repo=$(luma_os_channel_repo "$LUMA_OS_CHANNEL")
install -d -m 0700 "$LUMA_OS_ROOT/locks" "$LUMA_OS_ROOT/ostree"
exec 8>"$LUMA_OS_ROOT/locks/candidate-repo.lock"
flock -w 3600 8 || luma_os_die 'candidate repository is locked'
parent=
if [ -f "$published_repo/config" ]; then
  parent=$(ostree rev-parse --repo="$published_repo" "$ref" 2>/dev/null || true)
fi
verified=$(mktemp "$build_dir/.verified-native-export.XXXXXX")
rm "$verified"
trap 'rm -f "$verified"' EXIT
python3 "$luma_os_repo_root/scripts/os/lib/prepared_export.py" verify \
  --build-dir "$build_dir" --receipt-sha256 "$receipt_sha" \
  --source-revision "$expected_source" --image-id "$expected_image" \
  --parent "$parent" --env-output "$verified"
luma_os_load_env "$verified"
[ "$LUMA_EXPORT_REF" = "$ref" ] || luma_os_die 'prepared ref differs from selected channel'
candidate_repo="$LUMA_OS_ROOT/ostree/candidate-repo"
if [ ! -f "$candidate_repo/config" ]; then
  ostree init --repo="$candidate_repo" --mode=archive --collection-id="$LUMA_OS_COLLECTION_ID"
fi
[ "$(ostree config --repo="$candidate_repo" get core.collection-id)" = "$LUMA_OS_COLLECTION_ID" ] || luma_os_die 'candidate collection differs'
log="$build_dir/logs/sign-prepared-export.log"
install -d -m 0755 "$build_dir/logs"
# Pull validates every object checksum; no ARM execution occurs on the signer.
python3 -B - "$luma_os_repo_root/scripts/os/lib" "$candidate_repo" "$prepared_repo" \
  "$LUMA_EXPORT_TREE_COMMIT" "$LUMA_EXPORT_CANDIDATE" >>"$log" 2>&1 <<'PYPULL'
import sys
sys.path.insert(0, sys.argv[1])
from prepared_export import pull_prepared_objects
pull_prepared_objects(*sys.argv[2:])
PYPULL
# Verify parent, source, image/provenance, architecture, ref binding and the
# complete package inventory against the actual imported commit metadata.
python3 -B - "$luma_os_repo_root/scripts/os/lib" "$candidate_repo" "$build_dir" <<'PYVERIFY'
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from prepared_export import verify_commit
print(json.dumps(verify_commit(Path(sys.argv[2]),Path(sys.argv[3]),
    json.loads((Path(sys.argv[3])/'export-prepared.json').read_bytes()))))
PYVERIFY
python3 "$luma_os_repo_root/scripts/os/lib/repair_import_metadata.py" verify \
  "$candidate_repo" "$LUMA_EXPORT_TREE_COMMIT" "$LUMA_EXPORT_CANDIDATE" \
  "$build_dir/image-import-metadata.json" "$build_dir/image-modes.txt" >>"$log"
python3 "$luma_os_repo_root/scripts/os/lib/repair_modes.py" check \
  "$candidate_repo" "$LUMA_EXPORT_CANDIDATE" "$build_dir/image-modes.txt" >>"$log"
actual_root=$(python3 "$luma_os_repo_root/scripts/os/lib/ostree_metadata.py" root-dirtree "$candidate_repo" "$LUMA_EXPORT_CANDIDATE")
[ "$actual_root" = "$LUMA_EXPORT_ROOT_DIRTREE" ] || luma_os_die 'native candidate root changed'
current_parent=
if [ -f "$published_repo/config" ]; then
  current_parent=$(ostree rev-parse --repo="$published_repo" "$ref" 2>/dev/null || true)
fi
[ "$current_parent" = "$parent" ] || luma_os_die 'published parent moved during native export verification'
luma_os_gpg_unlock
trap 'luma_os_gpg_lock; rm -f "$verified"' EXIT
ostree gpg-sign --repo="$candidate_repo" --gpg-homedir="$(luma_os_gpg_home)" \
  "$LUMA_EXPORT_CANDIDATE" "$(luma_os_gpg_fingerprint)"
ostree refs --repo="$candidate_repo" --create="$ref" --force "$LUMA_EXPORT_CANDIDATE"
[ "$(ostree rev-parse --repo="$candidate_repo" "$ref")" = "$LUMA_EXPORT_CANDIDATE" ] || luma_os_die 'candidate ref did not move'
ostree summary --repo="$candidate_repo" --update \
  --gpg-sign="$(luma_os_gpg_fingerprint)" --gpg-homedir="$(luma_os_gpg_home)"
luma_os_gpg_lock
body_index_sha=
if [ "${LUMA_OS_CAPTURE_PAYLOAD_BODIES:-0}" = 1 ]; then
  body_args=()
  if [ -n "${LUMA_OS_PREVIOUS_BODY_INDEX:-}" ]; then
    body_args=(--previous "$LUMA_OS_PREVIOUS_BODY_INDEX" --previous-sha256 "${LUMA_OS_PREVIOUS_BODY_INDEX_SHA256:?}")
  fi
  body_index_sha=$(python3 "$luma_os_repo_root/scripts/install/atlas-iso/payload-object-reuse.py" \
    --scope candidate-cache --repository "$candidate_repo" --output "$build_dir/payload-object-bodies.json" \
    --source-revision "$LUMA_SOURCE_REVISION" --candidate "$LUMA_EXPORT_CANDIDATE" \
    --architecture "$LUMA_OS_ARCH" --profile "$LUMA_OS_PROFILE" --ref "$ref" "${body_args[@]}" 2>>"$log")
  printf 'PAYLOAD_CACHE_INDEX_SHA256=%s\n' "$body_index_sha" >>"$log"
fi
cat "$verified" >"$build_dir/export.env.new"
printf 'LUMA_EXPORT_BODY_INDEX_SHA256=%s\n' "$body_index_sha" >>"$build_dir/export.env.new"
printf 'LUMA_EXPORT_COMPLETED=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$build_dir/export.env.new"
mv "$build_dir/export.env.new" "$build_dir/export.env"
luma_os_log "signed native candidate $LUMA_EXPORT_CANDIDATE on $ref; qualification remains pending"
printf '%s\n' "$LUMA_EXPORT_CANDIDATE"
