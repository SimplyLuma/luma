#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

build_root=${LUMA_BUILD_ROOT:-/srv/luma-build}
client_name=
public_key=
usage() {
  printf 'usage: %s --name SAFE_NAME --public-key PATH\n' "$0" >&2
  exit 2
}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --name) client_name=${2:-}; shift 2 ;;
    --public-key) public_key=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done
[ "$(id -u)" -eq 0 ] || { printf 'error: registration requires root\n' >&2; exit 1; }
[[ "$client_name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || usage
[ -s "$public_key" ] || usage
[ "$(awk 'NF && $1 !~ /^#/ {count++} END {print count+0}' "$public_key")" -eq 1 ] || {
  printf 'error: client public-key file must contain exactly one key\n' >&2
  exit 1
}
read -r key_type key_body _ <"$public_key"
[ "$key_type" = ssh-ed25519 ] || {
  printf 'error: Recent transport requires an Ed25519 client key\n' >&2
  exit 1
}
fingerprint=$(ssh-keygen -lf "$public_key" -E sha256 | awk '{print $2}')
authorized="$build_root/update-tunnel/.ssh/authorized_keys"
registry="$build_root/update-tunnel/clients.tsv"
exec 9>"$build_root/update-tunnel/.registration.lock"
flock -x 9
if [ -s "$authorized" ] && ssh-keygen -lf "$authorized" -E sha256 |
  awk -v wanted="$fingerprint" '$2 == wanted {found=1} END {exit !found}'; then
  printf 'Recent client already registered: %s %s\n' "$client_name" "$fingerprint"
  exit 0
fi
printf '%s %s %s luma-recent:%s\n' \
  'restrict,port-forwarding,permitopen="127.0.0.1:8443"' \
  "$key_type" "$key_body" "$client_name" >>"$authorized"
printf '%s\t%s\t%s\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$client_name" "$fingerprint" >>"$registry"
tunnel_user=luma-update-tunnel
chown "$tunnel_user:$tunnel_user" "$authorized" "$registry"
chmod 0600 "$authorized" "$registry"
printf 'Recent client registered: %s %s\n' "$client_name" "$fingerprint"
