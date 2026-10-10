#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build one Luma desktop OS image from image/luma-desktop/Containerfile.
#
#   build-image.sh [--build-id YYYYMMDD.N] [--channel nightly|beta|stable]
#                  [--base pinned|latest] [--source DIR] [--cache]
#                  [--allow-dirty] --app-baseline /signed/offline/bundles
#
# --cache reuses podman layers between development builds. Layer reuse does not
# notice changes in files the RUN steps read through build-context mounts, so
# it is refused together with a clean source; release builds never use it.
#
# Output: $LUMA_OS_ROOT/builds/<build-id>/ with the build log, the image
# reference and digest, the package inventory, the SBOM, provenance.json and
# an OCI layout the OSTree export consumes. The image is tagged
# localhost/luma-os/desktop:<build-id> in the pipeline's own container storage.
#
# --base latest resolves the newest Fedora Silverblue 44 x86_64 tag and records
# its digest (nightly automation); the default uses the digest pinned in
# config/os/release.env. --allow-dirty is for development: such a build is
# marked dirty and every publication step refuses it.

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"

build_id=
channel=nightly
base_policy=pinned
source_dir=$luma_os_repo_root
use_cache=0
allow_dirty=0
source_snapshot=
app_baseline=${LUMA_OS_APP_BASELINE:-}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --build-id) build_id=${2:?}; shift 2 ;;
    --channel) channel=${2:?}; shift 2 ;;
    --base) base_policy=${2:?}; shift 2 ;;
    --source) source_dir=$(realpath "${2:?}"); shift 2 ;;
    --cache) use_cache=1; shift ;;
    --allow-dirty) allow_dirty=1; shift ;;
    --source-snapshot) source_snapshot=$(realpath "${2:?}"); shift 2 ;;
    --app-baseline) app_baseline=$(realpath "${2:?}"); shift 2 ;;
    *) printf 'usage: %s [--build-id ID] [--channel C] [--base pinned|latest] [--source DIR] [--cache] [--allow-dirty] [--source-snapshot FILE] --app-baseline DIR\n' "$0" >&2; exit 2 ;;
  esac
done

luma_os_require_root
luma_os_require_tools podman git python3 sha256sum rpm
luma_os_check_host
luma_os_check_space 30
luma_os_channel_ref "$channel" >/dev/null
[ -n "$app_baseline" ] || luma_os_die 'a signed offline OS application baseline is required (--app-baseline DIR)'
app_baseline=$(realpath "$app_baseline")
app_baseline_admission=$(python3 "$source_dir/scripts/os/lib/app_baseline.py" "$app_baseline" \
  --shipping "$source_dir/config/os/first-party-app-baseline.json") || \
  luma_os_die 'the offline application input fails this OS shipping contract'
app_baseline_sha=$(printf '%s' "$app_baseline_admission" | python3 -c 'import json,sys; print(json.load(sys.stdin)["manifest_sha256"])')

# Source identity.
git_src() { git -c "safe.directory=$source_dir" -C "$source_dir" "$@"; }
source_revision=$(git_src rev-parse HEAD 2>/dev/null || true)
[[ "$source_revision" =~ ^[0-9a-f]{40}$ ]] || luma_os_die "source is not a Git checkout: $source_dir"
dirty=false
if [ -n "$(git_src status --porcelain --untracked-files=no)" ]; then
  dirty=true
fi
if [ "$source_dir" != "$luma_os_repo_root" ] && [ -n "$(git -c "safe.directory=$luma_os_repo_root" -C "$luma_os_repo_root" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  luma_os_log 'note: the pipeline scripts themselves come from a dirty checkout'
fi
if [ "$use_cache" -eq 1 ] && [ "$allow_dirty" -ne 1 ]; then
  luma_os_die '--cache is for development builds only (with --allow-dirty)'
fi
if [ "$dirty" = true ] && [ "$allow_dirty" -ne 1 ]; then
  luma_os_die "source checkout has uncommitted changes: $source_dir (use --allow-dirty for a development build)"
fi

# Explicit private source snapshots never acquire a clean/public identity.
if [ -n "$source_snapshot" ]; then
  [ "$allow_dirty" -eq 1 ] || luma_os_die '--source-snapshot requires --allow-dirty (private builds only)'
  dirty=true
fi

# Reproducibility of the pins, before anything is built from them. Every Luma
# package this source pins has to name the release this source's own spec
# builds; a pin ahead of its spec is a package that was built outside the tree
# and cannot be rebuilt from this revision, so the image it goes into is not
# reproducible either. luma-developer-platform 1.luma.64 and 1.luma.65 shipped
# in nightlies exactly that way, because the assertion existed in
# tests/smoke/internal-stage-contract.sh and was not part of this pipeline.
# The contract reports on stdout, and this script's stdout is its result: the
# nightly keeps only its last line (the build directory). Its report goes to
# stderr, which the nightly log captures, or a failure shows only the line below.
if ! bash "$source_dir/tests/smoke/package-release-contract.sh" "$source_dir" >&2; then
  luma_os_die 'the source pins packages this source cannot build; see the package release contract above'
fi

builds="$LUMA_OS_ROOT/builds"
install -d -m 0755 "$builds"
# Build ids are never reused, even after prune removed a build's directory:
# the last id handed out is recorded, and every id of the day that a build
# directory or a published release already names is skipped.
exec 7>"$builds/.id.lock"
flock -w 60 7 || luma_os_die 'could not lock the build id counter'
if [ -z "$build_id" ]; then
  # A nightly is named for the day it closes out: the run at 00:00 in
  # America/Chicago on the 18th builds the 17th's nightly (owner decision
  # 2026-09-16). Ids never go backwards: a run whose day is earlier than the
  # newest id already handed out continues that id's day and sequence.
  day=$(TZ=America/Chicago date -d 'yesterday' +%Y%m%d)
  n=1
  last=$(cat "$builds/.last-id" 2>/dev/null || true)
  if [[ "${last%%.*}" =~ ^[0-9]{8}$ ]] && [ "${last%%.*}" -gt "$day" ]; then
    day=${last%%.*}
  fi
  if [ "${last%%.*}" = "$day" ] && [[ "${last#*.}" =~ ^[0-9]+$ ]]; then
    n=$((${last#*.} + 1))
  fi
  while [ -e "$builds/$day.$n" ] ||
        compgen -G "$LUMA_OS_ROOT/publish/*/luma/releases/*.$day.$n" >/dev/null; do
    n=$((n + 1))
  done
  build_id="$day.$n"
fi
printf '%s\n' "$build_id" >"$builds/.last-id.new" && mv "$builds/.last-id.new" "$builds/.last-id"
exec 7>&-
[[ "$build_id" =~ ^[0-9]{8}\.[0-9]+$ ]] || luma_os_die "invalid build id: $build_id"
build_dir="$builds/$build_id"
[ ! -e "$build_dir" ] || luma_os_die "build directory already exists: $build_dir"
install -d -m 0755 "$build_dir" "$build_dir/logs"
exec 9>"$build_dir/.build.lock"
flock -n 9 || luma_os_die "another build holds $build_id"
source_snapshot_sha=
release_checks_sha=
if [ -n "$source_snapshot" ]; then
  snapshot_binding=$(python3 "$source_dir/scripts/os/lib/source_snapshot.py" capture \
    --root "$source_dir" --manifest "$source_snapshot" --revision "$source_revision" \
    --output "$build_dir/source-checks") || luma_os_die 'private source snapshot admission failed'
  source_snapshot_sha=$(printf '%s' "$snapshot_binding" | python3 -c 'import json,sys; print(json.load(sys.stdin)["source_snapshot_sha256"])')
  release_checks_sha=$(printf '%s' "$snapshot_binding" | python3 -c 'import json,sys; print(json.load(sys.stdin)["release_checks_sha256"])')
fi

# ADR-040 requires beta and stable trees to carry their own release identity.
# The channel was checked above; release_identity.py below enforces Beta >= 1
# and the final stage. Do not relabel a modern nightly tree by promotion.
started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
identity="$source_dir/scripts/os/lib/release_identity.py"
version=$(python3 "$identity" machine-version --contract "$source_dir/config/os/release.env" \
  --channel "$channel" --build-id "$build_id") || luma_os_die 'the release identity is invalid'
# The day this nightly is labelled for is fixed when the build starts, so the
# image's own name, the download index and the release notes agree: the build
# id's day, or the Central day the build started on when that is earlier. A
# rerun that replaces an earlier day's failed nightly passes LUMA_NIGHTLY_DATE.
nightly_date=${LUMA_NIGHTLY_DATE:-$(python3 "$identity" nightly-date --build-id "$build_id" --built "$started_utc")} ||
  luma_os_die 'cannot tell the nightly date'
[[ "$nightly_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || luma_os_die "invalid nightly date: $nightly_date"
display_name=$(python3 "$identity" display-name --contract "$source_dir/config/os/release.env" \
  --channel "$channel" --nightly-date "$nightly_date") || luma_os_die 'the release identity is invalid'
build_log="$build_dir/logs/image-build.log"
luma_os_log "build $build_id ($version, $display_name) from $source_revision dirty=$dirty"

# Base image.
case "$base_policy" in
  pinned)
    base_tag=$LUMA_OS_BASE_IMAGE_TAG
    base_digest=$LUMA_OS_BASE_IMAGE_DIGEST
    ;;
  latest)
    read -r base_tag base_digest < <("$luma_os_repo_root/scripts/os/resolve-base-image.sh")
    ;;
  *) luma_os_die "unknown base policy: $base_policy" ;;
esac
[[ "$base_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || luma_os_die "invalid base image digest: $base_digest"
base_ref="$LUMA_OS_BASE_IMAGE_REPOSITORY@$base_digest"
if ! luma_os_podman image exists "$base_ref"; then
  luma_os_log "pulling $base_ref ($base_tag)"
  luma_os_podman pull --quiet "$base_ref" >>"$build_log" 2>&1
fi
base_labels=$(luma_os_podman image inspect --format '{{json .Labels}}' "$base_ref")

# Package repository from the verified pool.
pool="$LUMA_OS_ROOT/rpms/pool"
repo="$build_dir/packages"
install -d -m 0755 "$repo/Packages"
mapfile -t pins < <(sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$source_dir/config/desktop/packages.txt")
# Exact Fedora NEVRAs are verified pool inputs too; unpinned names remain
# resolved by DNF inside the image build.
mapfile -t -O "${#pins[@]}" pins < <(sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$source_dir/config/os/fedora-packages.txt" | grep -E '\.(x86_64|aarch64|noarch)$' || true)
: >"$build_dir/luma-packages.manifest"
for nevra in "${pins[@]}"; do
  line=$(awk -v n="$nevra" '$1 == n' "$pool/pool.manifest" 2>/dev/null || true)
  [ -n "$line" ] && [ -f "$pool/$nevra.rpm" ] ||
    luma_os_die "pinned package is not in the verified pool: $nevra (run scripts/os/collect-packages.sh)"
  read -r _ sha header <<<"$line"
  [ "$(luma_os_sha256 "$pool/$nevra.rpm")" = "$sha" ] || luma_os_die "pool file digest changed: $nevra"
  ln "$pool/$nevra.rpm" "$repo/Packages/$nevra.rpm" 2>/dev/null || cp "$pool/$nevra.rpm" "$repo/Packages/$nevra.rpm"
  printf '%s %s %s\n' "$nevra" "$sha" "$header" >>"$build_dir/luma-packages.manifest"
done
LC_ALL=C sort -o "$build_dir/luma-packages.manifest" "$build_dir/luma-packages.manifest"
LUMA_OS_TOOLS_MOUNTS="$repo" luma_os_tools createrepo_c --quiet --no-database "$repo" >>"$build_log" 2>&1
[ -f "$repo/repodata/repomd.xml" ] || luma_os_die 'package repository metadata was not generated'

image_tag="$LUMA_OS_IMAGE_NAME:$build_id"
cache_args=(--no-cache)
[ "$use_cache" -eq 1 ] && cache_args=(--layers)
build_date=$(date -u +%Y-%m-%dT%H:%M:%SZ)
luma_os_log "podman build $image_tag (log: $build_log)"
build_started=$SECONDS
if ! luma_os_podman build \
    --security-opt label=disable \
    --cap-add=SYS_ADMIN \
    --pull=never \
    "${cache_args[@]}" \
    --build-context "source=$source_dir" \
    --build-context "packages=$repo" \
    --build-context "app-baseline=$app_baseline" \
    --build-arg "LUMA_APP_BASELINE_SHA256=$app_baseline_sha" \
    --build-arg "BASE_IMAGE=$base_ref" \
    --build-arg "LUMA_BUILD_ID=$build_id" \
    --build-arg "LUMA_OS_VERSION=$version" \
    --build-arg "LUMA_OS_CHANNEL=$channel" \
    --build-arg "LUMA_NIGHTLY_DATE=$nightly_date" \
    --build-arg "LUMA_BUILD_DATE=$build_date" \
    --build-arg "LUMA_SOURCE_REVISION=$source_revision" \
    --build-arg "LUMA_SOURCE_DIRTY=$dirty" \
    --build-arg "LUMA_SOURCE_SNAPSHOT_SHA256=$source_snapshot_sha" \
    --build-arg "LUMA_RELEASE_CHECKS_SHA256=$release_checks_sha" \
    --file "$source_dir/image/luma-desktop/Containerfile" \
    --tag "$image_tag" \
    "$source_dir/image/luma-desktop" >>"$build_log" 2>&1; then
  tail -n 40 "$build_log" >&2
  luma_os_die "image build failed; log: $build_log"
fi
build_seconds=$((SECONDS - build_started))
if [ -n "$source_snapshot_sha" ]; then
  python3 "$source_dir/scripts/os/lib/source_snapshot.py" validate \
    --root "$source_dir" --manifest "$build_dir/source-checks/SOURCE-SNAPSHOT.json" \
    --revision "$source_revision" --expected-sha "$source_snapshot_sha" >&2 || \
    luma_os_die 'private source input changed during composition'
fi
app_baseline_post=$(python3 "$source_dir/scripts/os/lib/app_baseline.py" "$app_baseline" \
  --shipping "$source_dir/config/os/first-party-app-baseline.json") || \
  luma_os_die 'the offline application input changed during composition'
[ "$app_baseline_post" = "$app_baseline_admission" ] || luma_os_die 'application baseline identity changed during composition'
printf '%s\n' "$app_baseline_admission" >"$build_dir/app-baseline-input.json"

image_id=$(luma_os_podman image inspect --format '{{.Id}}' "$image_tag")
image_size=$(luma_os_podman image inspect --format '{{.Size}}' "$image_tag")

# Inventory from the image itself.
luma_os_podman run --rm --net=none --security-opt label=disable "$image_tag" \
  rpm -qa --qf '%{NAME}\t%{EPOCHNUM}\t%{VERSION}\t%{RELEASE}\t%{ARCH}\t%{SHA256HEADER}\t%{SOURCERPM}\t%{LICENSE}\n' |
  LC_ALL=C sort >"$build_dir/packages-installed.tsv"
luma_os_podman run --rm --net=none --security-opt label=disable "$image_tag" \
  cat /usr/lib/os-release >"$build_dir/os-release"

# Every icon name first-party code uses must exist in the image: a missing one
# draws GTK's broken-image placeholder. The recipe's own checker reads the
# image's themes and resources and the recipe's sources and patches.
if ! luma_os_podman run --rm --net=none --security-opt label=disable \
    -v "$source_dir:/luma-source:ro" "$image_tag" \
    python3 /luma-source/scripts/os/lib/icon_references.py --root / \
      --source /luma-source/src --patches /luma-source/patches --installed \
      --allowlist /luma-source/config/os/icon-reference-allowlist.txt \
      >"$build_dir/icon-references.log" 2>&1; then
  grep '^FAIL' "$build_dir/icon-references.log" | head -n 20 >&2
  luma_os_die "first-party code names icons nothing in the image provides; log: $build_dir/icon-references.log"
fi

# OCI layout for the SBOM and the OSTree export.
rm -rf "$build_dir/oci"
luma_os_podman push --quiet --format oci "$image_tag" "oci:$build_dir/oci:luma" >>"$build_log" 2>&1
"$luma_os_repo_root/scripts/os/generate-sbom.sh" --build-dir "$build_dir" >>"$build_log" 2>&1 ||
  luma_os_die "SBOM generation failed; log: $build_log"

completed_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
recipe_digest=$(cd "$source_dir" && {
  find image/luma-desktop config/os -type f -print0
  printf '%s\0' config/desktop/packages.txt config/shared/application-packages.txt \
    config/desktop/application-packages.txt config/shared/excluded-background-packages.txt \
    config/desktop/dconf/profile/user config/desktop/dconf/db/luma.d/00-luma-desktop \
    config/desktop/dconf/db/gdm.d/00-prairie-login config/desktop/useradd \
    config/boot/plymouthd.conf config/desktop/fprintd-no-idle-exit.conf \
    config/desktop/waydroid-container-ordering.conf scripts/os/lib/os_release.py scripts/os/lib/release_identity.py \
    scripts/os/lib/app_baseline.py
} | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | awk '{ print $1 }')

python3 "$luma_os_repo_root/scripts/os/lib/provenance.py" build \
  --output "$build_dir/provenance.json" \
  --build-id "$build_id" --version "$version" --channel "$channel" \
  --source-revision "$source_revision" --source-dirty "$dirty" \
  --source-snapshot-sha256 "$source_snapshot_sha" --release-checks-sha256 "$release_checks_sha" \
  --recipe-digest "$recipe_digest" \
  --base-repository "$LUMA_OS_BASE_IMAGE_REPOSITORY" --base-tag "$base_tag" \
  --base-digest "$base_digest" --base-labels "$base_labels" \
  --luma-packages "$build_dir/luma-packages.manifest" \
  --installed-packages "$build_dir/packages-installed.tsv" \
  --image-id "$image_id" --image-size "$image_size" \
  --build-log "$build_log" --sbom "$build_dir/sbom.spdx.json" \
  --started "$started_utc" --completed "$completed_utc" \
  --build-seconds "$build_seconds" \
  --app-baseline-input "$build_dir/app-baseline-input.json" \
  --podman-version "$(podman --version | awk '{ print $3 }')"

cat >"$build_dir/build.env" <<EOF
LUMA_BUILD_ID=$build_id
LUMA_OS_VERSION=$version
LUMA_OS_CHANNEL=$channel
LUMA_NIGHTLY_DATE=$nightly_date
LUMA_DISPLAY_NAME=$display_name
LUMA_SOURCE_REVISION=$source_revision
LUMA_SOURCE_DIRTY=$dirty
LUMA_SOURCE_SNAPSHOT_SHA256=$source_snapshot_sha
LUMA_RELEASE_CHECKS_SHA256=$release_checks_sha
LUMA_IMAGE_TAG=$image_tag
LUMA_IMAGE_ID=$image_id
LUMA_BASE_IMAGE=$base_ref
LUMA_BUILD_STARTED=$started_utc
LUMA_BUILD_COMPLETED=$completed_utc
EOF
luma_os_log "built $image_tag ($version) in ${build_seconds}s"
printf '%s\n' "$build_dir"
