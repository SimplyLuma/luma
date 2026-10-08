#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in cpio mktemp patch rpm2cpio sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required RPM build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build the Luma Text Editor RPM on the canonical Linux/x86_64 builder\n' >&2
  exit 1
fi

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/gnome-text-editor"
mkdir -p "$cache_dir" "$output_dir"

srpm="$cache_dir/$GNOME_TEXT_EDITOR_SRPM"
if [ ! -f "$srpm" ]; then
  command -v dnf5 >/dev/null 2>&1 || {
    printf 'error: dnf5 is required when the admitted Text Editor SRPM is not cached\n' >&2
    exit 1
  }
  dnf5 download --source --destdir "$cache_dir" gnome-text-editor-50.1-1.fc44
fi

printf '%s  %s\n' "$GNOME_TEXT_EDITOR_SRPM_SHA256" "$srpm" |
  sha256sum --check --status || {
    printf 'error: GNOME Text Editor source RPM checksum mismatch\n' >&2
    exit 1
  }

work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
extract_dir="$work_dir/srpm"
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$extract_dir" "$rpmbuild_dir/BUILD" "$rpmbuild_dir/BUILDROOT" \
  "$rpmbuild_dir/RPMS" "$rpmbuild_dir/SOURCES" \
  "$rpmbuild_dir/SPECS" "$rpmbuild_dir/SRPMS"

(
  cd "$extract_dir"
  rpm2cpio "$srpm" >payload.cpio
  cpio -idm --quiet <payload.cpio
  rm -f payload.cpio
)

mv "$extract_dir/gnome-text-editor.spec" "$rpmbuild_dir/SPECS/"
find "$extract_dir" -maxdepth 1 -type f -exec mv -t "$rpmbuild_dir/SOURCES" {} +
install -m 0644 \
  "$repo_root/patches/gnome-text-editor/0001-luma-native-application-frame.patch" \
  "$rpmbuild_dir/SOURCES/"
(
  cd "$rpmbuild_dir/SPECS"
  patch --batch --forward --fuzz=0 -p1 \
    <"$repo_root/patches/gnome-text-editor/0000-luma-fedora-spec.patch"
)

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" \
  "$FEDORA_RPM_BUILD_CONTAINER" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins
    dnf5 -y builddep SPECS/gnome-text-editor.spec
    rpmbuild -ba --define "_topdir $PWD" SPECS/gnome-text-editor.spec

    topdir=$PWD
    main_rpm=$(find "$topdir/RPMS/x86_64" -maxdepth 1 -type f \
      -name "gnome-text-editor-[0-9]*.rpm" -print -quit)
    test -n "$main_rpm"
    verify_dir=$(mktemp -d)
    cd "$verify_dir"
    rpm2cpio "$main_rpm" >payload.cpio
    cpio -idm --quiet <payload.cpio
    rm -f payload.cpio
    gresource extract ./usr/bin/gnome-text-editor \
      /org/gnome/TextEditor/ui/editor-window.ui >editor-window.ui
    gresource extract ./usr/bin/gnome-text-editor \
      /org/gnome/TextEditor/style.css >style.css
    grep -Fq "luma-text-editor-window" editor-window.ui
    grep -Fq "luma-app-window" editor-window.ui
    grep -Fq "luma-identity-button" editor-window.ui
    grep -Fq "org.gnome.TextEditor" editor-window.ui
    grep -Fq "luma-command-bar" editor-window.ui
    grep -Fq "luma-command-group" editor-window.ui
    grep -Fq "luma-editor-surface" editor-window.ui
    grep -Fq "luma-island-split" editor-window.ui
    test "$(grep -Fo \"luma-editor-surface\" editor-window.ui | wc -l)" -eq 2
    grep -Fq "primary_menu_model" editor-window.ui
    grep -Fq "session.new-draft" editor-window.ui
    grep -Fq "info-outline-symbolic" editor-window.ui
    grep -Fq "Project Luma native application-frame composition" style.css
    ! grep -Fq ".luma-command-bar {" style.css
    ! grep -Fq "!important" style.css
    grep -Fq "Name=Text Editor" ./usr/share/applications/org.gnome.TextEditor.desktop
    glib-compile-schemas ./usr/share/glib-2.0/schemas
  '

stable_rpms="$output_dir/RPMS/x86_64"
stable_srpms="$output_dir/SRPMS"
install -d -m 0755 "$stable_rpms" "$stable_srpms"
install -m 0644 \
  "$rpmbuild_dir/RPMS/x86_64/$GNOME_TEXT_EDITOR_NEVRA.rpm" \
  "$stable_rpms/"
install -m 0644 \
  "$rpmbuild_dir/SRPMS/gnome-text-editor-50.1-${GNOME_TEXT_EDITOR_LUMA_RELEASE}.fc44.src.rpm" \
  "$stable_srpms/"

(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma Text Editor packages: %s\n' "$output_dir"
