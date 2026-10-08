# SPDX-License-Identifier: Apache-2.0
# Shared helpers for the Luma OS release pipeline. Sourced, never executed.
#
# Everything the pipeline writes on a build host lives below LUMA_OS_ROOT, a
# dedicated volume. Private signing material lives below LUMA_OS_KEYS and its
# passphrases below LUMA_OS_SECRETS; neither is ever copied anywhere else.

shopt -s inherit_errexit

luma_os_repo_root=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)

: "${LUMA_OS_ROOT:=/mnt/luma-secondary/luma-build/os-release/fs}"
: "${LUMA_OS_KEYS:=$LUMA_OS_ROOT/keys}"
: "${LUMA_OS_SECRETS:=/root/luma-os-release-secrets}"
: "${LUMA_OS_TOOLS_IMAGE_NAME:=localhost/luma-os/tools}"
: "${LUMA_OS_IMAGE_NAME:=localhost/luma-os/desktop}"
# The build host's other Luma work refuses to run while this VM is in an
# unexpected state; the OS pipeline follows the same protection.
: "${LUMA_OS_PROTECTED_VM:=viola-windows-builder}"

luma_os_log() {
  printf '%s os-release: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

luma_os_die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

# Read KEY=value lines without evaluating them (values may contain spaces).
luma_os_load_env() {
  local file=$1 key value
  [ -r "$file" ] || luma_os_die "contract file is missing: $file"
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    key=${line%%=*}
    value=${line#*=}
    [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || luma_os_die "invalid key in $file: $key"
    printf -v "$key" '%s' "$value"
    export "${key?}"
  done <"$file"
}

luma_os_load_env "$luma_os_repo_root/config/os/release.env"

luma_os_require_root() {
  [ "$(id -u)" -eq 0 ] || luma_os_die "$(basename "$0") must run as root on the build host"
}

luma_os_require_tools() {
  local tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || luma_os_die "required tool is missing: $tool"
  done
}

luma_os_channel_ref() {
  local channel=$1
  case " $LUMA_OS_CHANNELS " in
    *" $channel "*) printf '%s/%s\n' "$LUMA_OS_REF_PREFIX" "$channel" ;;
    *) luma_os_die "unknown channel: $channel" ;;
  esac
}

luma_os_channel_is_preview() {
  case " $LUMA_OS_PREVIEW_CHANNELS " in *" $1 "*) return 0 ;; esac
  return 1
}

# The archive repository a channel's ref lives in. The contract keeps
# legacy preview support, while current Beta and Nightly are public too.
luma_os_channel_repo() {
  if luma_os_channel_is_preview "$1"; then
    printf '%s\n' "$LUMA_OS_ROOT/publish/preview-repo"
  else
    printf '%s\n' "$LUMA_OS_ROOT/publish/public-repo"
  fi
}

# Every container the pipeline starts lives in luma-os.slice, whose memory and
# CPU caps (set by luma_os_ensure_slice) also bound the scripts that run-capped
# starts there. A podman container would otherwise escape the caps of the unit
# that started it: podman places containers in their own scopes.
: "${LUMA_OS_SLICE:=luma-os.slice}"
luma_os_podman() {
  local command=${1:-}
  case "$command" in
    run|build|create)
      shift
      CONTAINERS_STORAGE_CONF="$LUMA_OS_ROOT/etc/storage.conf" TMPDIR="$LUMA_OS_ROOT/tmp" \
        podman "$command" --cgroup-parent="$LUMA_OS_SLICE" "$@"
      ;;
    *)
      CONTAINERS_STORAGE_CONF="$LUMA_OS_ROOT/etc/storage.conf" TMPDIR="$LUMA_OS_ROOT/tmp" \
        podman "$@"
      ;;
  esac
}

luma_os_ensure_slice() {
  systemctl set-property --runtime "$LUMA_OS_SLICE" \
    CPUQuota=800% CPUWeight=20 MemoryHigh=14G MemoryMax=18G MemorySwapMax=2G \
    IOWeight=20 TasksMax=8192 >/dev/null
}

# Refuse to start while the protected VM is in an unexpected state, and report
# how much of the host is ours to use. Mirrors /srv/luma-build/bin/luma-build-run.
luma_os_check_host() {
  local state
  if command -v virsh >/dev/null 2>&1; then
    state=$(virsh domstate "$LUMA_OS_PROTECTED_VM" 2>/dev/null | tr -d '\r' || true)
    case "$state" in
      running|shut\ off|'') ;;
      *) luma_os_die "protected VM $LUMA_OS_PROTECTED_VM is $state; refusing OS pipeline work" ;;
    esac
  fi
  # Transaction tests run against a throwaway root and set
  # LUMA_OS_REQUIRE_VOLUME=0; production always requires the dedicated volume.
  if [ "${LUMA_OS_REQUIRE_VOLUME:-1}" != 0 ]; then
    mountpoint -q "$LUMA_OS_ROOT" || luma_os_die "OS release volume is not mounted: $LUMA_OS_ROOT"
  fi
}

# Free-space guard: both the dedicated volume and the disk that carries its
# sparse image file must keep head room, or the job refuses before writing.
luma_os_check_space() {
  local need_gib=$1 volume_free host_free image_dir
  volume_free=$(df --output=avail -B1G "$LUMA_OS_ROOT" | tail -n 1 | tr -d ' ')
  image_dir=$LUMA_OS_ROOT
  local source backing
  source=$(findmnt -rn -o SOURCE --target "$LUMA_OS_ROOT" || true)
  case "$source" in
    /dev/loop*)
      backing=$(losetup -nO BACK-FILE "$source" 2>/dev/null | head -n 1 | xargs || true)
      [ -n "$backing" ] && image_dir=$(dirname "$backing")
      ;;
  esac
  host_free=$(df --output=avail -B1G "$image_dir" | tail -n 1 | tr -d ' ')
  [ "${volume_free:-0}" -ge "$need_gib" ] ||
    luma_os_die "OS release volume has ${volume_free} GiB free; this step needs $need_gib GiB"
  # A sparse volume image takes new space on the disk that carries it only for
  # blocks it has not allocated yet. Blocks it holds but the volume does not use
  # (freed and not trimmed) are reused first, so the disk is asked only for
  # the rest, and never for more than the image can still grow.
  local host_need=$need_gib
  if [ -n "${backing:-}" ] && [ -f "$backing" ]; then
    local size_gib allocated_gib used_gib held_free
    size_gib=$(( $(stat -c %s "$backing") / 1073741824 ))
    allocated_gib=$(( $(stat -c %b "$backing") * $(stat -c %B "$backing") / 1073741824 ))
    used_gib=$(df --output=used -B1G "$LUMA_OS_ROOT" | tail -n 1 | tr -d ' ')
    held_free=$((allocated_gib - used_gib))
    [ "$held_free" -ge 0 ] || held_free=0
    host_need=$((need_gib - held_free))
    [ "$host_need" -ge 0 ] || host_need=0
    [ $((size_gib - allocated_gib)) -ge "$host_need" ] || host_need=$((size_gib - allocated_gib))
  fi
  # The disk is shared: a run that would leave it with less than the reserve
  # (LUMA_OS_HOST_RESERVE_GIB, default 10) does not start, because a full
  # host filesystem takes every agent's work down with it.
  local reserve=${LUMA_OS_HOST_RESERVE_GIB:-10}
  [ "${host_free:-0}" -ge $((host_need + reserve)) ] ||
    luma_os_die "disk carrying the OS release volume has ${host_free} GiB free; this step may grow the volume image by $host_need GiB and must leave $reserve GiB"
}

# luma_os_release_files REVISION DIR: the files a release is checked against
# (its image contract, desktop smoke test, package pins, release contract and
# installer boot policy), exactly as committed at the revision the image was
# built from. The pipeline's own scripts may be newer (a resumed night, media
# verified later), but a release is always judged by its own tests.
luma_os_release_files() {
  local revision=$1 dir=$2 build_dir=${3:-}
  if [ -n "$build_dir" ] && [ "${LUMA_SOURCE_DIRTY:-false}" = true ]; then
    [ -n "${LUMA_SOURCE_SNAPSHOT_SHA256:-}" ] && [ -n "${LUMA_RELEASE_CHECKS_SHA256:-}" ] || \
      luma_os_die 'dirty source has no privately bound release checks'
    local admission private_checks
    admission=$(python3 "$luma_os_repo_root/scripts/os/lib/source_snapshot.py" admit \
      --bundle "$build_dir/source-checks" --revision "$revision" \
      --snapshot-sha "$LUMA_SOURCE_SNAPSHOT_SHA256" --checks-sha "$LUMA_RELEASE_CHECKS_SHA256" \
      --provenance "$build_dir/provenance.json") || luma_os_die 'private release checks do not match image provenance'
    private_checks=$(printf '%s' "$admission" | python3 -c 'import json,sys; print(json.load(sys.stdin)["release_checks_root"])')
    [ ! -e "$dir" ] || luma_os_die 'private release-check output already exists'
    install -d -m 0755 "$dir"
    cp -a -- "$private_checks/." "$dir/"
    return
  fi
  [ -z "${LUMA_SOURCE_SNAPSHOT_SHA256:-}" ] && [ -z "${LUMA_RELEASE_CHECKS_SHA256:-}" ] || luma_os_die 'private snapshot cannot use the clean Git release-check path'
  [ -n "$revision" ] && [ "$revision" != unknown ] || luma_os_die 'no source revision to take the release checks from'
  git -c "safe.directory=$luma_os_repo_root" -C "$luma_os_repo_root" cat-file -e "$revision^{commit}" 2>/dev/null ||
    git -c "safe.directory=$luma_os_repo_root" -C "$luma_os_repo_root" fetch --quiet origin "$revision" 2>/dev/null ||
    luma_os_die "source revision $revision is not in $luma_os_repo_root"
  rm -rf "$dir"
  install -d -m 0755 "$dir"
  git -c "safe.directory=$luma_os_repo_root" -C "$luma_os_repo_root" archive --format=tar "$revision" -- \
    tests/os/image-contract.sh tests/smoke/desktop.sh config/desktop/inputs.env \
    config/os/release.env config/boot/apply-grub-policy.sh tests/os/gate | tar -xf - -C "$dir"
}

# One VM job at a time. Gates, media verification and migration tests each
# need tens of GiB of disk images at their peak; two at once filled the volume
# and paused both VMs on I/O errors. luma_os_vm_lock [WAIT_SECONDS] waits for
# the lock (default four hours) and holds it until the calling script exits.
luma_os_vm_lock() {
  local wait=${1:-14400}
  install -d -m 0755 "$LUMA_OS_ROOT/locks"
  exec 9>"$LUMA_OS_ROOT/locks/vm-jobs.lock"
  if ! flock -n 9; then
    luma_os_log "waiting for the running VM job to finish (up to ${wait}s)"
    flock -w "$wait" 9 || luma_os_die 'another VM job still holds the VM lock'
  fi
}

luma_os_tools_image() {
  local digest tag fedora_image
  digest=$(sha256sum "$luma_os_repo_root/image/os-tools/Containerfile" | cut -c1-16)
  tag="$LUMA_OS_TOOLS_IMAGE_NAME:$digest"
  if ! luma_os_podman image exists "$tag"; then
    fedora_image=$(awk -F= '$1 == "FEDORA_RPM_BUILD_CONTAINER" { print $2 }' \
      "$luma_os_repo_root/config/desktop/inputs.env")
    [ -n "$fedora_image" ] || luma_os_die 'FEDORA_RPM_BUILD_CONTAINER is not pinned'
    luma_os_log "building tools image $tag"
    luma_os_podman build --security-opt label=disable \
      --build-arg "FEDORA_IMAGE=$fedora_image" \
      -t "$tag" -f "$luma_os_repo_root/image/os-tools/Containerfile" \
      "$luma_os_repo_root/image/os-tools" >&2
  fi
  printf '%s\n' "$tag"
}

# Run a command in the tools container with the given host directories mounted
# at the same paths. The container has no network unless LUMA_OS_TOOLS_NETWORK=1.
luma_os_tools() {
  local image network=none mounts=() dir
  image=$(luma_os_tools_image)
  [ "${LUMA_OS_TOOLS_NETWORK:-0}" = 1 ] && network=host
  for dir in ${LUMA_OS_TOOLS_MOUNTS:-}; do
    mounts+=(--volume "$dir:$dir")
  done
  # Secrets reach the container as environment from a root-only file, never
  # as command-line arguments.
  [ -z "${LUMA_OS_TOOLS_ENV_FILE:-}" ] || mounts+=(--env-file "$LUMA_OS_TOOLS_ENV_FILE")
  luma_os_podman run --rm --interactive --security-opt label=disable \
    --network "$network" "${mounts[@]}" \
    --env LANG=C.UTF-8 "$image" "$@"
}

# GnuPG home and fingerprint of the Luma OS Release key.
luma_os_gpg_home() { printf '%s\n' "$LUMA_OS_KEYS/os-release-gnupg"; }

luma_os_gpg_fingerprint() {
  local file="$LUMA_OS_KEYS/os-release-fingerprint.txt"
  [ -s "$file" ] || luma_os_die 'no Luma OS Release key; run scripts/os/generate-release-keys.sh'
  tr -d ' \n' <"$file"
}

# Load the release key's passphrase into its agent for this job, and forget it
# again on exit. The passphrase file is root-only and never leaves the host.
luma_os_gpg_unlock() {
  local home fingerprint keygrip passphrase_file
  home=$(luma_os_gpg_home)
  fingerprint=$(luma_os_gpg_fingerprint)
  passphrase_file="$LUMA_OS_SECRETS/os-release-gpg.passphrase"
  [ -r "$passphrase_file" ] || luma_os_die "release key passphrase is unavailable: $passphrase_file"
  keygrip=$(gpg --batch --homedir "$home" --with-colons --with-keygrip \
    --list-secret-keys "$fingerprint" | awk -F: '$1 == "grp" { print $10; exit }')
  [ -n "$keygrip" ] || luma_os_die 'release key has no secret key material on this host'
  grep -qx 'allow-preset-passphrase' "$home/gpg-agent.conf" 2>/dev/null ||
    luma_os_die "release keyring does not allow a preset passphrase: $home/gpg-agent.conf"
  GNUPGHOME="$home" gpg-connect-agent /bye >/dev/null 2>&1
  GNUPGHOME="$home" /usr/libexec/gpg-preset-passphrase --preset "$keygrip" \
    <"$passphrase_file" || luma_os_die 'could not unlock the release key'
  LUMA_OS_GPG_KEYGRIP=$keygrip
}

luma_os_gpg_lock() {
  local home
  home=$(luma_os_gpg_home)
  if [ -n "${LUMA_OS_GPG_KEYGRIP:-}" ]; then
    GNUPGHOME="$home" /usr/libexec/gpg-preset-passphrase --forget \
      "$LUMA_OS_GPG_KEYGRIP" >/dev/null 2>&1 || true
  fi
  gpgconf --homedir "$home" --kill gpg-agent >/dev/null 2>&1 || true
}

# Tree creation can outlive the signing agent's preset. Finish it before
# unlocking, and return a commit only after its detached signature succeeds.
luma_os_commit_and_sign() {
  local repository=$1 commit
  shift
  commit=$(ostree commit --repo="$repository" "$@") || return $?
  [[ "$commit" =~ ^[0-9a-f]{64}$ ]] || return 1
  luma_os_gpg_unlock || return $?
  ostree gpg-sign --repo="$repository" --gpg-homedir="$(luma_os_gpg_home)" \
    "$commit" "$(luma_os_gpg_fingerprint)" >&2 || return $?
  printf '%s\n' "$commit"
}

luma_os_sha256() { sha256sum "$1" | awk '{ print $1 }'; }

luma_os_json_string() {
  python3 -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$1"
}
