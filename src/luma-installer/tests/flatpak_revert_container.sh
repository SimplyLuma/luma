#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Real libflatpak, no mocks: going back to an earlier build of a system Flatpak
# through luma-installer-system's flatpak-revert, in a disposable Fedora
# container, as root. Builds a tiny runtime and two versions of an app into a
# local repository named "luma", installs version 1 system-wide, updates to
# version 2, then reverts; also checks that a build the source no longer has is
# reported as commit-unavailable.
#
#   podman run --rm -v SRC:/src:ro,z registry.fedoraproject.org/fedora:44 \
#     bash /src/luma-installer/tests/flatpak_revert_container.sh
set -euo pipefail
dnf -y -q install flatpak flatpak-libs gobject-introspection python3-gobject ostree >/dev/null
ls /usr/lib64/girepository-1.0/Flatpak-1.0.typelib >/dev/null || { echo "FAIL no Flatpak typelib"; rpm -ql flatpak-libs | grep -i typelib; exit 1; }
work=$(mktemp -d)
cd "$work"

mkdir -p runtime/files/bin
cat >runtime/metadata <<'EOF'
[Runtime]
name=org.luma.TestRuntime
runtime=org.luma.TestRuntime/x86_64/1
sdk=org.luma.TestRuntime/x86_64/1
EOF
cp /usr/bin/true runtime/files/bin/sh
flatpak build-export --runtime --files=files --disable-sandbox repo runtime 1 >/dev/null

export_app() {
  rm -rf app
  mkdir -p app/files/bin app/export
  printf '[Application]\nname=org.example.Notes\nruntime=org.luma.TestRuntime/x86_64/1\nsdk=org.luma.TestRuntime/x86_64/1\ncommand=notes\n' >app/metadata
  printf '#!/bin/sh\necho %s\n' "$1" >app/files/bin/notes
  chmod +x app/files/bin/notes
  flatpak build-export --disable-sandbox repo app stable >/dev/null
  ostree --repo=repo rev-parse app/org.example.Notes/x86_64/stable
}
v1=$(export_app 1.0)
v2=$(export_app 2.0)
flatpak build-update-repo repo >/dev/null
flatpak remote-add --system --no-gpg-verify luma "file://$work/repo"
flatpak install --system -y --noninteractive luma org.example.Notes >/dev/null
installed() { flatpak info --system --show-commit org.example.Notes; }
[ "$(installed)" = "$v2" ] || { echo "FAIL expected v2 installed first"; exit 1; }
flatpak update --system -y --noninteractive --commit="$v1" org.example.Notes >/dev/null
flatpak update --system -y --noninteractive org.example.Notes >/dev/null
[ "$(installed)" = "$v2" ] || { echo "FAIL setup: not back on v2"; exit 1; }
echo "PASS setup: v1 $v1, v2 $v2 installed system-wide"

run_helper() {
  PYTHONPATH=/src/luma-installer python3 - "$@" <<'PY'
import sys
from unittest import mock
from luma_installer import depot_flatpak, system_helper
# The test repository is a file:// "luma" remote: accept it and this one app.
with mock.patch.object(depot_flatpak, "APPLICATIONS", frozenset({"org.example.Notes"})), \
     mock.patch.object(depot_flatpak, "validate_remote", lambda *a, **k: None):
    sys.exit(system_helper.main(["flatpak-revert", "org.example.Notes", sys.argv[1]]))
PY
}

out=$(run_helper "$v1" 2>&1) || { echo "FAIL revert exited non-zero: $out"; exit 1; }
[ "$(installed)" = "$v1" ] && echo "PASS reverted to v1 through flatpak-revert" || { echo "FAIL still on $(installed): $out"; exit 1; }
grep -q "result: reverted" <<<"$out" && echo "PASS helper said result: reverted" || { echo "FAIL output: $out"; exit 1; }

out=$(run_helper "$v1" 2>&1)
grep -q "result: unchanged" <<<"$out" && echo "PASS reverting to the installed build changes nothing" || { echo "FAIL: $out"; exit 1; }

missing=$(printf 'f%.0s' $(seq 64))
if out=$(run_helper "$missing" 2>&1); then echo "FAIL a missing build was accepted"; exit 1; fi
grep -q "commit-unavailable" <<<"$out" && echo "PASS a build the source does not have is commit-unavailable" ||
  { echo "FAIL missing build said: $out"; exit 1; }
[ "$(installed)" = "$v1" ] && echo "PASS nothing changed after the refused revert"

flatpak update --system -y --noninteractive org.example.Notes >/dev/null
[ "$(installed)" = "$v2" ] && echo "PASS a normal update afterwards installs v2 again"
echo "ALL PASS"
