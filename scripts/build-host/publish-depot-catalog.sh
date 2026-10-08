#!/usr/bin/env bash
# Publish the Depot catalogue to the Recent HTTPS host.
#
# The catalogue is served beside the OSTree repository so that adding an
# application is a file publication rather than a package rebuild and a
# reboot. It is validated before it is published and written atomically: a
# half-written catalogue is never the live one.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source_catalog="${1:-$repo_root/src/luma-installer/data/depot-catalog.json}"
webroot="${LUMA_DEPOT_WEBROOT:-/srv/luma-build/updates/webroot/depot}"

test -f "$source_catalog" || {
  printf 'error: no catalogue at %s\n' "$source_catalog" >&2
  exit 1
}

# Refuse to publish anything the client would reject. The validator is the
# same one the client runs, so this cannot drift from what is accepted.
PYTHONPATH="$repo_root/src/luma-installer" python3 - "$source_catalog" <<'PY'
import sys
from luma_installer.depot_catalog import load_catalog
catalog = load_catalog(sys.argv[1])
print(f'  {len(catalog.applications)} applications validated')
PY

install -d -m 0755 "$webroot"
tmp="$(mktemp "$webroot/.catalog.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
install -m 0644 "$source_catalog" "$tmp"
mv -f "$tmp" "$webroot/catalog.json"
trap - EXIT

printf '  published %s\n' "$webroot/catalog.json"
printf '  reachable by enrolled devices at https://127.0.0.1:8443/luma/depot/catalog.json\n'
