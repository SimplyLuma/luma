#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Execute through the project's bounded test runner. All account data is private
# to this disposable bus; production credentials and services are never used.
set -euo pipefail
ari_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
ari_test_dir=$(mktemp -d "${DEV_ARTIFACT_DIR:-/tmp}/ari-chatgpt-native.XXXXXX")
# AF_UNIX control sockets must fit the kernel's short pathname limit even
# when DEV_ARTIFACT_DIR is a long server/job path. This directory is disposable.
ari_test_runtime=$(mktemp -d /tmp/ari-chatgpt-runtime.XXXXXX)
cleanup_runtime() {
    ari_cleanup_status=$?
    # The disposable session may activate its own document portal. Never walk
    # through its FUSE mount while removing the temporary runtime directory.
    if mountpoint -q "$ari_test_runtime/doc"; then
        fusermount3 -u "$ari_test_runtime/doc" || return 1
    fi
    rm -rf -- "$ari_test_runtime"
    return "$ari_cleanup_status"
}
trap cleanup_runtime EXIT
mkdir -m 700 "$ari_test_dir/config" "$ari_test_dir/data" "$ari_test_dir/cache"
mkdir "$ari_test_dir/schemas"
cp "$ari_root/src/luma-ari/data/org.projectluma.Ari.gschema.xml" "$ari_test_dir/schemas/"
glib-compile-schemas --strict "$ari_test_dir/schemas"
export GSETTINGS_SCHEMA_DIR="$ari_test_dir/schemas"
export XDG_RUNTIME_DIR="$ari_test_runtime" XDG_CONFIG_HOME="$ari_test_dir/config"
export XDG_DATA_HOME="$ari_test_dir/data" XDG_CACHE_HOME="$ari_test_dir/cache"
export GSETTINGS_BACKEND=memory GDK_BACKEND=x11 GSK_RENDERER=cairo GTK_A11Y=none
export LUMA_ARI_PREVIEW=1 ARI_CHATGPT_PRIVATE_TEST=1
export PYTHONPATH="${ARI_TEST_INSTALLED_PYTHON:-$ari_root/src/luma-ari}:$ari_root/src/luma-platform/appkit"
export LUMA_APPKIT_ICON_PATH="$ari_root/assets/icon-theme/Prairie/symbolic/actions"
export ARI_CHATGPT_CAPTURE="$ari_test_dir"
export ARI_TEST_KEYRING_DAEMON=${ARI_TEST_KEYRING_DAEMON:-$(command -v gnome-keyring-daemon)}
test -x "$ARI_TEST_KEYRING_DAEMON"
cd "$ari_root"
dbus-run-session -- xvfb-run -a -s '-screen 0 1920x1080x24' "${ARI_TEST_PYTHON:-python3}" tests/luma-ari/native_chatgpt.py
