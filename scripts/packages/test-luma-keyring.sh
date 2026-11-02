#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
#
# test-luma-keyring.sh [PODMAN_GLOBAL_OPTION...]
#
# Runs luma-keyring's unit tests (as an ordinary user), the PAM integration
# test (as root, against a throwaway account) and an RPM build with %check,
# in one disposable Fedora container booted with systemd, so systemd-creds,
# systemd-userdbd and the real passwd stack are all there. Nothing touches
# the host's accounts or PAM files. Podman global options (for example
# --root on a scratch disk) are passed through. The container is removed
# afterwards; RPMs are left in build/packages/luma-keyring-test/.
# LUMA_KEYRING_TEST_RUN_OPTS adds options to podman run (for example
# --security-opt=label=disable where the storage is not labelled for
# containers).

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
image=${LUMA_KEYRING_TEST_IMAGE:-registry.fedoraproject.org/fedora:44}
name=luma-keyring-test-$$
output="$repo_root/build/packages/luma-keyring-test"
mkdir -p "$output"

# LUMA_KEYRING_TEST_KEEP=1 leaves the container for a look inside.
cleanup() {
  if [ "${LUMA_KEYRING_TEST_KEEP:-}" = 1 ]; then echo "kept container $name" >&2; return; fi
  podman "$@" rm -f "$name" >/dev/null 2>&1 || :
}
trap 'cleanup "$@"' EXIT INT TERM

# systemd-userdbd reads /etc/shadow with CAP_DAC_READ_SEARCH alone, which
# podman leaves out by default; without it no account sees its own hash.
# shellcheck disable=SC2086
podman "$@" run -d --name "$name" --systemd=always --cap-add=DAC_READ_SEARCH \
  ${LUMA_KEYRING_TEST_RUN_OPTS:-} \
  -v "$repo_root/src/luma-keyring:/src/luma-keyring:ro,Z" \
  -v "$repo_root/packaging/rpm/luma-keyring.spec:/src/luma-keyring.spec:ro,Z" \
  -v "$output:/out:Z" \
  "$image" sh -c '
    dnf5 -q -y install --setopt=install_weak_deps=False systemd util-linux shadow-utils \
      libpwquality cracklib-dicts authselect gcc meson ninja-build pam-devel libxcrypt-devel binutils \
      python3-cryptography python3-gobject-base python3-devel desktop-file-utils glib2 \
      rpm-build systemd-rpm-macros >/dev/null &&
    authselect select local --force >/dev/null &&
    exec /usr/sbin/init' >/dev/null

for _ in $(seq 1 240); do
  state=$(podman "$@" exec "$name" systemctl is-system-running 2>/dev/null || :)
  case "$state" in running|degraded) break ;; esac
  sleep 5
done
case "$state" in running|degraded) ;; *) echo "error: container did not boot ($state)" >&2; exit 1 ;; esac

podman "$@" exec -i "$name" bash -euo pipefail <<'EOF'
umask 022
cp -a /src/luma-keyring /tmp/lk
find /tmp/lk -name __pycache__ -prune -exec rm -rf {} +
meson setup /tmp/lk-build /tmp/lk --prefix=/usr --libdir=lib64 -Dwerror=true >/dev/null
ninja -C /tmp/lk-build >/dev/null
useradd -m -K SUB_UID_COUNT=0 -K SUB_GID_COUNT=0 tester
chmod -R a+rX /tmp/lk
echo "== unit tests ($(uname -m))"
runuser -u tester -- sh -c 'cd /tmp/lk/tests && python3 -B -m unittest discover -p "test_*.py"'
echo "== PAM integration ($(uname -m))"
python3 -B /tmp/lk/tests/pam/pam_integration.py /tmp/lk-build
echo "== RPM build ($(uname -m))"
mkdir -p /tmp/rpm/SOURCES /tmp/rpm/SPECS
tar -C /src --exclude __pycache__ -czf /tmp/rpm/SOURCES/luma-keyring.tar.gz luma-keyring
cp /src/luma-keyring.spec /tmp/rpm/SPECS/
rpmbuild -ba --define "_topdir /tmp/rpm" /tmp/rpm/SPECS/luma-keyring.spec >/tmp/rpm/build.log 2>&1 ||
  { tail -40 /tmp/rpm/build.log; exit 1; }
grep -E "^Ran |^OK" /tmp/rpm/build.log | tail -2
cp /tmp/rpm/RPMS/*/*.rpm /tmp/rpm/SRPMS/*.rpm /out/
ls /out
EOF
