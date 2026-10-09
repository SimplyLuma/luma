#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
#
# Release gate checks for Depot out of the box. Run as root inside a disposable
# gate VM installed from the candidate's own ISO, with network access to
# dl.simplyluma.com and Flathub. Prints one JSON line per check
# ({"check", "result": "pass"|"fail"|"skip", "detail"}) and exits 1 if any
# check failed. Everything it adds is removed again.
#
#   depot-out-of-box.sh [--quick]
#
# Depot runs as the person, so every Depot check runs its real modules
# (luma_installer.depot_catalog, depot_flatpak; luma_depot.native) as a fresh
# unprivileged user. --quick skips the two installs (about 1.2 GB of runtimes).
#
# Check 3 needs luma-application-installer carrying commit e29fb914
# ("Let a developer's Flatpak listing say Luma's image ships the same app");
# when the installed build predates it, it fails and says so.
set -uo pipefail
quick=0
[ "${1:-}" = --quick ] && quick=1
user=luma-gate-depot
failed=0
work=$(mktemp -d /tmp/luma-gate-depot.XXXXXX)
chmod 0755 "$work"
declare -A originally_installed=()
for app in org.projectluma.Notes com.transmissionbt.Transmission; do
  if flatpak info --system "$app" >/dev/null 2>&1; then
    originally_installed[$app]=1
  fi
done

emit() {
  python3 -c 'import json, sys; print(json.dumps({"check": sys.argv[1], "result": sys.argv[2], "detail": sys.argv[3][-600:]}))' "$1" "$2" "$3"
  [ "$2" = fail ] && failed=1
  return 0
}
# py_check NAME SCRIPT: the script prints its detail and exits 0 pass, 1 fail, 77 skip.
py_check() {
  local name=$1 out code
  # In the user's own systemd user session (session bus, runtime directory),
  # as Depot runs: Flatpak's system helper and revokefs need both.
  out=$(systemd-run --user -M "$user@" --wait --pipe --quiet --collect \
        -p WorkingDirectory=/tmp python3 "$work/$2" 2>&1); code=$?
  case "$code" in
    0) emit "$name" pass "$(tail -n 4 <<<"$out")" ;;
    77) emit "$name" skip "$(tail -n 4 <<<"$out")" ;;
    *) emit "$name" fail "exit $code: $(tail -n 10 <<<"$out") | diag: $(ls -ldZ /var/tmp "/run/user/$(id -u "$user")" 2>&1 | tr '\n' ' '; df -h /var/tmp 2>&1 | tail -n 1; ls -la /var/tmp 2>&1 | grep -c flatpak-cache) flatpak-cache dirs" ;;
  esac
}

cleanup() {
  loginctl disable-linger "$user" >/dev/null 2>&1 || true
  loginctl terminate-user "$user" >/dev/null 2>&1 || true
  for app in org.projectluma.Notes com.transmissionbt.Transmission; do
    if [ "${originally_installed[$app]:-0}" != 1 ]; then
      flatpak uninstall --system -y --noninteractive "$app" >/dev/null 2>&1 || true
    fi
    runuser -u "$user" -- flatpak uninstall --user -y --noninteractive "$app" >/dev/null 2>&1 || true
  done
  flatpak uninstall --system -y --noninteractive --unused >/dev/null 2>&1 || true
  rm -f /etc/polkit-1/rules.d/10-luma-gate-depot.rules
  loginctl disable-linger "$user" >/dev/null 2>&1 || true
  userdel -r "$user" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

useradd -m "$user" || { emit gate-user fail "useradd $user failed"; exit 1; }
loginctl enable-linger "$user"
for _ in $(seq 60); do [ -S "/run/user/$(id -u "$user")/bus" ] && break; sleep 1; done
loginctl enable-linger "$user" >/dev/null 2>&1
for _ in $(seq 20); do [ -d "/run/user/$(id -u "$user")" ] && break; sleep 0.5; done
# Depot and the counter must not count a gate VM as a person's installation.
install -d -o "$user" -g "$user" "/home/$user/.config/luma/depot"
printf '{"install_events": false, "countme": false, "app_updates": false}\n' >"/home/$user/.config/luma/depot/settings.json"
chown "$user:" "/home/$user/.config/luma/depot/settings.json"
# On a desktop the person's active session authorizes a system Flatpak install;
# a gate user has no session, so allow exactly Flatpak's actions for it, until cleanup.
cat >/etc/polkit-1/rules.d/10-luma-gate-depot.rules <<EOF
polkit.addRule(function(action, subject) {
  if (subject.user == "$user" && action.id.indexOf("org.freedesktop.Flatpak.") == 0) return polkit.Result.YES;
});
EOF

if installer=$(rpm -q luma-application-installer 2>/dev/null); then
  emit installer-build pass "$installer"
else
  emit installer-build fail "luma-application-installer is not installed"
fi

# --- 1. the signed catalog ----------------------------------------------------------------
cat >"$work/catalog.py" <<'PY'
import tempfile, urllib.request
from pathlib import Path
from luma_installer import depot_catalog
from luma_installer.depot_signature import SignatureError, verify_file

with tempfile.TemporaryDirectory() as tmp:
    catalog = depot_catalog.fetch_catalog(cache=Path(tmp) / 'catalog-4.json')
if not catalog.verified or catalog.schema_version != 4:
    raise SystemExit(f'not verified: verified={catalog.verified} schema={catalog.schema_version}')
key = depot_catalog.public_key()
print(f'{depot_catalog.CATALOG_URL}: verified with {depot_catalog.PUBLIC_KEY_PATH}, '
      f'generated {catalog.generated_at}, {len(catalog.applications)} applications, {catalog.skipped} skipped')

def get(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Luma-Depot/4 gate'})
    with urllib.request.urlopen(request, timeout=20) as stream:
        return stream.read()
content = bytearray(get(depot_catalog.CATALOG_URL))
signature = get(depot_catalog.CATALOG_URL + '.minisig')
content[len(content) // 2] ^= 0x01
try:
    verify_file(key, bytes(content), signature)
except SignatureError as error:
    print(f'tampered copy refused: {error}')
else:
    raise SystemExit('a tampered catalog verified')
PY
py_check depot-catalog-signed catalog.py

# --- 2. app sources, system-wide ------------------------------------------------------------
cat >"$work/remotes.py" <<'PY'
import gi
gi.require_version('Flatpak', '1.0')
from gi.repository import Flatpak
from luma_installer import depot_flatpak

system = Flatpak.Installation.new_system(None)
remotes = {r.get_name(): r for r in system.list_remotes(None)}
problems = []
for name in ('luma', 'flathub'):
    remote = remotes.get(name)
    if remote is None:
        problems.append(f'{name}: not configured system-wide')
        continue
    try:
        depot_flatpak.validate_remote(remote, name)  # address, enabled, signature checks
    except depot_flatpak.SourceUnavailable as error:
        problems.append(f'{name}: {error}')
        continue
    if remote.get_filter():
        problems.append(f'{name}: filtered by {remote.get_filter()}')
        continue
    print(f'{name}: {remote.get_url()} enabled, gpg-verify, unfiltered')
if problems:
    raise SystemExit('; '.join(problems))
PY
py_check depot-sources-system-wide remotes.py

# --- 3. Viola is Luma's own browser, not a Flatpak to install -------------------------------
cat >"$work/viola.py" <<'PY'
import json, urllib.request
from luma_installer import depot_catalog
from luma_depot.native import NativeCatalogue

catalog = depot_catalog.fetch_catalog()
entry = next((a for a in catalog.applications if a.id == 'viola'), None)
if entry is None:
    raise SystemExit('the signed catalog has no Viola listing')
if entry.luma_system is None:
    request = urllib.request.Request(depot_catalog.CATALOG_URL, headers={'User-Agent': 'Luma-Depot/4 gate'})
    with urllib.request.urlopen(request, timeout=20) as stream:
        row = next(a for a in json.load(stream)['applications'] if a['id'] == 'viola')
    if row.get('sources', {}).get('luma_system'):
        raise SystemExit('the installed luma-application-installer predates e29fb914: it ignores '
                         "Viola's sources.luma_system (viola-browser-stable) and would offer the Flatpak")
    raise SystemExit('the Viola listing names no image package')
app = next((a for a in NativeCatalogue().snapshot().apps if a.slug == 'viola'), None)
if app is None:
    raise SystemExit('Depot does not show Viola')
state = (app.system_state, app.installable, app.availability, app.source_title)
print(f'Viola: system_state={app.system_state} installable={app.installable} '
      f'availability={app.availability!r} source={app.source_title!r} package={entry.luma_system.package}')
if app.system_state != 'installed' or app.installable or app.source_title != 'Luma':
    raise SystemExit(f'Viola is not shown as included with Luma: {state}')
PY
py_check depot-viola-included-with-luma viola.py

# --- 4. install and uninstall through Depot's path -------------------------------------------
# Each install runs Depot's resolve -> transaction (commit and remote checks) -> run,
# then removal_transaction, exactly as the window does.
install_check() {
  local name=$1 app_id=$2
  cat >"$work/$name.py" <<PY
import gi
gi.require_version('Flatpak', '1.0')
from luma_installer import depot_flatpak, depot_inventory

app_id = '$app_id'
installation, remote = depot_flatpak.installation_for(app_id)
source = depot_flatpak.resolve(installation, app_id, 'x86_64')
original = depot_flatpak.installed_ref(installation, source)
original_identity = None
if original is not None:
    original_identity = (original.format_ref(), original.get_commit(), original.get_origin())
    # Resolve and verify the signed source before changing a bundled application.
    # Refuse to replace an older installed commit merely to exercise this fixture.
    if original_identity != (source.ref, source.commit, remote):
        raise SystemExit(f'{app_id}: installed ref differs from the signed source; leaving it untouched')
try:
    if original is not None:
        depot_flatpak.removal_transaction(installation, original).run(None)
    if depot_flatpak.installed_ref(installation, source) is not None:
        raise SystemExit(f'{app_id}: clean-install setup failed')
    tx = depot_flatpak.transaction(installation, source)
    if tx is None:
        raise SystemExit(f'{app_id}: clean-install setup unexpectedly retained the app')
    tx.run(None)
    ref = depot_flatpak.installed_ref(installation, source)
    if ref is None or ref.get_commit() != source.commit or ref.get_origin() != remote:
        raise SystemExit(f'{app_id} is not installed at {source.commit[:12]} from {remote}')
    print(f'installed {source.ref} {source.commit[:12]} from {remote} ({installation.get_id()})')
    depot_flatpak.removal_transaction(installation, ref).run(None)
    if depot_flatpak.installed_ref(installation, source) is not None:
        raise SystemExit(f'{app_id} is still installed after removal')
    print(f'removed {app_id}')
finally:
    if original_identity is not None:
        restore = depot_flatpak.transaction(installation, source)
        if restore is not None:
            restore.run(None)
        restored = depot_flatpak.installed_ref(installation, source)
        identity = None if restored is None else (restored.format_ref(), restored.get_commit(), restored.get_origin())
        if identity != original_identity:
            raise SystemExit(f'{app_id}: failed to restore the original signed ref')
        print(f'restored original {identity[0]} {identity[1]} from {identity[2]}')
PY
  py_check "$name" "$name.py"
}
if [ "$quick" = 1 ]; then
  emit depot-install-luma-source skip "--quick"
  emit depot-install-flathub skip "--quick"
else
  # Notes: a 330 kB app on the Luma remote (org.projectluma.Platform//44) that
  # the catalog lists. Sticky Notes was withdrawn from the catalog on
  # 2026-09-18, so Depot rightly no longer installs it.
  install_check depot-install-luma-source org.projectluma.Notes
  # Transmission: a 6 MB Flathub app in Depot's catalog (org.gnome.Platform//50).
  install_check depot-install-flathub com.transmissionbt.Transmission
fi

exit "$failed"
