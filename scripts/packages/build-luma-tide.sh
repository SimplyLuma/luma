#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in mktemp podman sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Tide build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ "$(uname -s)" = Linux ] || {
  printf 'error: build the architecture-independent Tide RPM on a Fedora Linux builder\n' >&2
  exit 1
}

architecture=${LUMA_TARGET_ARCHITECTURE:-$(uname -m)}
case "$architecture" in
  x86_64) builder_container=$FEDORA_RPM_BUILD_CONTAINER ;;
  aarch64) builder_container=$FEDORA_RPM_BUILD_CONTAINER_AARCH64 ;;
  *) printf 'error: unsupported Tide build architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
export LUMA_RPM_BUILDER_ARCHITECTURE=$architecture

# The candidate must be compiled and exercised against one known AppKit ABI,
# not whatever happens to be built: that is why this is an exact release and
# not the spec's floor (>= 0.1.0-1.luma.47~preview.20260906.1). The release is
# the one this architecture pins in config/desktop/inputs.env, so it advances
# deliberately with the tuple instead of staying frozen in this script.
#
# It was held at exactly luma.62 to keep the candidate on the Settings 14
# AppKit ABI while the graphical gates were outstanding. Those gates now run
# in the nightly (the release's own image contract and desktop smoke, passing
# on 20260915.13 and .14) and the image ships platform luma.65, so the hold is
# stale: it only made every later platform a hard failure here for no reason
# the package requires.
case "$architecture" in
  x86_64) platform_nevra=$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA ;;
  aarch64) platform_nevra=$LUMA_DEVELOPER_PLATFORM_AARCH64_NEVRA ;;
esac
[ -n "${platform_nevra:-}" ] || {
  printf 'error: config/desktop/inputs.env pins no Luma Developer Platform for %s\n' "$architecture" >&2
  exit 1
}
platform_rpm_dir="$repo_root/build/packages/luma-developer-platform/$architecture/RPMS"
[ -d "$platform_rpm_dir" ] || {
  printf 'error: the Luma Developer Platform output directory is missing for %s: %s\n' \
    "$architecture" "$platform_rpm_dir" >&2
  printf 'build %s before the Tide surface candidate\n' "$platform_nevra" >&2
  exit 1
}
platform_rpm=$(find "$platform_rpm_dir" -maxdepth 1 -type f \
  -name "$platform_nevra.rpm" -print -quit)
if [ -z "$platform_rpm" ]; then
  printf 'error: build %s for %s before the Tide surface candidate\n' \
    "$platform_nevra" "$architecture" >&2
  printf 'that is the release config/desktop/inputs.env pins; this build uses no other\n' >&2
  exit 1
fi
platform_rpm_name=$(basename "$platform_rpm")

output_dir="$repo_root/build/packages/luma-tide"
mkdir -p "$(dirname "$output_dir")"
work_dir=$(mktemp -d "$output_dir.work.XXXXXX")
cleanup_workdir() {
  if [ -n "${LUMA_RPM_BUILDER_EXISTING:-}" ]; then
    podman exec --user root "$LUMA_RPM_BUILDER_EXISTING" rm -rf "$work_dir"
  else
    rm -rf "$work_dir"
  fi
}
trap cleanup_workdir EXIT INT TERM
rpmbuild_dir="$work_dir/rpmbuild"
source_dir="$work_dir/luma-tide"
mkdir -p "$rpmbuild_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS} "$source_dir"
tar -C "$repo_root/src/luma-tide" --exclude='./_build' -cf - . | tar -C "$source_dir" -xf -
mkdir -p "$source_dir/tests" && cp -R "$repo_root/tests/luma-tide/." "$source_dir/tests/"
mkdir -p "$source_dir/fixtures"
cp "$repo_root/tests/fixtures/tide-v70.json" "$source_dir/fixtures/"
find "$source_dir" -type d -name __pycache__ -prune -exec rm -rf {} +
source_date_epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" log -1 --format=%ct HEAD)}
tar -C "$work_dir" --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 \
  --numeric-owner -cf - luma-tide | gzip -n >"$rpmbuild_dir/SOURCES/luma-tide.tar.gz"
install -m 0644 "$repo_root/LICENSE.md" "$rpmbuild_dir/SOURCES/LICENSE.md"
install -m 0644 "$repo_root/src/luma-shell-state/org.project_luma.shell-state.gschema.xml" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$repo_root/packaging/rpm/luma-tide.spec" "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" "$builder_container" "
    set -euo pipefail
    export ATSPI_DBUS_IMPLEMENTATION=dbus-daemon
    export GDK_DEBUG=no-portals
    export XDG_RUNTIME_DIR=\$(mktemp -d)
    chmod 700 \"\$XDG_RUNTIME_DIR\"
    trap 'rm -rf \"\$XDG_RUNTIME_DIR\"' EXIT
    # The builder mounts whichever build root it was created with, which is not
    # always this repository's own, so absolute /build paths do not survive a
    # nested checkout. Everything here is resolved from the working directory
    # the runner already places us in: <build root>/packages/<pkg>.work.X/rpmbuild
    # rpmbuild only *checks* BuildRequires, so everything the spec declares has
    # to be installed here too, and this list drifting behind the spec is a
    # build failure rather than a skipped test. The gstreamer packages are what
    # tests/gapless_runtime.py needs during %check (a real playbin3 pipeline, an
    # appsink, FLAC/Opus/Vorbis encoders and an MP3 decoder); xdotool is what the
    # runtime scripts use to synthesise the X11 clicks that prove a credit link
    # inside a track row does not also activate the row.
    dnf5 -q -y --setopt=exclude= install \
      ../../luma-developer-platform/$architecture/RPMS/$platform_rpm_name \
      appstream dbus-daemon desktop-file-utils gstreamer1 gstreamer1-plugins-base \
      gstreamer1-plugins-good gtk4 libadwaita libappstream-glib meson ninja-build \
      python3-devel python3-gobject python3-gobject-base python3-mutagen rpm-build shared-mime-info \
      xdotool xorg-x11-server-Xvfb
    rpmbuild -ba --define \"_topdir \$PWD\" SPECS/luma-tide.spec
  "

rm -rf "$output_dir"
install -d -m 0755 "$output_dir/RPMS/noarch" "$output_dir/SRPMS"
install -m 0644 "$rpmbuild_dir"/RPMS/noarch/*.rpm "$output_dir/RPMS/noarch/"
install -m 0644 "$rpmbuild_dir"/SRPMS/*.rpm "$output_dir/SRPMS/"
(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Tide packages: %s\n' "$output_dir"
