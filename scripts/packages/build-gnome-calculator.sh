#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in cpio mktemp patch podman rpm2cpio sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required RPM build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build the Luma Calculator RPM on the canonical Linux/x86_64 builder\n' >&2
  exit 1
fi

cache_dir="$repo_root/build/cache/srpm"
output_dir="$repo_root/build/packages/gnome-calculator"
mkdir -p "$cache_dir" "$output_dir"

srpm="$cache_dir/$GNOME_CALCULATOR_SRPM"
if [ ! -f "$srpm" ]; then
  command -v dnf5 >/dev/null 2>&1 || {
    printf 'error: dnf5 is required when the admitted Calculator SRPM is not cached\n' >&2
    exit 1
  }
  dnf5 download --source --destdir "$cache_dir" \
    "gnome-calculator-50.0-1.fc44"
fi

printf '%s  %s\n' "$GNOME_CALCULATOR_SRPM_SHA256" "$srpm" |
  sha256sum --check --status || {
    printf 'error: GNOME Calculator source RPM checksum mismatch\n' >&2
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

mv "$extract_dir/gnome-calculator.spec" "$rpmbuild_dir/SPECS/"
find "$extract_dir" -maxdepth 1 -type f -exec mv -t "$rpmbuild_dir/SOURCES" {} +
install -m 0644 \
  "$repo_root/patches/gnome-calculator/0001-luma-calculator-aesthetic-v1.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/gnome-calculator/0002-prairie-basic-product.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/gnome-calculator/0003-prairie-calculator-polish.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/gnome-calculator/0004-prairie-readout-presentation.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/gnome-calculator/0005-prairie-readout-input-boundary.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/gnome-calculator/0006-luma-appkit-convergence.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/gnome-calculator/0007-luma-shared-component-boundaries.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/gnome-calculator/0008-luma-preserve-island-elevation-overflow.patch" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 \
  "$repo_root/patches/gnome-calculator/0009-luma-advanced-mode-and-readout.patch" \
  "$rpmbuild_dir/SOURCES/"

(
  cd "$rpmbuild_dir/SPECS"
  patch --batch --forward -p1 \
    <"$repo_root/patches/gnome-calculator/0000-luma-fedora-spec.patch"
)

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" \
  "$FEDORA_RPM_BUILD_CONTAINER" '
    set -euo pipefail
    dnf5 -y install rpm-build dnf5-plugins
    dnf5 -y builddep SPECS/gnome-calculator.spec
    rpmbuild -ba --define "_topdir $PWD" SPECS/gnome-calculator.spec

    topdir=$PWD
    main_rpm=$(find "$topdir/RPMS/x86_64" -maxdepth 1 -type f \
      -name "gnome-calculator-[0-9]*.rpm" -print -quit)
    test -n "$main_rpm"
    verify_dir=$(mktemp -d)
    cd "$verify_dir"
    rpm2cpio "$main_rpm" >payload.cpio
    cpio -idm --quiet <payload.cpio
    rm -f payload.cpio
    gresource extract ./usr/bin/gnome-calculator \
      /org/gnome/calculator/math-window.ui >math-window.ui
    gresource extract ./usr/bin/gnome-calculator \
      /org/gnome/calculator/math-display.ui >math-display.ui
    gresource extract ./usr/bin/gnome-calculator \
      /org/gnome/calculator/style.css >style.css
    gresource extract ./usr/bin/gnome-calculator \
      /org/gnome/calculator/buttons-basic.ui >buttons-basic.ui
    grep -Fq "luma-calculator-titlebar" math-window.ui
    ! grep -Fq "luma-calculator-actionbar" math-window.ui
    ! grep -Fq "HistoryView" math-window.ui
    ! grep -Fq "MathConverter" math-window.ui
    ! grep -Fq "Mode Selection" math-window.ui
    grep -Fq "<property name=\"default-width\">360</property>" math-window.ui
    grep -Fq "id=\"scientific\"" buttons-basic.ui
    grep -Fq "win.button-mode" math-window.ui
    grep -Fq "font-size: 40px" style.css
    grep -Fq "change-sign" buttons-basic.ui
    grep -Fq "<property name=\"column-span\">2</property>" buttons-basic.ui
    grep -Fq "result-button" buttons-basic.ui
    grep -Fq "Project Luma calculator composition" style.css
    grep -Fq "button.operator-button" style.css
    grep -Fq "button.result-button" style.css
    grep -Fq "result-medium" style.css
    grep -Fq "result-small" style.css
    grep -Fq "luma-identity-button" math-window.ui
    grep -Fq "org.gnome.Calculator" math-window.ui
    grep -Fq "app.new-window" math-window.ui
    grep -Fq "win.number-format" math-window.ui
    grep -Fq "app.about" math-window.ui
    grep -Fq "app.quit" math-window.ui
    grep -Fq "minimize,maximize,close" math-window.ui
    grep -Fq "padding: 18px 16px 6px" style.css
    grep -Fq "<property name=\"hscrollbar-policy\">2</property>" math-display.ui
    grep -Fq "id=\"result_label\"" math-display.ui
    grep -Fq "<property name=\"visible\">false</property>" math-display.ui
    grep -Fq "<property name=\"focusable\">true</property>" math-display.ui
    grep -Fq "<property name=\"vexpand\">true</property>" math-display.ui
    grep -Fq "display-result" style.css
    grep -Fq "background-color: @sidebar_bg_color" style.css
    grep -Fq "Shared Luma Application Kit composition" style.css
    grep -Fq "luma-island" math-window.ui
    grep -Fq "<property name=\"overflow\">0</property>" math-window.ui
    grep -Fq "padding: 0 8px 0 12px" style.css
    ! grep -Fq "0 20px 38px -15px" style.css
    ! grep -Fq "!important" style.css
    grep -Fq "Name=Calculator" ./usr/share/applications/org.gnome.Calculator.desktop
    glib-compile-schemas ./usr/share/glib-2.0/schemas
  '

stable_rpms="$output_dir/RPMS/x86_64"
stable_srpms="$output_dir/SRPMS"
install -d -m 0755 "$stable_rpms" "$stable_srpms"
install -m 0644 \
  "$rpmbuild_dir/RPMS/x86_64/$GNOME_CALCULATOR_NEVRA.rpm" \
  "$stable_rpms/"
install -m 0644 \
  "$rpmbuild_dir/SRPMS/gnome-calculator-50.0-${GNOME_CALCULATOR_LUMA_RELEASE}.fc44.src.rpm" \
  "$stable_srpms/"

(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Luma Calculator packages: %s\n' "$output_dir"
