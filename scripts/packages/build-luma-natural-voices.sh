#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Exact optional native voice package. Never registers/publishes a Depot entry.
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo_root/config/desktop/inputs.env"
output_dir="$repo_root/build/packages/luma-natural-voices"
mkdir -p "$repo_root/build"
work_dir=$(mktemp -d "$repo_root/build/luma-natural-voices.work.XXXXXX")
chmod 0755 "$work_dir"
trap 'rm -rf "$work_dir"' EXIT INT TERM
mkdir -p "$work_dir/rpmbuild"/{SOURCES,SPECS,RPMS,SRPMS,platform}
platform_rpm=${LUMA_PLATFORM_RPM:?exact qualified platform RPM required}
test -f "$platform_rpm"
cp "$platform_rpm" "$work_dir/rpmbuild/platform/"
for pattern in luma-developer-platform-devel-*.rpm luma-developer-platform-sdk-*.rpm; do
 for artifact in "$(dirname "$platform_rpm")"/$pattern; do
  [ ! -f "$artifact" ] || cp "$artifact" "$work_dir/rpmbuild/platform/"
 done
done
source_date_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}
# Pinned upstream/model sources are verified before any RPM build. An unknown
# or changed upstream response cannot become an installable component.
python3 - "$repo_root" "$work_dir/rpmbuild/SOURCES" <<'PY'
import hashlib,json,pathlib,sys,urllib.request
root=pathlib.Path(sys.argv[1]); dest=pathlib.Path(sys.argv[2])
for name,item in json.loads((root/'src/luma-natural-voices/sources.json').read_text()).items():
    with urllib.request.urlopen(item['url'],timeout=120) as response:
        data=response.read()
    actual=hashlib.sha256(data).hexdigest()
    if actual != item['sha256']: raise SystemExit(f'Pinned natural voice source differs: {name}')
    (dest/name).write_bytes(data)
    print(f'Pinned source PASS {name} {actual}')
PY
mkdir -p "$work_dir/luma-natural-voices"
cp -R "$repo_root/src/luma-natural-voices/." "$work_dir/luma-natural-voices/"
cp "$repo_root/LICENSE.md" "$work_dir/luma-natural-voices/LICENSE.md"
find "$work_dir/luma-natural-voices" -type d -name __pycache__ -prune -exec rm -rf {} +
tar -C "$work_dir" --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 --numeric-owner \
 -cf - luma-natural-voices | gzip -n > "$work_dir/rpmbuild/SOURCES/luma-natural-voices.tar.gz"
cp "$repo_root/packaging/rpm/luma-natural-voices.spec" "$work_dir/rpmbuild/SPECS/"
"$repo_root/scripts/packages/run-in-rpm-builder.sh" "$work_dir/rpmbuild" "$FEDORA_RPM_BUILD_CONTAINER" '
 set -euo pipefail
 dnf -y install platform/*.rpm rpm-build dnf5-plugins
 dnf builddep -y SPECS/luma-natural-voices.spec
 id conform >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash conform
 work=$PWD
 chown -R conform:conform "$work"
 runuser -u conform -- rpmbuild -ba --define "_topdir $work" --define "_smp_mflags -j2" SPECS/luma-natural-voices.spec
'
install -d "$output_dir"
cp -R "$work_dir/rpmbuild/RPMS" "$work_dir/rpmbuild/SRPMS" "$output_dir/"
(cd "$output_dir" && find RPMS SRPMS -name '*.rpm' -type f -exec sha256sum {} + | sort > SHA256SUMS)
printf 'Natural voices packages: %s\n' "$output_dir"
