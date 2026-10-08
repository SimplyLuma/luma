#!/usr/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# ADR-030 image contract, checked on a booted Luma OS install (the VM gate
# runs it as root after every boot it evaluates).
#
#   image-contract.sh --release-env FILE --expect-commit SHA256 --channel CHANNEL
#
# It proves the update and trust policy of the running system: Luma is the only
# OSTree source, signatures are required with the Luma OS Release key only,
# the booted deployment is the expected signed commit on the channel ref,
# Fedora's competing updaters are absent, the identity is Luma's, and greenboot
# judged this boot healthy.

set -euo pipefail

release_env=
expect_commit=
channel=
# A machine installed from an image that named its URL in the remote keeps its
# own /etc/ostree/remotes.d/luma.conf across updates (OSTree's /etc merge);
# luma-update supports that layout, so --updated-from-url-layout accepts it.
remote_layout=mirrorlist
# luma-update applies kargs.d arguments when it stages an update from
# 1.0.0-1.luma.4 on; a machine updated by an older agent boots the new release
# without arguments that release added, until its next update.
# --updated-by-agent-without-kargs reports those instead of failing.
kargs_policy=require
while [ "$#" -gt 0 ]; do
  case "$1" in
    --release-env) release_env=${2:?}; shift 2 ;;
    --expect-commit) expect_commit=${2:?}; shift 2 ;;
    --channel) channel=${2:?}; shift 2 ;;
    --updated-from-url-layout) remote_layout=any; shift ;;
    --updated-by-agent-without-kargs) kargs_policy=report; shift ;;
    *) printf 'usage: %s --release-env FILE --expect-commit SHA --channel C [--updated-from-url-layout]\n' "$0" >&2; exit 2 ;;
  esac
done
[ -r "$release_env" ] && [ -n "$expect_commit" ] && [ -n "$channel" ] || exit 2

contract() { sed -n "s/^$1=//p" "$release_env"; }
ref="$(contract LUMA_OS_REF_PREFIX)/$channel"
fingerprint=$(contract LUMA_OS_RELEASE_KEY_FINGERPRINT)
key_path=$(contract LUMA_OS_RELEASE_KEY_PATH)
collection=$(contract LUMA_OS_COLLECTION_ID)

failures=0
check() {
  local description=$1
  shift
  if "$@" >/tmp/luma-contract-check.out 2>&1; then
    printf 'PASS  %s\n' "$description"
  else
    printf 'FAIL  %s\n' "$description"
    sed 's/^/      /' /tmp/luma-contract-check.out | head -n 20
    failures=$((failures + 1))
  fi
}

status_json=$(rpm-ostree status --json)
booted() { python3 -c 'import json,sys; d=[x for x in json.load(sys.stdin)["deployments"] if x.get("booted")][0]; print(d.get(sys.argv[1]) or "")' "$1" <<<"$status_json"; }

check "booted deployment is the expected commit ($expect_commit)" \
  test "$(booted checksum)" = "$expect_commit"
check "origin follows luma:$ref" \
  test "$(booted origin)" = "luma:$ref"
check 'booted commit carries a channel version' \
  bash -c "[ -n \"\$(rpm-ostree status --json | python3 -c 'import json,sys; print([x for x in json.load(sys.stdin)[\"deployments\"] if x.get(\"booted\")][0].get(\"version\") or \"\")')\" ]"
check 'the booted commit is signed by the Luma OS Release key' \
  bash -c "ostree show --repo=/ostree/repo --gpg-verify-remote=luma '$expect_commit' | grep -Fq '${fingerprint:24}'"
check 'the only OSTree remote is luma' \
  test "$(ostree remote list --repo=/ostree/repo | tr '\n' ' ')" = 'luma '
check 'the luma remote requires signed commits' \
  test "$(ostree config --repo=/ostree/repo get --group 'remote "luma"' gpg-verify 2>/dev/null || sed -n 's/^gpg-verify=//p' /etc/ostree/remotes.d/luma.conf)" = true
check 'the luma remote requires a signed summary' \
  grep -Fxq 'gpg-verify-summary=true' /etc/ostree/remotes.d/luma.conf
check 'the luma remote trusts only the Luma OS Release key file' \
  grep -Fxq "gpgkeypath=$key_path" /etc/ostree/remotes.d/luma.conf
check 'the luma remote is bound to the Luma collection' \
  grep -Fxq "collection-id=$collection" /etc/ostree/remotes.d/luma.conf
check "the luma remote reads its URL from the mirror list (layout: $remote_layout)" \
  bash -c 'grep -Fxq "url=mirrorlist=file:///etc/luma/update-mirrorlist" /etc/ostree/remotes.d/luma.conf ||
    { [ "$1" = any ] && grep -Exq "url=(https://dl\.simplyluma\.com/os/repo|mirrorlist=file:///etc/luma/update-preview-mirrorlist)" /etc/ostree/remotes.d/luma.conf; }' _ "$remote_layout"
check 'the remote file is world-readable and the mirror list root-only' \
  bash -c '[ "$(stat -c "%a %U" /etc/ostree/remotes.d/luma.conf)" = "644 root" ] && [ "$(stat -c "%a %U" /etc/luma/update-mirrorlist)" = "600 root" ]'
check 'the mirror list names one repository URL over HTTPS (or a test rig over HTTP)' \
  bash -c '[ "$(grep -c . /etc/luma/update-mirrorlist)" = 1 ] && grep -Eq "^(https://|http://[0-9.]+:[0-9]+)" /etc/luma/update-mirrorlist'
check 'ostree resolves the luma remote to the URL its file names' \
  bash -c '[ "url=$(ostree remote show-url --repo=/ostree/repo luma)" = "$(grep -m1 "^url=" /etc/ostree/remotes.d/luma.conf)" ]'
check 'greenboot can tell a trial boot (grub2-editenv and a readable grubenv)' \
  bash -c 'command -v grub2-editenv >/dev/null && test -r /boot/grub2/grubenv && grub2-editenv /boot/grub2/grubenv list >/dev/null && command -v logger >/dev/null'
check 'the greenboot helper logged no errors this boot' \
  bash -c '! journalctl -b -p err -t luma-greenboot --no-pager -q | grep -q .'
check 'the shipped release key is the contract key' \
  bash -c "gpg --batch --no-options --homedir \"\$(mktemp -d)\" --with-colons --show-keys '$key_path' | grep -q '^fpr:::::::::$fingerprint:'"
check 'no Fedora OSTree remote definitions' \
  bash -c '! ls /etc/ostree/remotes.d/ | grep -Ev "^luma\.conf$"'
check 'GNOME Software is absent' \
  bash -c '! rpm -q gnome-software gnome-software-rpm-ostree'
check 'rpm-ostreed automatic updates are off' \
  bash -c "grep -Fxq 'AutomaticUpdatePolicy=none' /etc/rpm-ostreed.conf && ! systemctl is-enabled --quiet rpm-ostreed-automatic.timer"
check 'the Fedora OCI Flatpak remote is not added' \
  bash -c '! systemctl is-enabled --quiet flatpak-add-fedora-repos.service && ! flatpak remotes --system --columns=url | grep -Fq oci+https://registry.fedoraproject.org'
check 'Fedora dnf repositories stay available for layering' \
  test -f /etc/yum.repos.d/fedora.repo
check 'fwupd stays installed' rpm -q fwupd
check 'rpm-ostree base packages are the image packages (overrides of Luma packages act)' \
  bash -c '[ "$(rpm -qa --dbpath /usr/lib/sysimage/rpm-ostree-base-db | sort | sha256sum)" = "$(rpm -qa | sort | sha256sum)" ]'
check 'sudo keeps its setuid bit' \
  test "$(stat -c %a /usr/bin/sudo)" = 4111
check 'every setuid and setgid file has the mode its package records' \
  python3 -c '
import os, subprocess, sys
out = subprocess.run(["rpm", "-qa", "--qf", "[%{FILEMODES} %{FILENAMES}\\n]"], capture_output=True, text=True, check=True).stdout
bad = []
for line in out.splitlines():
    mode, _, path = line.partition(" ")
    mode = int(mode)
    if not mode & 0o6000 or not path.startswith("/usr/") or not os.path.lexists(path) or os.path.islink(path):
        continue
    actual = os.lstat(path).st_mode & 0o7777
    if actual != mode & 0o7777:
        bad.append(f"{path}: {actual:o}, package records {mode & 0o7777:o}")
print("\n".join(bad))
sys.exit(1 if bad else 0)'
check 'every file capability its package records is present (newuidmap, newgidmap and others)' \
  python3 -c '
import os, subprocess, sys
out = subprocess.run(["rpm", "-qa", "--qf", "[%{FILECAPS}\\t%{FILENAMES}\\n]"], capture_output=True, text=True, check=True).stdout
bad = []
checked = 0
for line in out.splitlines():
    caps, _, path = line.partition("\t")
    if caps in ("", "(none)") or not path.startswith("/usr/") or not os.path.lexists(path) or os.path.islink(path):
        continue
    checked += 1
    got = subprocess.run(["getcap", path], capture_output=True, text=True).stdout.split(None, 1)
    actual = got[1].strip() if len(got) == 2 else ""
    if actual != caps:
        bad.append(path + ": " + (actual or "none") + ", package records " + caps)
print(f"{checked} files with capabilities")
print("\n".join(bad))
sys.exit(1 if bad or not checked else 0)'
check 'the running kernel has every argument the image declares in kargs.d' \
  python3 -c '
import glob, sys, tomllib
cmdline = open("/proc/cmdline").read().split()
missing = []
for path in sorted(glob.glob("/usr/lib/bootc/kargs.d/*.toml")):
    data = tomllib.load(open(path, "rb"))
    arches = data.get("match-architectures")
    if arches and "x86_64" not in arches:
        continue
    missing += [karg + " (" + path + ")" for karg in data.get("kargs", []) if karg not in cmdline]
print("\n".join(missing))
if missing and sys.argv[1] == "report":
    print("not applied: this machine was updated by a luma-update older than 1.0.0-1.luma.4")
    sys.exit(0)
sys.exit(1 if missing else 0)' "$kargs_policy"
check 'package layering resolves the Fedora release' \
  bash -c '. /usr/lib/os-release && [ "$(cat /etc/dnf/vars/releasever)" = "${PLATFORM_ID#platform:f}" ]'
check 'os-release names Luma' \
  bash -c '. /usr/lib/os-release && [ "$NAME" = Luma ] && [ "$ID" = luma ] && [ "$ID_LIKE" = fedora ] && [ -n "$VERSION_ID" ] && [ -n "$BUILD_ID" ]'
check 'the update-graph public key is shipped' \
  bash -c "grep -Fq \"$(contract LUMA_OS_GRAPH_KEY_ID)\" \"$(contract LUMA_OS_GRAPH_PUBLIC_KEY_PATH)\""
check 'the machine boots to the graphical login' \
  test "$(systemctl get-default)" = graphical.target
check 'greenboot health checks are installed and enabled' \
  bash -c 'ls /usr/lib/greenboot/check/required.d/*luma* >/dev/null && systemctl is-enabled --quiet greenboot-healthcheck.service'
check 'greenboot judged this boot healthy' \
  bash -c 'systemctl is-active --quiet greenboot-healthcheck.service && ! systemctl is-failed --quiet greenboot-healthcheck.service'
check 'every Luma health check passed on this boot' \
  bash -c 'journalctl -b -u greenboot-healthcheck.service --no-pager | grep -Fq "Luma: display manager reached" && journalctl -b -u greenboot-healthcheck.service --no-pager | grep -Fq "Luma: GNOME Shell" && journalctl -b -u greenboot-healthcheck.service --no-pager | grep -Fq "Luma: NetworkManager is running"'
check 'GRUB boot counting is present for automatic rollback' \
  grep -q boot_counter /boot/grub2/grub.cfg
check 'the boot was marked successful' \
  bash -c 'grub2-editenv list | grep -Fxq boot_success=1'
check 'no failed system units' \
  bash -c '[ -z "$(systemctl list-units --state=failed --no-legend --plain)" ] || { systemctl list-units --state=failed --no-legend --plain; exit 1; }'
check 'no failed user units in any running session (login screen included)' \
  bash -c 'rc=0; for u in $(loginctl list-users --no-legend | awk "{ print \$2 }"); do f=$(systemctl --user -M "$u@" list-units --state=failed --no-legend --plain 2>/dev/null); [ -z "$f" ] || { printf "%s: %s\n" "$u" "$f"; rc=1; }; done; exit $rc'

if [ "$failures" -ne 0 ]; then
  printf '\nImage contract: FAIL (%s checks)\n' "$failures"
  exit 1
fi
printf '\nImage contract: PASS\n'
