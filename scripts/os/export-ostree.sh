#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Turn a built image into a signed candidate commit for its channel.
#
#   export-ostree.sh --build-id YYYYMMDD.N
#
# 1. The image's OCI layout is imported with OSTree's container tooling (run
#    from the image itself, so the importer matches the tree) into a
#    bare-user staging repository. The importer writes one merged commit whose
#    tree carries the image's SELinux labels and the /usr/etc layout rpm-ostree
#    deploys.
# 2. A candidate commit is written into the private candidate archive
#    repository with that exact tree, bound to the channel ref and collection,
#    whose parent is the channel's current published head, with the build's
#    version and provenance metadata and the rpm package list rpm-ostree uses
#    for `db diff` and update previews.
# 3. The candidate commit is signed with the Luma OS Release key and the
#    candidate repository's ref and signed summary are updated so the VM gate
#    can install and update from it exactly as a device would.
#
# Nothing here touches a published repository. A candidate that fails its gate
# is deleted from the candidate repository by scripts/os/discard-candidate.sh.

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"

build_id=
case "${1:-}" in
  --build-id) build_id=${2:?} ;;
  *) printf 'usage: %s --build-id YYYYMMDD.N\n' "$0" >&2; exit 2 ;;
esac

luma_os_require_root
luma_os_require_tools ostree gpg python3 flock
luma_os_check_host
luma_os_check_space 20

build_dir="$LUMA_OS_ROOT/builds/$build_id"
[ -s "$build_dir/build.env" ] || luma_os_die "build is not complete: $build_dir"
luma_os_load_env "$build_dir/build.env"
channel=$LUMA_OS_CHANNEL
ref=$(luma_os_channel_ref "$channel")
if [ ! -d "$build_dir/oci" ]; then
  # Retention removes the layout once a build is exported; re-export (resume)
  # recreates it from the build's image, whose id the build recorded.
  luma_os_podman image exists "$LUMA_IMAGE_TAG" ||
    luma_os_die "build has neither an OCI layout nor its image: $LUMA_IMAGE_TAG"
  [ "$(luma_os_podman image inspect --format '{{.Id}}' "$LUMA_IMAGE_TAG")" = "$LUMA_IMAGE_ID" ] ||
    luma_os_die "image $LUMA_IMAGE_TAG is not the image this build recorded"
  luma_os_podman push --quiet --format oci "$LUMA_IMAGE_TAG" "oci:$build_dir/oci:luma" >>"$build_dir/logs/export.log" 2>&1
fi
log="$build_dir/logs/export.log"

install -d -m 0700 "$LUMA_OS_ROOT/locks"
exec 8>"$LUMA_OS_ROOT/locks/candidate-repo.lock"
flock -w 3600 8 || luma_os_die 'candidate repository is locked by another job'

import_repo="$LUMA_OS_ROOT/ostree/import"
candidate_repo="$LUMA_OS_ROOT/ostree/candidate-repo"
published_repo=$(luma_os_channel_repo "$channel")
install -d -m 0700 "$LUMA_OS_ROOT/ostree"
[ -f "$import_repo/config" ] || ostree init --repo="$import_repo" --mode=bare-user
if [ ! -f "$candidate_repo/config" ]; then
  ostree init --repo="$candidate_repo" --mode=archive --collection-id="$LUMA_OS_COLLECTION_ID"
fi
[ "$(ostree config --repo="$candidate_repo" get core.collection-id)" = "$LUMA_OS_COLLECTION_ID" ] ||
  luma_os_die 'candidate repository has the wrong collection id'

luma_os_log "importing $LUMA_IMAGE_TAG into OSTree"
# Container-side paths: /mnt in a bootable image is a link into /var.
image_ref="ostree-unverified-image:oci:/run/luma-import/oci:luma"
luma_os_podman run --rm --net=none --privileged --security-opt label=disable \
  --volume "$build_dir/oci:/run/luma-import/oci:ro" \
  --volume "$import_repo:/run/luma-import/repo" \
  --entrypoint /usr/bin/ostree \
  "$LUMA_IMAGE_TAG" container image pull /run/luma-import/repo "$image_ref" >"$build_dir/logs/import.out" 2>&1 || {
  cat "$build_dir/logs/import.out" >>"$log"
  luma_os_die "container import failed; log: $build_dir/logs/import.out"
}
cat "$build_dir/logs/import.out" >>"$log"
# The importer reports "Wrote: <image> => <merged commit>" (or "No changes in
# <image> => <merged commit>" when the same image was imported before).
merge=$(sed -n -E 's/^(Wrote:|No changes in) .* => ([0-9a-f]{64})$/\2/p' "$build_dir/logs/import.out" | tail -n 1)
[[ "$merge" =~ ^[0-9a-f]{64}$ ]] || luma_os_die "the importer reported no merged commit; log: $build_dir/logs/import.out"
ostree show --repo="$import_repo" "$merge" >/dev/null || luma_os_die "imported commit $merge is not in $import_repo"
luma_os_log "imported tree commit $merge"

# The tree must carry the rpm database and a kernel with its initramfs.
ostree ls --repo="$import_repo" "$merge" /usr/share/rpm/rpmdb.sqlite >/dev/null ||
  luma_os_die 'imported tree has no rpm database'
kver=$(luma_os_podman run --rm --net=none --security-opt label=disable "$LUMA_IMAGE_TAG" \
  ls /usr/lib/modules | head -n 1)
ostree ls --repo="$import_repo" "$merge" "/usr/lib/modules/$kver/vmlinuz" "/usr/lib/modules/$kver/initramfs.img" >/dev/null ||
  luma_os_die "imported tree has no bootable kernel for $kver"
ostree ls --repo="$import_repo" "$merge" /usr/etc/os-release >/dev/null 2>&1 ||
  ostree ls --repo="$import_repo" "$merge" /usr/etc/ostree/remotes.d/luma.conf >/dev/null ||
  luma_os_die 'imported tree does not carry /usr/etc'

# File modes the import can change. The bare-user staging repository stores a
# file its owner cannot read with owner-read added and set-user-ID and
# set-group-ID dropped (sudo 4111 became 0511; shadow 0000 became 0400). Every
# path in the image with a special bit or without owner-read is listed with
# its image mode; differences are restored from the image by overlaying a tar
# of exactly those paths (the tree's content, ownership and xattrs, the
# image's mode), and the result is verified path by path.
modes="$build_dir/image-modes.txt"
luma_os_podman run --rm --net=none --security-opt label=disable --entrypoint /usr/bin/find "$LUMA_IMAGE_TAG" \
  / -xdev \( -path /proc -o -path /sys -o -path /dev -o -path /run -o -path /tmp -o -path /var \
  -o -path /sysroot -o -path /boot \) -prune -o \( -perm /7000 -o ! -perm -u=r \) \
  \( -type f -o -type d \) -printf '%m %p\n' | LC_ALL=C sort >"$modes"
grep -q ' /usr/bin/sudo$' "$modes" || luma_os_die "the image mode list is incomplete (no /usr/bin/sudo): $modes"
repair_tar="$build_dir/mode-repair.tar"
python3 "$luma_os_repo_root/scripts/os/lib/repair_modes.py" tar "$import_repo" "$merge" "$modes" "$repair_tar" \
  >"$build_dir/mode-repair.txt" || luma_os_die 'could not prepare the file mode repair'
tree_args=(--tree="ref=$merge")
if [ -s "$build_dir/mode-repair.txt" ]; then
  tree_args+=(--tree="tar=$repair_tar")
  luma_os_log "restoring image modes the import changed: $(awk '{ printf "%s %s->%s; ", $1, $2, $3 }' "$build_dir/mode-repair.txt")"
fi

luma_os_log 'copying the tree into the candidate repository'
ostree pull-local --repo="$candidate_repo" --untrusted "$import_repo" "$merge" >>"$log" 2>&1

# Channel history: the parent is the channel's published head. Its commit
# object (not its content) is copied so history and deltas can refer to it.
parent=
if [ -f "$published_repo/config" ]; then
  parent=$(ostree rev-parse --repo="$published_repo" "$ref" 2>/dev/null || true)
fi
if [ -n "$parent" ]; then
  ostree pull-local --repo="$candidate_repo" --commit-metadata-only --untrusted \
    "$published_repo" "$parent" >>"$log" 2>&1
fi

# rpm-ostree's package list metadata, from the build's own inventory.
pkglist=$(python3 "$luma_os_repo_root/scripts/os/lib/ostree_metadata.py" pkglist \
  "$build_dir/packages-installed.tsv")
bootable=$(ostree show --repo="$import_repo" --print-metadata-key=ostree.bootable "$merge" 2>/dev/null || echo true)
linux=$(ostree show --repo="$import_repo" --print-metadata-key=ostree.linux "$merge" 2>/dev/null || printf "'%s'" "$kver")
provenance_sha=$(luma_os_sha256 "$build_dir/provenance.json")
sbom_sha=$(luma_os_sha256 "$build_dir/sbom.spdx.json")
base_digest=${LUMA_BASE_IMAGE#*@}
timestamp=$LUMA_BUILD_COMPLETED

metadata_file="$build_dir/commit-metadata.args"
{
  printf -- '--add-metadata-string=version=%s\n' "$LUMA_OS_VERSION"
  printf -- '--add-metadata=ostree.bootable=%s\n' "$bootable"
  printf -- '--add-metadata=ostree.linux=%s\n' "$linux"
  printf -- '--add-metadata-string=org.projectluma.build-id=%s\n' "$build_id"
  printf -- '--add-metadata-string=org.projectluma.build-version=%s\n' "$LUMA_OS_VERSION"
  printf -- '--add-metadata-string=org.projectluma.channel=%s\n' "$channel"
  # The release's name as people see it (os-release PRETTY_NAME, ADR-040).
  [ -z "${LUMA_DISPLAY_NAME:-}" ] ||
    printf -- '--add-metadata-string=org.projectluma.display-name=%s\n' "$LUMA_DISPLAY_NAME"
  printf -- '--add-metadata-string=org.projectluma.source-revision=%s\n' "$LUMA_SOURCE_REVISION"
  printf -- '--add-metadata-string=org.projectluma.source-dirty=%s\n' "$LUMA_SOURCE_DIRTY"
  [ -z "${LUMA_SOURCE_SNAPSHOT_SHA256:-}" ] || printf -- '--add-metadata-string=org.projectluma.source-snapshot-sha256=%s\n' "$LUMA_SOURCE_SNAPSHOT_SHA256"
  [ -z "${LUMA_RELEASE_CHECKS_SHA256:-}" ] || printf -- '--add-metadata-string=org.projectluma.release-checks-sha256=%s\n' "$LUMA_RELEASE_CHECKS_SHA256"
  printf -- '--add-metadata-string=org.projectluma.base-image-digest=%s\n' "$base_digest"
  printf -- '--add-metadata-string=org.projectluma.provenance-sha256=%s\n' "$provenance_sha"
  printf -- '--add-metadata-string=org.projectluma.sbom-sha256=%s\n' "$sbom_sha"
  printf -- '--add-metadata-string=org.projectluma.image-tree=%s\n' "$merge"
  printf -- '--add-metadata=rpmostree.rpmdb.pkglist=%s\n' "$pkglist"
} >"$metadata_file"
mapfile -t metadata_args <"$metadata_file"
parent_args=(--orphan)
[ -n "$parent" ] && parent_args=(--orphan --parent="$parent")

candidate=$(ostree commit --repo="$candidate_repo" \
  "${parent_args[@]}" \
  "${tree_args[@]}" \
  --bind-ref="$ref" \
  --timestamp="$timestamp" \
  --subject="${LUMA_DISPLAY_NAME:-Luma} $LUMA_OS_VERSION" \
  --body="Build $build_id from source $LUMA_SOURCE_REVISION" \
  "${metadata_args[@]}")
[[ "$candidate" =~ ^[0-9a-f]{64}$ ]] || luma_os_die 'candidate commit failed'

if [ -s "$build_dir/mode-repair.txt" ]; then
  # Exactly the repaired paths differ, and only in mode.
  diff <(ostree diff --repo="$candidate_repo" "$merge" "$candidate" | awk '{ print $1, $2 }' | LC_ALL=C sort) \
       <(awk '{ print "M", $1 }' "$build_dir/mode-repair.txt" | LC_ALL=C sort) >>"$log" ||
    luma_os_die 'candidate commit differs from the imported tree beyond the mode repair'
  python3 "$luma_os_repo_root/scripts/os/lib/repair_modes.py" verify "$candidate_repo" "$merge" "$candidate" "$modes" >>"$log" ||
    luma_os_die 'mode repair changed content, ownership or xattrs'
else
  python3 "$luma_os_repo_root/scripts/os/lib/ostree_metadata.py" same-tree \
    "$candidate_repo" "$merge" "$candidate" ||
    luma_os_die 'candidate commit does not carry the imported tree exactly'
fi
python3 "$luma_os_repo_root/scripts/os/lib/repair_modes.py" check "$candidate_repo" "$candidate" "$modes" >>"$log" ||
  luma_os_die "candidate commit has file modes that differ from the image (see $log)"

luma_os_gpg_unlock
trap luma_os_gpg_lock EXIT
ostree gpg-sign --repo="$candidate_repo" --gpg-homedir="$(luma_os_gpg_home)" \
  "$candidate" "$(luma_os_gpg_fingerprint)"
ostree refs --repo="$candidate_repo" --create="$ref" --force "$candidate" 2>/dev/null ||
  ostree reset --repo="$candidate_repo" "$ref" "$candidate"
[ "$(ostree rev-parse --repo="$candidate_repo" "$ref")" = "$candidate" ] ||
  luma_os_die 'candidate ref did not move'
ostree summary --repo="$candidate_repo" --update \
  --gpg-sign="$(luma_os_gpg_fingerprint)" --gpg-homedir="$(luma_os_gpg_home)"
luma_os_gpg_lock
trap - EXIT

# Release the import staging content; the candidate repository owns it now.
ostree refs --repo="$import_repo" --delete ostree/container >/dev/null 2>&1 || true
ostree prune --repo="$import_repo" --refs-only >>"$log" 2>&1 || true

root_dirtree=$(python3 "$luma_os_repo_root/scripts/os/lib/ostree_metadata.py" root-dirtree "$candidate_repo" "$candidate")
cat >"$build_dir/export.env" <<EOF
LUMA_EXPORT_REF=$ref
LUMA_EXPORT_TREE_COMMIT=$merge
LUMA_EXPORT_CANDIDATE=$candidate
LUMA_EXPORT_PARENT=$parent
LUMA_EXPORT_ROOT_DIRTREE=$root_dirtree
LUMA_EXPORT_KERNEL=$kver
LUMA_EXPORT_COMPLETED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
luma_os_log "candidate $candidate on $ref (parent ${parent:-none})"
printf '%s\n' "$candidate"
