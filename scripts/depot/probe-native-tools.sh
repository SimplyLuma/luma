#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Exercise the ordinary native builder sandbox and signed linter without app inputs.
set -euo pipefail
source_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
: "${DEPOT_ROOT:?an isolated probe root is required}"
: "${DEPOT_ARCH:?the actual target CPU is required}"
[ "$(uname -m)" = "$DEPOT_ARCH" ]
if [ "${1:-}" != --user ]; then
  [ "$(id -u)" = 0 ]
  . "$source_root/config/desktop/inputs.env"
  case "$DEPOT_ARCH" in
    x86_64) test "${BUILDER_IMAGE:-}" = "$FEDORA_RPM_BUILD_CONTAINER" ;;
    aarch64) test "${BUILDER_IMAGE:-}" = "$FEDORA_RPM_BUILD_CONTAINER_AARCH64" ;;
    *) exit 1 ;;
  esac
  bash "$source_root/scripts/depot/install-tools-packages.sh"
  # This is a new disposable container account, with no signing/API/SSH keys.
  useradd --create-home --uid 1000 luma-ci
  chown luma-ci:luma-ci /home/luma-ci
  install -d -o luma-ci -g luma-ci "$DEPOT_ROOT" "$DEPOT_ROOT/tmp" "$DEPOT_ROOT/logs"
  exec runuser -u luma-ci -- env HOME=/home/luma-ci DEPOT_ROOT="$DEPOT_ROOT" \
    DEPOT_ARCH="$DEPOT_ARCH" TMPDIR="$DEPOT_ROOT/tmp" \
    FLATPAK_USER_DIR="$DEPOT_ROOT/flatpak-user" \
    bash "$source_root/scripts/depot/probe-native-tools.sh" --user
fi
[ "$(id -u)" != 0 ]
[ -w "$HOME" ]
bwrap --unshare-user --unshare-pid --ro-bind / / /usr/bin/true
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user --noninteractive --or-update --arch="$DEPOT_ARCH" flathub org.flatpak.Builder
dbus-run-session -- flatpak run --user --command=flatpak-builder-lint org.flatpak.Builder --help > "$DEPOT_ROOT/logs/linter-help.txt"
flatpak info --user --show-ref org.flatpak.Builder | grep -qx "app/org.flatpak.Builder/$DEPOT_ARCH/stable"
python3 - "$DEPOT_ROOT" "$DEPOT_ARCH" <<'PY'
import datetime,json,pathlib,subprocess,sys
root=pathlib.Path(sys.argv[1]);arch=sys.argv[2]
def run(*args):return subprocess.check_output(args,text=True).strip()
result={'result':'PASS','scope':'native keyless tooling/sandbox/linter execution only; no app/runtime build, signing or publication','observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'architecture':arch,'builder_ref':run('flatpak','info','--user','--show-ref','org.flatpak.Builder'),'builder_commit':run('flatpak','info','--user','--show-commit','org.flatpak.Builder'),'linter_help_bytes':(root/'logs/linter-help.txt').stat().st_size}
assert result['linter_help_bytes']>0
(root/'NATIVE-TOOLS-RECEIPT.json').write_text(json.dumps(result,indent=2)+'\n')
PY
