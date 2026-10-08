#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Stage the exact source commits the app manifests name onto the build host.
#
#   DEPOT_BUILD_SSH="ssh -p PORT user@build-host" \
#     prepare-sources.sh NAME=REPO_PATH [NAME=REPO_PATH ...]
#
# For each app repository, a git bundle of every branch containing the pinned
# commit becomes a bare mirror at $DEPOT_ROOT/sources/mirrors/NAME.git, which
# build-app.sh uses in place of the network URL (the commit id stays the one in
# the manifest, so a mirror cannot substitute other code). NAME must match the
# last path component of the manifest's git URL (Write, Grid, Designer, ...).
#
# NAME=Luma is special: the operating-system repository is large, so only the
# paths an app needs (src/luma-reel and LICENSE.md) are sent, as
# sources/archives/Luma-<commit>.tar.gz; build-app.sh then pins its sha256.
#
# Without DEPOT_BUILD_SSH the files are written under $DEPOT_ROOT/sources
# locally.
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"

run_remote() {
  if [ -n "${DEPOT_BUILD_SSH:-}" ]; then $DEPOT_BUILD_SSH "$@"; else bash -c "$*"; fi
}
copy_remote() {  # copy_remote LOCAL REMOTE_PATH
  if [ -n "${DEPOT_BUILD_SSH:-}" ]; then $DEPOT_BUILD_SSH "cat > '$2'" <"$1"; else cp "$1" "$2"; fi
}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
run_remote "install -d '$DEPOT_ROOT/sources/mirrors' '$DEPOT_ROOT/sources/archives' '$DEPOT_ROOT/sources/incoming'"

for pair in "$@"; do
  name=${pair%%=*}
  path=${pair#*=}
  manifest=$(grep -rl "url: https://github.com/ProjectLuma/$name.git" "$depot_repo_root/packaging/flatpak/apps" | head -n 1)
  [ -n "$manifest" ] || depot_die "no manifest uses ProjectLuma/$name"
  commit=$(awk -v u="https://github.com/ProjectLuma/$name.git" \
    '$0 ~ "url: " u {found=1; next} found && /commit:/ {print $2; exit}' "$manifest")
  git -C "$path" cat-file -e "$commit^{commit}" || depot_die "$path lacks commit $commit"

  if [ "$name" = Luma ]; then
    archive="$tmp/Luma-$commit.tar.gz"
    git -C "$path" archive --format=tar --prefix=luma/ "$commit" src/luma-reel LICENSE.md | gzip -n >"$archive"
    copy_remote "$archive" "$DEPOT_ROOT/sources/archives/Luma-$commit.tar.gz"
    depot_log "Luma $commit: archive of src/luma-reel and LICENSE.md"
    continue
  fi

  bundle="$tmp/$name.bundle"
  git -C "$path" bundle create "$bundle" --branches --tags >/dev/null 2>&1
  copy_remote "$bundle" "$DEPOT_ROOT/sources/incoming/$name.bundle"
  run_remote "set -e; m='$DEPOT_ROOT/sources/mirrors/$name.git';
    [ -d \"\$m\" ] || git init -q --bare \"\$m\";
    git -C \"\$m\" fetch -q '$DEPOT_ROOT/sources/incoming/$name.bundle' '+refs/heads/*:refs/heads/*';
    git -C \"\$m\" cat-file -e '$commit^{commit}';
    rm -f '$DEPOT_ROOT/sources/incoming/$name.bundle'"
  depot_log "$name $commit: mirror updated"
done
