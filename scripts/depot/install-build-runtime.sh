#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Install the published Luma Platform and SDK, and the Flathub linter, into the
# tools container's build installation ($DEPOT_ROOT/flatpak-user).
#
# The runtime is installed from the local remote through the same signed
# summary and luma.flatpakrepo key clients use, so an app is never built
# against a runtime that failed verification. Safe to run repeatedly: refs
# already installed are updated to the published commit.
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"

depot_validate_build_architectures
site="$DEPOT_ROOT/site"
[ -f "$site/luma.flatpakrepo" ] || depot_die "no $site/luma.flatpakrepo; run update-remote.sh first"

# Different app builders may share one installation through distinct symlinks.
# Serialize remote configuration and deployment as one operation; otherwise a
# sibling's container-private repository URL can replace ours mid-install.
install -d "$depot_flatpak_user_dir"
compiler_installation=$(realpath "$depot_flatpak_user_dir")
exec 9>"$compiler_installation.compiler.lock"
flock --exclusive 9

depot_in_tools bash -s -- "$site" "$depot_repo" "$DEPOT_RUNTIME_BRANCH" "$DEPOT_ARCH" "$DEPOT_TOOLS_ARCH" <<'SH'
set -euo pipefail
site=$1 repo=$2 branch=$3 target_arch=$4 tools_arch=$5
sed "s#^Url=.*#Url=file://$repo#" "$site/luma.flatpakrepo" >/tmp/luma-local.flatpakrepo
flatpak remote-add --user --if-not-exists luma-local /tmp/luma-local.flatpakrepo
flatpak remote-modify --user --url="file://$repo" luma-local
flatpak install --user --arch="$target_arch" --noninteractive --or-update luma-local \
  "org.projectluma.Platform//$branch" "org.projectluma.Sdk//$branch"
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user --arch="$tools_arch" --noninteractive --or-update flathub org.flatpak.Builder
SH
