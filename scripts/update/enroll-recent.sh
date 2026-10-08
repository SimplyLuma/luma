#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: enroll-recent.sh --url HTTPS_URL --public-key PATH [--allow-insecure-http]
EOF
  exit 2
}

url=
public_key=
allow_insecure=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --url) url=${2:-}; shift 2 ;;
    --public-key) public_key=${2:-}; shift 2 ;;
    --allow-insecure-http) allow_insecure=1; shift ;;
    *) usage ;;
  esac
done
[ -n "$url" ] && [ -n "$public_key" ] || usage

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/update/recent.env"
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail 'enrollment must run as root'
[ -s "$public_key" ] || fail "public key is missing or empty: $public_key"
for tool in gpg ostree rpm-ostree; do
  command -v "$tool" >/dev/null 2>&1 || fail "required enrollment tool is missing: $tool"
done
case "$url" in
  https://*) ;;
  http://*) [ "$allow_insecure" -eq 1 ] || fail 'HTTP requires --allow-insecure-http' ;;
  file://*) [ "$allow_insecure" -eq 1 ] || fail 'file repositories require --allow-insecure-http' ;;
  *) fail 'Recent URL must use HTTPS (or an explicitly allowed lab transport)' ;;
esac

key_fingerprint=$(gpg --batch --show-keys --with-colons "$public_key" 2>/dev/null |
  awk -F: '$1 == "fpr" { print $10; exit }')
[ -n "$key_fingerprint" ] || fail 'public key is not a valid OpenPGP keyring'

install -D -m 0644 "$public_key" /etc/pki/ostree/luma-recent.gpg
ostree remote add --sysroot=/ --force \
  --gpg-import=/etc/pki/ostree/luma-recent.gpg \
  --set=gpg-verify=true --set=gpg-verify-summary=true \
  --collection-id="$LUMA_UPDATE_COLLECTION_ID" \
  "$LUMA_UPDATE_REMOTE" "$url" "$LUMA_UPDATE_REF"

# Fetch and verify metadata before persisting the enrollment contract.
ostree remote refs --repo=/ostree/repo "$LUMA_UPDATE_REMOTE" |
  grep -Fxq "$LUMA_UPDATE_REMOTE:$LUMA_UPDATE_REF" ||
  fail 'the signed remote does not advertise the Recent ref'

install -d -m 0755 /etc/luma
channel_tmp=$(mktemp /etc/luma/update-channel.conf.XXXXXX)
trap 'rm -f "$channel_tmp"' EXIT
{
  printf 'channel=%s\n' "$LUMA_UPDATE_CHANNEL"
  printf 'remote=%s\n' "$LUMA_UPDATE_REMOTE"
  printf 'ref=%s\n' "$LUMA_UPDATE_REF"
} >"$channel_tmp"
chmod 0644 "$channel_tmp"
mv "$channel_tmp" /etc/luma/update-channel.conf
trap - EXIT

# A fresh Fedora Atomic machine does not have the Luma client unit until the
# first Recent deployment boots. Persist the normal enablement symlink now;
# when enrollment starts from an existing Luma deployment, also start it.
if systemctl cat luma-recent-update.timer >/dev/null 2>&1; then
  systemctl enable --now luma-recent-update.timer
else
  install -d -m 0755 /etc/systemd/system/timers.target.wants
  ln -sfn /usr/lib/systemd/system/luma-recent-update.timer \
    /etc/systemd/system/timers.target.wants/luma-recent-update.timer
fi
rpm-ostree rebase "$LUMA_UPDATE_REMOTE:$LUMA_UPDATE_REF"
printf 'Recent enrolled; signed deployment staged. Reboot when ready.\n'
printf 'key=%s\nref=%s\n' "$key_fingerprint" "$LUMA_UPDATE_REF"
