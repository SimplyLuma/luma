#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Run rpm-ostree against a sysroot that is not booted (a card image being
# assembled), with the payload's own rpm-ostree, so the result is exactly what
# rpm-ostree would make on the device: a client-side layer, its origin and its
# boot entry. Used for overrides a device needs before its first boot.
#
#   rpm-ostree-offline.sh --sysroot DIR --stateroot NAME --deployment DIR
#                         [--bind HOST_DIR:PATH]... -- RPM_OSTREE_ARGS...
#
# The payload's deployment, overlaid (never written), is the root rpm-ostree
# runs in; the sysroot is bound at /sysroot and rpmostreed is started with
# --sysroot=/sysroot on a private system bus (the host's dbus-daemon), with
# the host's journal socket for its log. --stateroot NAME is added to the
# command. Package repositories come from the merge deployment's
# /etc/yum.repos.d; --bind makes local repositories or RPMs visible at PATH.
#
# On a host that is not aarch64 the payload runs under qemu-user binfmt (F
# flag). qemu-user cannot create namespaces, so bwrap (rpm-ostree's script
# sandbox) is the host's own bwrap, run through the host's dynamic loader.
#
# Runs as root from an unconfined login or `systemd-run --scope` (rpm-ostree
# writes SELinux labels the host's policy may lack: mac_admin).
set -euo pipefail

fail() { printf 'rpm-ostree-offline: %s\n' "$*" >&2; exit 1; }
sysroot="" stateroot="" deployment="" binds=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --sysroot) sysroot=$(realpath -e "${2:?}"); shift 2 ;;
    --stateroot) stateroot=${2:?}; shift 2 ;;
    --deployment) deployment=$(realpath -e "${2:?}"); shift 2 ;;
    --bind) binds+=("${2:?}"); shift 2 ;;
    --) shift; break ;;
    *) sed -n 's/^#   rpm-ostree-offline.sh /usage: rpm-ostree-offline.sh /p' "$0" >&2; exit 2 ;;
  esac
done
[ -n "$sysroot" ] && [ -n "$stateroot" ] && [ -n "$deployment" ] && [ "$#" -gt 0 ] || fail "missing arguments"
[ "$(id -u)" = 0 ] || fail "run as root"
[ -x "$deployment/usr/bin/rpm-ostree" ] || fail "the payload has no rpm-ostree"
command -v dbus-daemon >/dev/null || fail "the host needs dbus-daemon (a private system bus)"
host_bwrap=""
if [ "$(uname -m)" != aarch64 ]; then
  grep -qx 'flags: .*F.*' /proc/sys/fs/binfmt_misc/qemu-aarch64 2>/dev/null ||
    fail "aarch64 binaries need qemu-user binfmt with the F flag (qemu-user-static)"
  host_bwrap=$(command -v bwrap) || fail "the host needs bubblewrap"
fi

work=$(mktemp -d "${TMPDIR:-/var/tmp}/rpm-ostree-offline.XXXXXX")
bus_pid=""
cleanup() {
  [ -z "$bus_pid" ] || kill "$bus_pid" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT INT TERM
mkdir -p "$work/bus" "$work/upper" "$work/work" "$work/root" "$work/x86"
cat >"$work/bus.conf" <<EOF
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>system</type>
  <listen>unix:path=$work/bus/system_bus_socket</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow user="root"/><allow own="*"/><allow send_destination="*"/><allow receive_sender="*"/>
  </policy>
</busconfig>
EOF
bus_pid=$(dbus-daemon --config-file="$work/bus.conf" --fork --print-pid)

# Inside the root: rpmostreed on the bound sysroot, then the client.
cat >"$work/inner.sh" <<'EOF'
#!/bin/bash
set -u
touch /run/ostree-booted
rpm-ostree start-daemon --sysroot=/sysroot &
daemon=$!
trap 'kill $daemon 2>/dev/null' EXIT
for _ in $(seq 1 60); do rpm-ostree status >/dev/null 2>&1 && break; sleep 1; done
rpm-ostree "$@"
EOF
chmod 0755 "$work/inner.sh"
if [ -n "$host_bwrap" ]; then
  loader=$(ldd "$host_bwrap" | sed -n 's|^\s*\(/[^ ]*ld-linux[^ ]*\) .*|\1|p' | head -n 1)
  [ -n "$loader" ] || fail "cannot find the host bwrap's loader"
  for lib in "$host_bwrap" $(ldd "$host_bwrap" | grep -o '/[^ ]*'); do cp -L "$lib" "$work/x86/"; done
  printf '#!/bin/sh\nexec /run/luma-host/%s --library-path /run/luma-host /run/luma-host/bwrap "$@"\n' \
    "$(basename "$loader")" >"$work/bwrap"
  chmod 0755 "$work/bwrap"
fi

verb=$1; shift
case "$verb" in
  override) sub=$1; shift; set -- "$verb" "$sub" --stateroot="$stateroot" "$@" ;;
  *) set -- "$verb" --stateroot="$stateroot" "$@" ;;
esac

unshare --mount --propagation private bash -euc '
work=$1 sysroot=$2 deployment=$3 host_bwrap=$4 binds=$5; shift 5
r=$work/root
mount -t overlay overlay -o "lowerdir=$deployment,upperdir=$work/upper,workdir=$work/work" "$r"
mount -t proc proc "$r/proc"
mount --rbind /sys "$r/sys"; mount --make-rslave "$r/sys"
mount --rbind /dev "$r/dev"; mount --make-rslave "$r/dev"
for d in run tmp var; do mount -t tmpfs tmpfs "$r/$d"; done
mkdir -p "$r/run/dbus" "$r/run/systemd/journal" "$r/sysroot" "$r/var/tmp" "$r/var/cache"
mount --bind "$work/bus" "$r/run/dbus"
[ ! -S /run/systemd/journal/socket ] || mount --bind /run/systemd/journal "$r/run/systemd/journal"
mount --rbind "$sysroot" "$r/sysroot"  # with /boot: deployments are found through the boot loader entries
IFS=$'\''\n'\''
for pair in $binds; do
  [ -n "$pair" ] || continue
  mkdir -p "$r${pair#*:}"
  mount --bind "${pair%%:*}" "$r${pair#*:}"
done
unset IFS
cp "$work/inner.sh" "$r/run/luma-offline.sh"
if [ -n "$host_bwrap" ]; then
  mkdir -p "$r/run/luma-host"
  mount --bind "$work/x86" "$r/run/luma-host"
  mount --bind "$work/bwrap" "$r/usr/bin/bwrap"
fi
chroot "$r" /run/luma-offline.sh "$@"
' _ "$work" "$sysroot" "$deployment" "$host_bwrap" "$(printf '%s\n' "${binds[@]+"${binds[@]}"}")" "$@"
