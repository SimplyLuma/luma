#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Shared keyless Fedora builder tooling; normal repository signature checks apply.
set -euo pipefail
dnf5 -q -y install \
  flatpak flatpak-libs flatpak-builder flatpak-module-tools ostree minisign \
  appstream appstream-compose createrepo_c gnupg2 pinentry \
  python3-pyyaml python3-requests python3-pytest git-core rsync jq \
  desktop-file-utils librsvg2-tools util-linux zstd tar findutils which python3-pillow cpio \
  dnf5-plugins rclone s3cmd
