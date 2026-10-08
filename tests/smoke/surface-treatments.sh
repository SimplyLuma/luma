#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$repo_root"

protocol=src/luma-platform/protocols/ext-background-effect-v1.xml
printf '%s  %s\n' \
  e463b7863c97d7be05489b52b15cd0b3a5d8290b51340f62023b47d03b4217e3 \
  "$protocol" | sha256sum -c - >/dev/null

python3 scripts/developer/generate-luma-platform-tokens.py --check
python3 -m py_compile src/luma-platform/appkit/luma_appkit/widgets.py

grep -Fq "rgba(241,242,243,.73)" \
  src/luma-platform/appkit/luma-appkit-frost-tokens.css
grep -Fq "rgba(255,255,255,.31)" \
  src/luma-platform/appkit/luma-appkit-glass-tokens.css
grep -Fq "'blur_px': <38.0>" src/luma-platform/appearance/luma-surface-recipes.h
grep -Fq "'saturation': <1.45>" src/luma-platform/appearance/luma-surface-recipes.h
grep -Fq "'blur_px': <24.0>" src/luma-platform/appearance/luma-surface-recipes.h
grep -Fq "'saturation': <1.65>" src/luma-platform/appearance/luma-surface-recipes.h

grep -Fq "meta_backend_is_rendering_hardware_accelerated" \
  patches/mutter/0006-wayland-Vendor-protocol-and-gate-blur-capability.patch
grep -Fq "meta_surface_actor_get_texture (self)" \
  patches/mutter/0007-compositor-Fix-opaque-region-after-surface-content-backport.patch
grep -Fq "luma_surface_backdrop_new" \
  src/luma-platform/ui/luma-application-window.c
grep -Fq "was_available != luma_surface_backdrop_get_available(self)" \
  src/luma-platform/ui/luma-surface-backdrop.c
grep -Fq "g_object_notify_by_pspec(G_OBJECT(self), properties[PROP_AVAILABLE])" \
  src/luma-platform/ui/luma-surface-backdrop.c
grep -Fq "window.luma-translucent.luma-treatment-frost headerbar.luma-titlebar" \
  src/luma-platform/ui/luma-ui.css
grep -Fq "window.luma-translucent.luma-treatment-glass headerbar.luma-titlebar" \
  src/luma-platform/ui/luma-ui.css
grep -Fq "toolbarview.luma-window-toolbar-view > .top-bar" \
  src/luma-platform/ui/luma-ui.css
grep -Fq '"LUMA_APPKIT_STRUCTURE_PATH", "luma-ui.css"' \
  src/luma-platform/appkit/luma_appkit/widgets.py
grep -Fq "Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2" \
  src/luma-platform/appkit/luma_appkit/widgets.py
grep -Fq "GTK_STYLE_PROVIDER_PRIORITY_APPLICATION + 2U" \
  src/luma-platform/ui/luma-ui-init.c
if rg -n '\.luma-(app|application)-window \.title' \
    src/luma-platform/appkit/luma-appkit-base.css; then
  printf 'error: AppKit globally overrides libadwaita row title typography\n' >&2
  exit 1
fi
grep -Fq "luma_surface_backdrop_display_is_available" \
  patches/gnome-control-center/0011-appearance-Add-native-Luma-surface-treatments.patch
grep -Fq "this._materialBinding.setEnabled(visible)" \
  patches/gnome-shell/0045-luma-surface-materials.patch
rg -Fq "this._surface.setSurfaceVisible(surfaceMode === 'connected', castShadows)" \
  patches/gnome-shell
rg -Fq "island.setSurfaceVisible(surfaceMode === 'separate', castShadows)" \
  patches/gnome-shell

if rg -n 'g_timeout_add|GLib\.timeout_add|setInterval|Shell\.Screenshot|org\.gnome\.Shell\.Screenshot' \
    src/luma-platform/ui/luma-surface-backdrop.c \
    patches/gnome-shell/0045-luma-surface-materials.patch \
    patches/gnome-control-center/0011-appearance-Add-native-Luma-surface-treatments.patch; then
  printf 'error: Surface Treatments introduced a polling or screenshot path\n' >&2
  exit 1
fi

bash -n scripts/packages/build-mutter.sh
bash -n scripts/packages/build-gnome-shell.sh
bash -n scripts/packages/build-gnome-control-center.sh
grep -Fq 'surface_candidate_release=1.luma.99.surfacepreview20260908.8' \
  scripts/packages/build-gnome-shell.sh
grep -Fq '0047-luma-shelf-settle-and-artwork-background.patch' \
  scripts/packages/build-gnome-shell.sh
grep -Fq 'background-gradient-start: transparent' \
  patches/gnome-shell/0047-luma-shelf-settle-and-artwork-background.patch
grep -Fq 'this._statusHover.remove_all_transitions()' \
  patches/gnome-shell/0047-luma-shelf-settle-and-artwork-background.patch
grep -Fq '0048-luma-shelf-artwork-only.patch' \
  scripts/packages/build-gnome-shell.sh
grep -Fq "artwork.add_style_class_name('luma-shelf-artwork')" \
  patches/gnome-shell/0048-luma-shelf-artwork-only.patch
grep -Fq 'box-shadow: none !important' \
  patches/gnome-shell/0048-luma-shelf-artwork-only.patch
grep -Fq '0049-luma-shelf-direct-paint-damage.patch' \
  scripts/packages/build-gnome-shell.sh
grep -Fq 'Clutter.OffscreenRedirect.NEVER' \
  patches/gnome-shell/0049-luma-shelf-direct-paint-damage.patch
grep -Fq 'Clutter.OffscreenRedirect.ALWAYS' \
  patches/gnome-shell/0049-luma-shelf-direct-paint-damage.patch
grep -Fq '0050-luma-status-island-material-owner.patch' \
  scripts/packages/build-gnome-shell.sh
for treatment in light dark frost glass; do
  grep -Fq "luma-shelf-material.luma-status-cluster.luma-surface-$treatment" \
    patches/gnome-shell/0050-luma-status-island-material-owner.patch
done
grep -Fq 'luma-shelf-root.luma-shelf-connected' \
  patches/gnome-shell/0050-luma-status-island-material-owner.patch
grep -Fq 'surface_candidate_release=1.luma.12.preview20260908' \
  scripts/packages/build-gnome-control-center.sh
if rg -n 'GNOME_(SHELL|CONTROL_CENTER)_LUMA_RELEASE' \
    scripts/packages/build-gnome-shell.sh \
    scripts/packages/build-gnome-control-center.sh; then
  printf 'error: Surface preview artifact paths depend on accepted tuple pins\n' >&2
  exit 1
fi
git diff --check

printf 'Surface Treatments source contracts: PASS\n'
