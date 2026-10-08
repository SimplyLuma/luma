#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build one app from its manifest, lint it, and compute its permissions.
#
#   build-app.sh packaging/flatpak/apps/<app-id>/<app-id>.yml [BRANCH]
#
# BRANCH defaults to beta. The build runs in the tools container against the
# org.projectluma.Sdk installed from the local remote, so it links against the
# exact runtime that is published. Results under $DEPOT_ROOT/out/apps/<app-id>/:
#
#   repo/                 archive repo holding app/<app-id>/<arch>/<BRANCH>
#   metadata              the exported Flatpak metadata
#   permissions.json      ADR-028 section 7 permissions (flatpak-permissions.py)
#   lint-*.json           flatpak-builder-lint results; appstream.txt
#   build.log, SOURCES    the log and the exact source commits used
#
# Sources: a manifest names its upstream git URL and commit. When
# $DEPOT_ROOT/sources/mirrors/<repo>.git exists (see prepare-sources.sh) the
# commit is taken from that mirror; when sources/archives/<repo>-<commit>.tar.gz
# exists it is used with its sha256. The commit id is never changed, so what
# is built is what the manifest names.
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"

manifest=${1:?usage: build-app.sh MANIFEST [BRANCH]}
branch=${2:-beta}
case "$branch" in stable|beta|nightly) ;; *) depot_die "branch must be stable, beta or nightly" ;; esac
case "$manifest" in
  /*) ;;
  *) manifest="$depot_repo_root/$manifest" ;;
esac
[ -f "$manifest" ] || depot_die "no manifest at $manifest"
manifest=$(realpath "$manifest")
app_id=$(basename "$manifest" .yml)
out="$depot_out/apps/$app_id"
work="$DEPOT_ROOT/work/apps/$app_id"

"$(dirname -- "$0")/install-build-runtime.sh"

depot_in_tools rm -rf "$work" "$out"
depot_in_tools install -d "$work" "$out"

depot_log "resolving sources for $app_id"
depot_in_tools python3 - "$manifest" "$work/manifest.json" \
  "$DEPOT_ROOT/sources" "$out/SOURCES" <<'PY'
import hashlib, json, sys
from pathlib import Path
import yaml

manifest_path, output, sources_root, record = map(Path, sys.argv[1:])
doc = yaml.safe_load(manifest_path.read_text())
lines = []

def resolve(module):
    for source in module.get("sources", []):
        if isinstance(source, str):
            continue
        if source.get("type") in ("file", "patch", "dir") and "path" in source:
            source["path"] = str((manifest_path.parent / source["path"]).resolve())
        if source.get("type") == "archive" and "path" in source:
            archive = (manifest_path.parent / source["path"]).resolve()
            wanted = source.get("sha256", "")
            if len(wanted) != 64 or any(c not in "0123456789abcdef" for c in wanted):
                raise SystemExit(f"{archive}: local archives require an exact SHA256")
            actual = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
            if actual != wanted:
                raise SystemExit(f"{archive}: source archive digest mismatch")
            source["path"] = str(archive)
            lines.append(f"sealed archive sha256={wanted} path={archive}")
        if source.get("type") != "git":
            continue
        url, commit = source["url"], source.get("commit")
        if not commit or len(commit) != 40:
            raise SystemExit(f"{url}: sources must pin a full 40-character commit")
        name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        mirror = sources_root / "mirrors" / f"{name}.git"
        archive = sources_root / "archives" / f"{name}-{commit}.tar.gz"
        if mirror.is_dir():
            source["url"] = f"file://{mirror}"
            lines.append(f"{url} {commit} mirror")
        elif archive.is_file():
            sha = hashlib.sha256(archive.read_bytes()).hexdigest()
            source.clear()
            source.update({"type": "archive", "path": str(archive), "sha256": sha})
            lines.append(f"{url} {commit} archive sha256={sha}")
        else:
            lines.append(f"{url} {commit} network")
    for child in module.get("modules", []):
        if isinstance(child, dict):
            resolve(child)

for module in doc.get("modules", []):
    if isinstance(module, dict):
        if not module.get("sources"):
            raise SystemExit("an application module has no sealed source inputs")
        resolve(module)
output.write_text(json.dumps(doc, indent=2))
record.write_text("\n".join(lines) + "\n")
PY

# Private builder override for large filesystems: preserve an absolute 10 GiB
# reserve rather than libostree's default 3% of an unrelated terabyte volume.
# This configures only this newly generated unsigned output repo; signer,
# public repository and installed clients keep their own policies unchanged.
if [ -n "${DEPOT_BUILD_MIN_FREE_BYTES:-}" ]; then
  [ "$DEPOT_BUILD_MIN_FREE_BYTES" = 10737418240 ] || depot_die "private builder reserve must be exactly 10 GiB"
  depot_in_tools python3 - "$out" "$DEPOT_BUILD_MIN_FREE_BYTES" <<'PYFREE'
import os,sys
v=os.statvfs(sys.argv[1]); available=v.f_bavail*v.f_frsize
if available < int(sys.argv[2]): raise SystemExit('Private app builder lacks its 10 GiB physical reserve')
print('Private app builder physical bytes available:',available)
PYFREE
  depot_in_tools ostree --repo="$out/repo" init --mode=archive-z2
  depot_in_tools ostree --repo="$out/repo" config set core.min-free-space-percent 0
  # libostree accepts whole MB/GB/TB strings, not raw bytes. Round up so
  # either binary or decimal GB interpretation preserves at least 10 GiB.
  depot_in_tools ostree --repo="$out/repo" config set core.min-free-space-size 11GB
  depot_in_tools ostree --repo="$out/repo" config get core.min-free-space-percent
  depot_in_tools ostree --repo="$out/repo" config get core.min-free-space-size
fi

depot_log "building $app_id ($branch)"
# The maintained offline catalogue is composed by the keyless build tools.
# Glycin's normal bwrap sandbox runs here; an SDK build sandbox has no desktop
# portal available for nested flatpak-spawn image decoding.
offline_compose=$(depot_in_tools python3 -c 'import json,sys; print("yes" if json.load(open(sys.argv[1])).get("appstream-compose") is False else "no")' "$work/manifest.json")
if [ "$offline_compose" = yes ]; then
  depot_in_tools flatpak-builder --user --disable-rofiles-fuse --force-clean --build-only \
    --state-dir="$DEPOT_ROOT/work/flatpak-builder-state" --default-branch="$branch" \
    "$work/build" "$work/manifest.json" >"$out/build.log" 2>&1 ||
    { tail -n 60 "$out/build.log" >&2; depot_die "flatpak-builder build phase failed for $app_id"; }
  depot_in_tools appstreamcli compose --no-net --prefix=/ --origin="$app_id" \
    --media-baseurl="$DEPOT_PUBLIC_URL/media" --result-root="$work/build/files" \
    --data-dir="$work/build/files/share/app-info/xmls" \
    --icons-dir="$work/build/files/share/app-info/icons/flatpak" \
    --components="$app_id" "$work/build/files" >>"$out/build.log" 2>&1 ||
    { tail -n 60 "$out/build.log" >&2; depot_die "offline AppStream composition failed for $app_id"; }
  depot_in_tools flatpak-builder --user --disable-rofiles-fuse --finish-only \
    --state-dir="$DEPOT_ROOT/work/flatpak-builder-state" --default-branch="$branch" \
    --repo="$out/repo" "$work/build" "$work/manifest.json" >>"$out/build.log" 2>&1 ||
    { tail -n 60 "$out/build.log" >&2; depot_die "flatpak-builder finish phase failed for $app_id"; }
else
  if ! depot_in_tools flatpak-builder --user --disable-rofiles-fuse --force-clean \
      --state-dir="$DEPOT_ROOT/work/flatpak-builder-state" \
      --default-branch="$branch" --repo="$out/repo" \
      "$work/build" "$work/manifest.json" >"$out/build.log" 2>&1; then
    tail -n 60 "$out/build.log" >&2
    depot_die "flatpak-builder failed for $app_id; see $out/build.log"
  fi
fi
if [ -n "${DEPOT_BUILD_MIN_FREE_BYTES:-}" ]; then
  depot_in_tools python3 - "$out/repo" "$DEPOT_BUILD_MIN_FREE_BYTES" <<'PYFREE'
import os,sys
v=os.statvfs(sys.argv[1]); available=v.f_bavail*v.f_frsize
if available < int(sys.argv[2]): raise SystemExit('Private app export consumed its 10 GiB physical reserve')
print('Private app export physical bytes remaining:',available)
PYFREE
fi
depot_in_tools cp "$work/build/metadata" "$out/metadata"

depot_log "validating AppStream data"
metainfo="$work/build/files/share/metainfo/$app_id.metainfo.xml"
depot_in_tools sh -c "appstreamcli validate --no-net --explain '$metainfo' >'$out/appstream.txt' 2>&1" ||
  { cat "$out/appstream.txt" >&2; depot_die "appstreamcli validate failed for $app_id"; }
depot_in_tools desktop-file-validate "$work/build/files/share/applications/$app_id.desktop"

depot_log "linting with flatpak-builder-lint"
# The committed manifest is linted (the build copy points sources at local mirrors).
"$(dirname -- "$0")/lint-app.sh" "$app_id" "$manifest" "$work/build" "$out/repo" "$out"

depot_log "computing permissions"
depot_in_tools python3 -B scripts/depot/flatpak-permissions.py "$out/metadata" >"$out/permissions.json"
cat "$out/permissions.json"
depot_in_tools rm -rf "$work/build"
depot_log "$app_id built: $(du -sh "$out/repo" | cut -f1) in $out"
