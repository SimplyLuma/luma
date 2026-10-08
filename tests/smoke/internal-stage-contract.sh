#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

failures=0
check() {
  description=$1
  shift
  if "$@"; then
    printf 'PASS  %s\n' "$description"
  else
    printf 'FAIL  %s\n' "$description"
    failures=$((failures + 1))
  fi
}

spec_release() {
  awk '/^Release:/ { value=$2; sub(/%\{\?dist\}$/, "", value); print value; exit }' "$1"
}

check 'stage parity updater is fast-forward only' \
  grep -Fq 'merge --ff-only refs/remotes/origin/stage' \
  "$repo_root/scripts/dev/internal-stage.sh"
check 'stage parity updater rejects dirty source' \
  grep -Fq 'status --porcelain' "$repo_root/scripts/dev/internal-stage.sh"
check 'internal channel distinguishes source and installed parity' \
  grep -Fq 'Source parity and installed-machine parity are different' \
  "$repo_root/docs/build/internal-stage-channel.md"

# Every pinned Luma package, not only the ones named individually below: a pin
# ahead of its spec is a package built outside the tree. This is the assertion
# the release pipeline now runs before it builds anything
# (scripts/os/build-image.sh), so the drift cannot reach a nightly again.
check 'every pinned package names a release this tree builds' \
  "$repo_root/tests/smoke/package-release-contract.sh" "$repo_root"

check 'Android input matches its package release' test \
  "$LUMA_ANDROID_RUNTIME_RELEASE" = \
  "$(spec_release "$repo_root/packaging/rpm/luma-android-runtime.spec")"
check 'Core Apps input matches its package release' test \
  "$PRAIRIE_CORE_APPS_RELEASE" = \
  "$(spec_release "$repo_root/packaging/rpm/prairie-core-apps.spec")"
check 'Developer Platform input matches its package release' test \
  "$LUMA_DEVELOPER_PLATFORM_RELEASE" = \
  "$(spec_release "$repo_root/packaging/rpm/luma-developer-platform.spec")"
check 'Mods input matches its package release' test \
  "$LUMA_MODS_RELEASE" = \
  "$(spec_release "$repo_root/packaging/rpm/luma-mods.spec")"
check 'update agent input matches its package release' test \
  "$LUMA_UPDATE_RELEASE" = \
  "$(spec_release "$repo_root/packaging/rpm/luma-update.spec")"
check 'shell-state input matches its package release' test \
  "$LUMA_SHELL_STATE_RELEASE" = \
  "$(spec_release "$repo_root/packaging/rpm/luma-shell-state.spec")"
check 'Prairie icon input matches its package release' test \
  "$PRAIRIE_ICON_THEME_RELEASE" = \
  "$(spec_release "$repo_root/packaging/rpm/prairie-icon-theme.spec")"

check 'desktop carries the exact Core Apps package' \
  grep -Fqx "$PRAIRIE_CORE_APPS_NEVRA" "$repo_root/config/desktop/packages.txt"
check 'Core Apps remains a shared application role' \
  grep -Fqx 'prairie-core-apps' "$repo_root/config/shared/application-packages.txt"
check 'mobile consumes the exact shared Core Apps package' \
  grep -Fq 'build/packages/prairie-core-apps/RPMS/noarch/$PRAIRIE_CORE_APPS_NEVRA.rpm' \
  "$repo_root/scripts/mobile/compose-fp6-rootfs.sh"
check 'desktop consumes the exact shared Core Apps package' \
  grep -Fq 'build/packages/prairie-core-apps/RPMS/noarch/$PRAIRIE_CORE_APPS_NEVRA.rpm' \
  "$repo_root/scripts/vm/compose-desktop-image.sh"
check 'desktop build contains Android, Relay, Mods, platform, and Core Apps' \
  bash -c 'for component in build-luma-android-runtime.sh build-luma-relay.sh build-luma-mods.sh build-luma-developer-platform.sh build-prairie-core-apps.sh; do grep -Fq "$component" "$1" || exit 1; done' \
  _ "$repo_root/scripts/build-luma-desktop.sh"
check 'desktop build contains the Luma update agent' \
  grep -Fq 'build-luma-update.sh' "$repo_root/scripts/build-luma-desktop.sh"
check 'Recent publishing advances only signed accepted deployments' \
  bash -c 'grep -Fq "ostree gpg-sign" "$1" && grep -Fq "ostree summary" "$1" && grep -Fq "rpm-ostree status --json" "$2"' \
  _ "$repo_root/scripts/update/promote-ostree-commit.sh" \
  "$repo_root/scripts/vm/compose-desktop-image.sh"
check 'Recent publishing preserves bare OSTree metadata with a read-only image attach' \
  bash -c 'grep -Fq "qemu-nbd --read-only" "$1" && grep -Fq "mount_options=ro,noload" "$1" && grep -Fq "ostree init --repo=\"\$source_archive\" --mode=archive" "$1" && grep -Fq "LUMA_BUILD_USER=root" "$1" && grep -Fq -- "--source-repo \"\$source_archive\"" "$1" && ! grep -Fq "guestmount" "$2"' \
  _ "$repo_root/scripts/build-host/publish-current-recent.sh" \
  "$repo_root/scripts/update/publish-recent.sh"
check 'canonical builder may verify Stage against its protected local mirror' \
  grep -Fq 'stage_remote=${LUMA_STAGE_REMOTE:-origin}' \
  "$repo_root/scripts/update/publish-recent.sh"
check 'Recent HTTPS stays behind the approved SSH boundary' \
  bash -c 'grep -Fq "listen 127.0.0.1:8443 ssl;" "$1" && grep -Fq "ssl_certificate /etc/luma/recent-tls/server.crt;" "$1" && grep -Fq "client_body_temp_path /run/luma-recent-nginx/client_body;" "$1" && grep -Fq "types_hash_bucket_size 128;" "$1" && grep -Fq -- "-e stderr -c /etc/luma/recent-nginx.conf" "$2"' \
  _ "$repo_root/config/build-host/luma-recent-nginx.conf" \
  "$repo_root/config/build-host/luma-recent-http.service"
check 'guarded runner executes data-tree commands through a labeled system binary' \
  grep -Fq '/usr/bin/env -- "$@"' \
  "$repo_root/scripts/build-host/luma-build-run"
check 'guarded runner avoids SSH-labelled systemd pipe transport' \
  bash -c 'grep -Fq "StandardOutput=journal" "$1" && grep -Fq "journalctl --quiet" "$1" && grep -Fq "KillMode=process" "$1" && ! grep -Eq "^[[:space:]]+--pipe([[:space:]\\\\]|$)" "$1"' \
  _ "$repo_root/scripts/build-host/luma-build-run"
check 'guarded runner distinguishes an intentionally stopped protected VM' \
  grep -Fq 'coexistence_mode=protected-vm-off' \
  "$repo_root/scripts/build-host/luma-build-run"
check 'guarded runner still rejects unknown protected VM states' \
  grep -Fq 'running|shut\ off)' \
  "$repo_root/scripts/build-host/luma-build-run"
check 'Recent tunnel keys cannot open a shell or arbitrary forwards' \
  grep -Fq 'restrict,port-forwarding,permitopen="127.0.0.1:8443"' \
  "$repo_root/scripts/build-host/register-recent-client.sh"
check 'fresh Fedora enrollment persists the timer for the first Luma boot' \
  grep -Fq '/etc/systemd/system/timers.target.wants/luma-recent-update.timer' \
  "$repo_root/scripts/update/enroll-recent.sh"
check 'Recent enrollment retains GPG verification over the private transport' \
  bash -c 'grep -Fq "gpg-verify=true" "$1" && grep -Fq "gpg-verify-summary=true" "$1" && ! grep -Fq -- "--allow-insecure-http" "$2"' \
  _ "$repo_root/scripts/update/enroll-recent.sh" \
  "$repo_root/scripts/update/enroll-recent-tunneled.sh"
check 'Shell build carries desktop and handheld patches together' \
  bash -c 'grep -Fq "0008-luma-live-extensions.patch" "$1" && grep -Fq "0009-luma-handheld-activity-view.patch" "$1" && grep -Fq "0010-luma-shelf.patch" "$1" && grep -Fq "0011-luma-presence-login.patch" "$1"' \
  _ "$repo_root/scripts/packages/build-gnome-shell.sh"
check 'Shell source and spec releases remain synchronized' \
  bash -c '. "$1"; grep -Fq "Release:        ${GNOME_SHELL_LUMA_RELEASE}%{?dist}" "$2"' \
  _ "$repo_root/config/desktop/inputs.env" \
  "$repo_root/patches/gnome-shell/0000-luma-fedora-spec.patch"
check 'Luma Login source contract passes' \
  "$repo_root/tests/smoke/luma-login-source.sh"
check 'dock source and spec releases remain synchronized' \
  bash -c '. "$1"; grep -Fq "Release:        ${DASH_TO_DOCK_LUMA_RELEASE}%{?dist}" "$2"' \
  _ "$repo_root/config/desktop/inputs.env" \
  "$repo_root/patches/dash-to-dock/0000-luma-fedora-spec.patch"
check 'dock package verifier explicitly installs its GJS parser' \
  bash -c 'grep -Fq "dnf5 -y install rpm-build dnf5-plugins gjs" "$1" && grep -Fq "gjs -c" "$1" && grep -Fq '\''-e "/^import {$/,/^} from /d"'\'' "$1" && ! grep -Fq "gjs -m" "$1"' \
  _ "$repo_root/scripts/packages/build-dash-to-dock.sh"

if [ "$failures" -ne 0 ]; then
  printf '\nInternal stage contract: FAIL (%s checks)\n' "$failures"
  exit 1
fi

printf '\nInternal stage contract: PASS\n'
