#!/bin/bash
# lumaui-conform: (re)build the capture images on the build server, in the service's own
# container storage on the data disk (never the host's nearly full root filesystem), through
# the host's luma-heavy queue. Run on the server by install.sh --image / --cbuild:
#   build-images.sh [thinkpad] [cbuild]
# thinkpad  localhost/lumaui-conform-thinkpad: the shipped GTK stack. Luma packages at the NEVRAs
#           of the newest nightly's installed manifest (LUMAUI_CONFORM_SHIP_MANIFEST), from that
#           nightly's pool (LUMAUI_CONFORM_POOL); every other package converged on the same
#           manifest (pin.sh). The ThinkPad's package set and icon/MIME share stay as last copied
#           to /srv/lumaui-conform/image (install.sh copies new ones when the ThinkPad is up).
# cbuild    localhost/lumaui-conform-cbuild on top: toolchain, -devel headers, the harness preload,
#           and the runtime dependencies of the patched-upstream apps at the same nightly.
# The previous build of each image is removed once the new one is tagged; nothing else is pruned.
set -euo pipefail
umask 022
root=/srv/lumaui-conform
data=${LUMAUI_CONFORM_DATA:-/srv/dev-storage/projects/lumaui-conform}
store=$data/containers/storage
tools=$root/tools/lumaui-conform
podman() { command podman --root "$store" --runroot /run/lumaui-conform/containers "$@"; }
heavy=(); command -v luma-heavy >/dev/null 2>&1 && heavy=(luma-heavy)
mountpoint -q "$(df --output=target "$(dirname "$data")" | tail -1)" || { echo "build-images: $data is not on a mounted data disk" >&2; exit 3; }
mkdir -p "$store" "$data/build"
# The data disk's project tree carries default ACLs; container storage must not inherit them.
setfacl -R -b -k "$data/containers" 2>/dev/null || true

manifest=${LUMAUI_CONFORM_SHIP_MANIFEST:-$(ls -t /srv/luma-build/nightly-*/pipeline/builds/*/packages-installed.tsv 2>/dev/null | head -1)}
[ -f "$manifest" ] || { echo "build-images: no nightly manifest (LUMAUI_CONFORM_SHIP_MANIFEST)" >&2; exit 3; }
pool=${LUMAUI_CONFORM_POOL:-$(dirname "$(dirname "$(dirname "$manifest")")")/rpms/pool}
[ -d "$pool" ] || { echo "build-images: no pool at $pool (LUMAUI_CONFORM_POOL)" >&2; exit 3; }
echo "build-images: manifest $manifest, pool $pool"
nevra() {  # nevra <name>: name-version-release.arch from the manifest, or nothing
  awk -F'\t' -v n="$1" '$1 == n {print $1 "-" $3 "-" $4 "." $5; exit}' "$manifest"
}
fetch() {  # fetch <dest dir> <names...>: each package's shipped RPM from the pool
  local dest=$1 n v f; shift
  mkdir -p "$dest"; rm -f "$dest"/*.rpm
  for n in "$@"; do
    v=$(nevra "$n")
    [ -n "$v" ] || { echo "  $n: not in the nightly; skipped"; continue; }
    f=$pool/$v.rpm
    [ -f "$f" ] || { echo "  $v: shipped from Fedora (not in the Luma pool)"; continue; }
    cp -p "$f" "$dest/"
  done
}
build() {  # build <dir> <tag>
  local dir=$1 tag=$2 old new
  old=$(podman image inspect -f '{{.Id}}' "$tag" 2>/dev/null || true)
  if ! "${heavy[@]}" podman --root "$store" --runroot /run/lumaui-conform/containers build --pull=missing --layers=false \
      -t "$tag" "$dir" > "$dir/build.log" 2>&1; then
    tail -40 "$dir/build.log" >&2; exit 4
  fi
  grep -E "^packages:|cannot pin|headers without|not the shipped" "$dir/build.log" || true
  new=$(podman image inspect -f '{{.Id}}' "$tag")
  [ -n "$old" ] && [ "$old" != "$new" ] && podman rmi "$old" >/dev/null 2>&1 || true
  podman images "$tag"
}

for what in "$@"; do
  case "$what" in
    thinkpad)
      ctx=$data/build/image
      mkdir -p "$ctx"
      cp "$tools/server/Containerfile" "$tools/server/pin.sh" "$ctx/"
      for f in thinkpad-rpmqa.txt thinkpad-share.tar; do
        [ -f "$root/image/$f" ] || { echo "build-images: $root/image/$f is missing (install.sh --image with the ThinkPad up)" >&2; exit 3; }
        cp -p "$root/image/$f" "$ctx/"
      done
      fetch "$ctx/rpms" gtk4 libadwaita luma-developer-platform luma-developer-platform-sdk prairie-core-apps prairie-icon-theme \
        google-figtree-fonts google-caveat-fonts mutter mutter-common luma-shell-state gtk3 gnome-settings-daemon \
        gnome-control-center-filesystem ibus-libs libhandy gnome-calculator fwupd nautilus-extensions luma-stage-preview
      awk -F'\t' '{print $1, $1 "-" $3 "-" $4 "." $5}' "$manifest" | sort > "$ctx/ship-rpmqa.txt"
      echo "$manifest" > "$ctx/SHIP-MANIFEST"
      build "$ctx" localhost/lumaui-conform-thinkpad ;;
    cbuild)
      ctx=$data/build/image-cbuild
      mkdir -p "$ctx/cbuild/rpms-dev"
      cp "$tools/server/Containerfile.cbuild" "$ctx/Containerfile"
      cp "$tools/server/pin-cbuild.sh" "$tools/server/preload.c" "$tools/server/cbuild-devel.txt" "$ctx/cbuild/"
      rm -f "$ctx/cbuild/rpms-dev"/*.rpm
      fetch "$ctx/cbuild/rpms-app" nautilus nautilus-extensions gnome-control-center
      build "$ctx" localhost/lumaui-conform-cbuild ;;
    *) echo "build-images: thinkpad or cbuild, not $what" >&2; exit 2 ;;
  esac
done
