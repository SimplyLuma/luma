#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
build_root=${LUMA_BUILD_ROOT:-/srv/luma-build}
build_user=${LUMA_BUILD_USER:-luma-build}
protected_vm=${LUMA_PROTECTED_VM:-viola-windows-builder}
signing_home="$build_root/signing/recent-development-gnupg"
signing_fingerprint="$build_root/config/recent-signing-fingerprint"
tls_root="$build_root/secrets/recent-tls"
service_config_root=/etc/luma
service_tls_root="$service_config_root/recent-tls"
webroot="$build_root/updates/webroot"
staging_repo="$build_root/updates/staging/repo"
change_log="$build_root/bootstrap/system-changes.log"
tunnel_user=luma-update-tunnel
nginx_config_source="$repo_root/config/build-host/luma-recent-nginx.conf"
nginx_unit_source="$repo_root/config/build-host/luma-recent-http.service"
tls_ext_source="$repo_root/config/build-host/luma-recent-server.ext"

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || fail 'Recent bootstrap must run as root'
[ "$build_root" = /srv/luma-build ] || fail 'reviewed Recent paths are bound to /srv/luma-build'
for path in "$nginx_config_source" "$nginx_unit_source" "$tls_ext_source"; do
  [ -r "$path" ] || fail "required Recent input is unavailable: $path"
done
for tool in curl gpg nginx openssl ostree restorecon semanage ssh-keygen systemctl virsh; do
  command -v "$tool" >/dev/null 2>&1 || fail "required Recent tool is missing: $tool"
done
state=$(virsh domstate "$protected_vm" 2>/dev/null | tr -d '\r' || true)
[ "$state" = running ] || fail "protected VM $protected_vm is ${state:-unavailable}"
getent passwd "$build_user" >/dev/null || fail "build account is missing: $build_user"
getent passwd nginx >/dev/null || fail 'nginx account is missing'

touch "$change_log"
chmod 0644 "$change_log"
log_change() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$change_log"
}

if ! getent passwd "$tunnel_user" >/dev/null; then
  useradd --system --home-dir "$build_root/update-tunnel" \
    --create-home --shell /usr/sbin/nologin "$tunnel_user"
  passwd --lock "$tunnel_user" >/dev/null
  log_change "created locked forwarding-only account $tunnel_user"
fi
[ "$(getent passwd "$tunnel_user" | cut -d: -f6)" = "$build_root/update-tunnel" ] ||
  fail "$tunnel_user has an unexpected home directory"

build_uid=$(id -u "$build_user")
build_gid=$(id -g "$build_user")
nginx_gid=$(id -g nginx)
tunnel_uid=$(id -u "$tunnel_user")
tunnel_gid=$(id -g "$tunnel_user")
install -d -o "$build_uid" -g "$build_gid" -m 0700 \
  "$build_root/signing" "$signing_home"
install -d -o "$build_uid" -g "$nginx_gid" -m 0750 \
  "$build_root/updates" "$webroot"
install -d -o "$build_uid" -g "$build_gid" -m 0750 \
  "$build_root/updates/staging" "$staging_repo"
install -d -o root -g root -m 0700 "$build_root/secrets" "$tls_root"
install -d -o root -g root -m 0700 "$build_root/client-bundles"
install -d -o "$tunnel_uid" -g "$tunnel_gid" -m 0700 \
  "$build_root/update-tunnel/.ssh"
touch "$build_root/update-tunnel/.ssh/authorized_keys"
chown "$tunnel_uid:$tunnel_gid" "$build_root/update-tunnel/.ssh/authorized_keys"
chmod 0600 "$build_root/update-tunnel/.ssh/authorized_keys"

mapfile -t signing_keys < <(
  runuser -u "$build_user" -- env GNUPGHOME="$signing_home" \
    gpg --batch --with-colons --list-secret-keys 2>/dev/null |
    awk -F: '$1 == "fpr" { print $10 }'
)
if [ "${#signing_keys[@]}" -eq 0 ]; then
  runuser -u "$build_user" -- env GNUPGHOME="$signing_home" \
    gpg --batch --passphrase '' --quick-generate-key \
      'Project Luma Recent Development <recent-development@projectluma.invalid>' \
      ed25519 sign 1y
  mapfile -t signing_keys < <(
    runuser -u "$build_user" -- env GNUPGHOME="$signing_home" \
      gpg --batch --with-colons --list-secret-keys |
      awk -F: '$1 == "fpr" { print $10 }'
  )
  log_change 'generated one-year internal Recent development signing key'
fi
[ "${#signing_keys[@]}" -eq 1 ] || fail 'Recent signer must contain exactly one secret key'
printf '%s\n' "${signing_keys[0]}" >"$signing_fingerprint"
chown "$build_uid:$build_gid" "$signing_fingerprint"
chmod 0644 "$signing_fingerprint"
runuser -u "$build_user" -- env GNUPGHOME="$signing_home" \
  gpg --batch --export "${signing_keys[0]}" \
  >"$build_root/config/luma-recent-development.gpg"
chown "$build_uid:$build_gid" "$build_root/config/luma-recent-development.gpg"
chmod 0644 "$build_root/config/luma-recent-development.gpg"

tls_files=(ca.key ca.crt server.key server.crt)
missing_tls=0
for name in "${tls_files[@]}"; do
  [ -s "$tls_root/$name" ] || missing_tls=$((missing_tls + 1))
done
if [ "$missing_tls" -ne 0 ] && [ "$missing_tls" -ne "${#tls_files[@]}" ]; then
  fail 'partial Recent TLS state requires review; refusing replacement'
fi
if [ "$missing_tls" -eq "${#tls_files[@]}" ]; then
  tls_stage=$(mktemp -d "$build_root/secrets/.recent-tls.XXXXXX")
  cleanup_tls() { find "$tls_stage" -depth -delete 2>/dev/null || true; }
  trap cleanup_tls EXIT INT TERM
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 \
    -out "$tls_stage/ca.key"
  openssl req -x509 -new -sha256 -days 365 \
    -key "$tls_stage/ca.key" \
    -subj '/CN=Project Luma Recent Development CA' \
    -out "$tls_stage/ca.crt"
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 \
    -out "$tls_stage/server.key"
  openssl req -new -sha256 -key "$tls_stage/server.key" \
    -subj '/CN=127.0.0.1' -out "$tls_stage/server.csr"
  openssl x509 -req -sha256 -days 365 \
    -in "$tls_stage/server.csr" -CA "$tls_stage/ca.crt" \
    -CAkey "$tls_stage/ca.key" -CAcreateserial \
    -extfile "$tls_ext_source" -out "$tls_stage/server.crt"
  install -o root -g root -m 0600 "$tls_stage/ca.key" "$tls_root/ca.key"
  install -o root -g root -m 0644 "$tls_stage/ca.crt" "$tls_root/ca.crt"
  install -o root -g nginx -m 0640 "$tls_stage/server.key" "$tls_root/server.key"
  install -o root -g nginx -m 0644 "$tls_stage/server.crt" "$tls_root/server.crt"
  cleanup_tls
  trap - EXIT INT TERM
  log_change 'generated loopback-only Recent development TLS CA and server certificate'
fi

install -d -o root -g root -m 0755 "$service_config_root"
install -d -o root -g nginx -m 0750 "$service_tls_root"
install -o root -g root -m 0644 "$nginx_config_source" \
  "$service_config_root/recent-nginx.conf"
install -o root -g nginx -m 0644 "$tls_root/server.crt" \
  "$service_tls_root/server.crt"
install -o root -g nginx -m 0640 "$tls_root/server.key" \
  "$service_tls_root/server.key"
install -o root -g root -m 0644 "$nginx_unit_source" \
  /etc/systemd/system/luma-recent-http.service
install -o root -g root -m 0755 "$script_dir/publish-current-recent.sh" \
  "$build_root/bin/publish-current-recent.sh"
install -o root -g root -m 0755 "$script_dir/register-recent-client.sh" \
  "$build_root/bin/register-recent-client.sh"
install -o root -g root -m 0755 "$script_dir/prepare-recent-client-bundle.sh" \
  "$build_root/bin/prepare-recent-client-bundle.sh"

web_fcontext="$webroot(/.*)?"
if ! semanage fcontext -l | awk -v pattern="$web_fcontext" \
  '$1 == pattern && $NF ~ /:httpd_sys_content_t:/ {found=1} END {exit !found}'; then
  semanage fcontext -a -t httpd_sys_content_t "$web_fcontext"
  log_change "declared SELinux httpd_sys_content_t context for $web_fcontext"
fi
tls_fcontext="$tls_root(/.*)?"
if ! semanage fcontext -l | awk -v pattern="$tls_fcontext" \
  '$1 == pattern && $NF ~ /:cert_t:/ {found=1} END {exit !found}'; then
  semanage fcontext -a -t cert_t "$tls_fcontext"
  log_change "declared SELinux cert_t context for $tls_fcontext"
fi
service_tls_fcontext="$service_tls_root(/.*)?"
if ! semanage fcontext -l | awk -v pattern="$service_tls_fcontext" \
  '$1 == pattern && $NF ~ /:cert_t:/ {found=1} END {exit !found}'; then
  semanage fcontext -a -t cert_t "$service_tls_fcontext"
  log_change "declared SELinux cert_t context for $service_tls_fcontext"
fi
restorecon -RF "$webroot" "$tls_root" "$service_tls_root"
systemctl daemon-reload
if [ -L "$webroot/current" ]; then
  systemctl enable --now luma-recent-http.service
  recent_ready=0
  for _ in $(seq 1 20); do
    if curl --fail --silent --cacert "$tls_root/ca.crt" \
        https://127.0.0.1:8443/health >/dev/null 2>&1; then
      recent_ready=1
      break
    fi
    sleep 0.25
  done
  [ "$recent_ready" -eq 1 ] || fail 'Recent HTTPS service did not become ready'
fi

log_change "verified Recent channel bootstrap; protected VM state=$state"
printf 'Recent development signer: %s\n' "${signing_keys[0]}"
printf 'Recent loopback endpoint: https://127.0.0.1:8443/luma/repo\n'
