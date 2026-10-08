#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Build the Atlas installer front end RPM (luma-installer-atlas) on the
# canonical Linux/x86_64 builder. The web bundle is built inside the Fedora 44
# builder from src/luma-installer-atlas with npm ci (package-lock.json pins
# every npm input) and the verified Cockpit/Figtree inputs, the unit tests run,
# and the packaged tree is checked before the RPM is accepted.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in mktemp podman; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Atlas package build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build the Atlas RPM on the canonical Linux/x86_64 builder\n' >&2
  exit 1
fi

source_dir="$repo_root/src/luma-installer-atlas"
output_dir="$repo_root/build/packages/luma-installer-atlas"
mkdir -p "$output_dir"
work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
keep_work=${LUMA_ATLAS_BUILD_KEEP_WORK:-0}
case "$keep_work" in
  0|1) ;;
  *) printf 'error: LUMA_ATLAS_BUILD_KEEP_WORK must be 0 or 1\n' >&2; exit 2 ;;
esac
# Release qualification retains the actual compiler inputs and generated dist,
# rather than comparing a separately reconstructed source/bundle afterwards.
if [ "$keep_work" = 1 ]; then
  trap 'printf "Atlas retained producer stage: %s\n" "$work_dir"' EXIT
else
  trap 'rm -rf "$work_dir"' EXIT
fi
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$work_dir/source"

# Only the files the package is built from; never node_modules or local output.
tar -C "$source_dir" \
  --exclude=./node_modules --exclude=./dist --exclude=./dist-preview \
  --exclude=./.build-inputs --exclude=./test/visual/output --exclude=./test/visual/node_modules \
  -cf - . | tar -C "$work_dir/source" -xf -
mkdir -p "$work_dir/source/config-desktop"
mkdir -p "$work_dir/source/brand"
install -m 0644 "$repo_root/website/public/brand/luma-wordmark.svg" "$work_dir/source/brand/luma-wordmark.svg"
install -m 0644 "$repo_root/config/desktop/inputs.env" "$work_dir/source/config-desktop/inputs.env"

for source in atlas-probe atlas-media atlas-viewer atlas-viewer-fallback; do
  install -m 0755 "$source_dir/libexec/$source" "$rpmbuild_dir/SOURCES/$source"
done
install -m 0644 "$source_dir/config/90-luma-atlas.conf" "$rpmbuild_dir/SOURCES/"
install -m 0755 "$source_dir/runtime/webui-desktop" "$rpmbuild_dir/SOURCES/"
install -m 0755 "$source_dir/runtime/cockpit-coproc-wrapper.sh" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$source_dir/runtime/webui-cockpit-ws.service" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$source_dir/upstream/LICENSE.anaconda-webui" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$source_dir/test/python/test_atlas_media.py" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$source_dir/README.md" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$source_dir/src/data/app-collections.json" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/packaging/rpm/luma-installer-atlas.spec" "$rpmbuild_dir/SPECS/"

export LUMA_RPM_BUILDER_NAME=${LUMA_RPM_BUILDER_NAME:-luma-atlas-rpm-builder-f44}

"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$work_dir" "$FEDORA_RPM_BUILD_CONTAINER" '
  set -euo pipefail
  dnf5 -y install --setopt=install_weak_deps=False nodejs npm git-core curl python3 rpm-build systemd-rpm-macros cpio >/dev/null
  cd source
  # fetch-build-inputs.sh reads the repository pins; point it at the copy.
  ATLAS_DESKTOP_INPUTS=$PWD/config-desktop/inputs.env ./fetch-build-inputs.sh
  npm ci --no-audit --no-fund --ignore-scripts
  node node_modules/esbuild/install.js
  node --test test/unit/*.test.js
  python3 -m unittest discover -s test/python -v
  bash test/viewer/test-atlas-viewer.sh libexec/atlas-viewer
  NODE_ENV=production node build.js
  test -f dist/index.html && test -f dist/index.js && test -f dist/index.css && test -f dist/fonts/Figtree.ttf && test -s dist/loader-background.svg
  if grep -R -l "atlas-mock-backend\|TEST FIXTURE" dist/; then
    echo "error: the mocked backend leaked into the product bundle" >&2
    exit 1
  fi
  tar -cf ../rpmbuild/SOURCES/luma-installer-atlas-dist.tar dist
  cd ../rpmbuild
  rpmbuild -ba --define "_topdir $PWD" SPECS/luma-installer-atlas.spec
  rpm=$(find RPMS/noarch -name "luma-installer-atlas-*.rpm" -print -quit)
  verify=$(mktemp -d)
  (cd "$verify" && rpm2cpio "$OLDPWD/$rpm" | cpio -idm --quiet)
  test -f "$verify/usr/share/cockpit/anaconda-webui/index.html"
  test -f "$verify/usr/share/cockpit/anaconda-webui/manifest.json"
  test -x "$verify/usr/libexec/luma-installer-atlas/atlas-probe"
  test -x "$verify/usr/libexec/luma-installer-atlas/atlas-media"
  test -x "$verify/usr/libexec/luma-installer-atlas/atlas-viewer"
  test -x "$verify/usr/libexec/luma-installer-atlas/atlas-viewer-fallback"
  grep -q atlas-viewer "$verify/usr/libexec/anaconda/webui-desktop"
  grep -q viewer-heartbeat "$verify/usr/share/cockpit/anaconda-webui/index.js"
  test -x "$verify/usr/libexec/anaconda/webui-desktop"
  test -f "$verify/etc/anaconda/conf.d/90-luma-atlas.conf"
  python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert [c[\"id\"] for c in d[\"collections\"]]" "$verify/usr/share/luma-installer-atlas/app-collections.json"
  rpm -qp --provides "$rpm" | grep -qx "anaconda-webui = 68"
  rm -rf "$verify"
'

find "$rpmbuild_dir/RPMS" "$rpmbuild_dir/SRPMS" -name '*.rpm' -exec cp -f {} "$output_dir/" \;
( cd "$output_dir" && sha256sum ./*.rpm > SHA256SUMS )
printf 'Atlas RPMs written to %s\n' "$output_dir"
cat "$output_dir/SHA256SUMS"
