#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in mktemp podman sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Luma Search package build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build the Luma Search RPM on the canonical Linux/x86_64 builder\n' >&2
  exit 1
fi

output_dir="$repo_root/build/packages/luma-search"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

for source in \
  luma-search-service \
  org.projectluma.Search1.xml \
  org.projectluma.Search.service \
  org.projectluma.search.gschema.xml \
  luma-search-settings \
  org.projectluma.SearchSettings.desktop \
  50_luma-search-localsearch.gschema.override \
  README.md; do
  install -m 0644 "$repo_root/src/luma-search/$source" "$rpmbuild_dir/SOURCES/$source"
done
chmod 0755 "$rpmbuild_dir/SOURCES/luma-search-service"
chmod 0755 "$rpmbuild_dir/SOURCES/luma-search-settings"
for source in __init__.py contract.py providers.py ranking.py files.py places.py; do
  install -m 0644 "$repo_root/src/luma-search/luma_search/$source" \
    "$rpmbuild_dir/SOURCES/$source"
done
install -m 0644 "$repo_root/LICENSE.md" "$rpmbuild_dir/SOURCES/LICENSE.md"
install -m 0644 "$repo_root/tests/unit/test_luma_search.py" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/tests/search/test_ranking_vectors.py" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/tests/search/ranking-vectors.json" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/tests/search/settings-cases.json" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/src/luma-search/settings-pages.json" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/src/luma-search/filer-places.json" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/packaging/rpm/luma-search.spec" "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$rpmbuild_dir" "$FEDORA_RPM_BUILD_CONTAINER" '
  set -euo pipefail
  dnf5 -y install glib2-devel python3 python3-gobject-base rpm-build systemd-rpm-macros localsearch libtinysparql
  schemas=$(mktemp -d)
  cp SOURCES/org.projectluma.search.gschema.xml "$schemas/"
  glib-compile-schemas --strict --dry-run "$schemas"
  # The LocalSearch override must name real keys of the installed schema.
  cp /usr/share/glib-2.0/schemas/org.freedesktop.Tracker3.Miner.Files.gschema.xml \
    SOURCES/50_luma-search-localsearch.gschema.override "$schemas/"
  glib-compile-schemas --strict --targetdir="$schemas" "$schemas"
  GSETTINGS_SCHEMA_DIR="$schemas" GSETTINGS_BACKEND=memory \
    gsettings get org.freedesktop.Tracker3.Miner.Files ignored-directories >"$schemas/ignored.txt"
  grep -Fq node_modules "$schemas/ignored.txt"
  python3 -m py_compile SOURCES/luma-search-service SOURCES/luma-search-settings SOURCES/*.py
  rpmbuild -ba --define "_topdir $PWD" SPECS/luma-search.spec
  rpm=$(find RPMS/noarch -name "luma-search-*.rpm" -print -quit)
  verify=$(mktemp -d)
  cd "$verify"
  # Files, not a pipe: cpio stops at the trailer and pipefail would see SIGPIPE.
  rpm2cpio "$OLDPWD/$rpm" >package.cpio
  cpio -idm --quiet <package.cpio
  rm package.cpio
  test -x usr/libexec/luma-search-service
  test -x usr/libexec/luma-search-settings
  test -f usr/share/applications/org.projectluma.SearchSettings.desktop
  test -f usr/share/dbus-1/services/org.projectluma.Search.service
  test -f usr/share/dbus-1/interfaces/org.projectluma.Search1.xml
  test -f usr/share/glib-2.0/schemas/org.projectluma.search.gschema.xml
  grep -Fq "Exec=/usr/libexec/luma-search-service" \
    usr/share/dbus-1/services/org.projectluma.Search.service
  grep -Fq "/usr/share/dbus-1/interfaces/org.projectluma.Search1.xml" \
    usr/libexec/luma-search-service
  grep -Fq "Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES" \
    usr/libexec/luma-search-service
  grep -Fq "Gio.DBusProxyFlags.DO_NOT_AUTO_START_AT_CONSTRUCTION" \
    usr/libexec/luma-search-service
  grep -Fq "PROVIDER_QUERY_BUDGET_SECONDS = 0.18" \
    usr/libexec/luma-search-service
  test -f usr/libexec/luma_search/ranking.py
  test -f usr/libexec/luma_search/files.py
  test -s usr/share/luma-search/settings-pages.json
  grep -Fq "_query_settings_pages" usr/libexec/luma-search-service
  test -f usr/share/glib-2.0/schemas/50_luma-search-localsearch.gschema.override
  grep -Fq "_query_files" usr/libexec/luma-search-service
  test -f usr/libexec/luma_search/places.py
  python3 -c "import json,sys; t=json.load(open(sys.argv[1])); ids={p[\"id\"] for p in t[\"places\"]}; sys.exit(0 if {\"applications\",\"trash\",\"home\"} <= ids and len(ids) >= 13 else 1)" usr/share/luma-search/filer-places.json
  grep -Fq "_query_places" usr/libexec/luma-search-service
  grep -Fq "<method name=\"Reveal\">" usr/share/dbus-1/interfaces/org.projectluma.Search1.xml
  rpm -qp --requires "$OLDPWD/$rpm" >requires.txt
  grep -Fqx libtinysparql requires.txt
  printf "Packaged Luma Search files and folders: PASS\n"
'

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma Search package: %s\n' "$output_dir"
