#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

for tool in head mktemp podman python3 sha256sum tar xmllint; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required Prairie icon-theme build tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
  printf 'error: build the Prairie icon-theme RPM on the canonical Linux/x86_64 builder\n' >&2
  exit 1
fi

theme_dir="$repo_root/assets/icon-theme/Prairie"
full_icon="$theme_dir/scalable/apps/org.gnome.Nautilus.svg"
symbolic_icon="$theme_dir/symbolic/apps/org.gnome.Nautilus-symbolic.svg"
status_dir="$theme_dir/symbolic/status"
actions_dir="$theme_dir/symbolic/actions"
places_dir="$theme_dir/scalable/places"
trash_icon="$places_dir/user-trash.svg"
trash_full_icon="$places_dir/user-trash-full.svg"
calculator_icon="$theme_dir/scalable/apps/org.gnome.Calculator.svg"
calculator_symbolic="$theme_dir/symbolic/apps/org.gnome.Calculator-symbolic.svg"

xmllint --noout "$full_icon" "$symbolic_icon" \
  "$actions_dir"/*.svg "$status_dir"/*.svg "$trash_icon" "$trash_full_icon" \
  "$calculator_icon" "$calculator_symbolic"
grep -Fxq 'Inherits=Adwaita,hicolor' "$theme_dir/index.theme"
grep -Fxq 'Directories=scalable/apps,scalable/places,scalable/mimetypes,scalable/devices,symbolic/actions,symbolic/apps,symbolic/status' \
  "$theme_dir/index.theme"
grep -Fq 'symbolic/actions' "$theme_dir/index.theme"
grep -Fq 'symbolic/status' "$theme_dir/index.theme"
grep -Fxq '[scalable/places]' "$theme_dir/index.theme"
grep -Fxq 'Context=Places' "$theme_dir/index.theme"
for status_icon in "$status_dir"/*.svg; do
  test "$(xmllint --xpath 'string(/*[local-name()="svg"]/@width)' \
    "$status_icon")" = 16px
  test "$(xmllint --xpath 'string(/*[local-name()="svg"]/@height)' \
    "$status_icon")" = 16px
  test "$(xmllint --xpath 'string(/*[local-name()="svg"]/@viewBox)' \
    "$status_icon")" = '0 0 16 16'
  grep -Fq 'fill="#2e3436"' "$status_icon"
done
if grep -Eqi 'stroke=|currentColor|<(style|mask|filter|image|use)[ >]|href=' \
  "$status_dir"/*.svg; then
  printf 'error: Prairie status glyphs must be self-contained 16px fill-only symbolics\n' >&2
  exit 1
fi
if grep -Fq '#000' "$status_dir"/*.svg; then
  printf 'error: Prairie status glyphs must use GNOME symbolic foreground ink\n' >&2
  exit 1
fi
for action_icon in \
  window-minimize-symbolic.svg \
  window-maximize-symbolic.svg \
  window-restore-symbolic.svg \
  window-close-symbolic.svg; do
  action_asset="$actions_dir/$action_icon"
  test "$(xmllint --xpath 'string(/*[local-name()="svg"]/@width)' \
    "$action_asset")" = 16px
  test "$(xmllint --xpath 'string(/*[local-name()="svg"]/@height)' \
    "$action_asset")" = 16px
  test "$(xmllint --xpath 'string(/*[local-name()="svg"]/@viewBox)' \
    "$action_asset")" = '0 0 16 16'
  grep -Fq 'fill="#2e3436"' "$action_asset"
done
if grep -Eqi 'stroke=|currentColor|<(style|mask|filter|image|use)[ >]|href=' \
  "$actions_dir"/window-*-symbolic.svg; then
  printf 'error: Prairie window controls must be self-contained fill-only symbolics\n' >&2
  exit 1
fi
# The approved empty design is byte-checked separately; retain the existing
# full-state artwork's strict contract rather than rewriting it to match.
for trash_asset in "$trash_full_icon"; do
  test "$(xmllint --xpath 'string(/*[local-name()="svg"]/@width)' \
    "$trash_asset")" = 128
  test "$(xmllint --xpath 'string(/*[local-name()="svg"]/@height)' \
    "$trash_asset")" = 128
  test "$(xmllint --xpath 'string(/*[local-name()="svg"]/@viewBox)' \
    "$trash_asset")" = '0 0 128 128'
  grep -Fq '<rect width="128" height="128" rx="32" fill="#ffffff"/>' \
    "$trash_asset"
  grep -Fq 'stroke="#4878b8"' "$trash_asset"
done
grep -Fq 'fill="#4878b8" fill-opacity=".18"' "$trash_full_icon"
if grep -Eqi '<(filter|image|use)[ >]|href=' "$trash_icon" "$trash_full_icon"; then
  printf 'error: Prairie Trash icons must be self-contained full-colour SVGs\n' >&2
  exit 1
fi
test "$(xmllint --xpath 'string(/*[local-name()="svg"]/@width)' \
  "$calculator_symbolic")" = 16px
test "$(xmllint --xpath 'string(/*[local-name()="svg"]/@height)' \
  "$calculator_symbolic")" = 16px
test "$(xmllint --xpath 'string(/*[local-name()="svg"]/@viewBox)' \
  "$calculator_symbolic")" = '0 0 16 16'
grep -Fq 'fill="#2e3436"' "$calculator_symbolic"
if grep -Eqi 'stroke=|currentColor|<(style|mask|filter|image|use)[ >]|href=' \
  "$calculator_symbolic"; then
  printf 'error: Prairie Calculator symbolic must be self-contained fill-only geometry\n' >&2
  exit 1
fi
# Source digests and shared mask tokens replace old literal 128px artwork checks.
python3 "$repo_root/assets/icon-theme/tools/sync-application-icons.py" --check
python3 "$repo_root/assets/icon-theme/tools/import-lumaui-icons.py" --check
python3 "$repo_root/assets/icon-theme/tools/validate-system-icons.py"
xmllint --noout "$theme_dir"/scalable/apps/*.svg

output_dir="$repo_root/build/packages/prairie-icon-theme"
mkdir -p "$output_dir"
work_dir=$(mktemp -d "$output_dir/work.XXXXXX")
rpmbuild_dir="$work_dir/rpmbuild"
mkdir -p "$rpmbuild_dir/BUILD" "$rpmbuild_dir/BUILDROOT" \
  "$rpmbuild_dir/RPMS" "$rpmbuild_dir/SOURCES" \
  "$rpmbuild_dir/SPECS" "$rpmbuild_dir/SRPMS"

install -m 0644 "$theme_dir/index.theme" "$rpmbuild_dir/SOURCES/index.theme"
install -m 0644 "$full_icon" "$rpmbuild_dir/SOURCES/org.gnome.Nautilus.svg"
install -m 0644 "$symbolic_icon" \
  "$rpmbuild_dir/SOURCES/org.gnome.Nautilus-symbolic.svg"
install -m 0644 "$calculator_icon" \
  "$rpmbuild_dir/SOURCES/org.gnome.Calculator.svg"
install -m 0644 "$calculator_symbolic" \
  "$rpmbuild_dir/SOURCES/org.gnome.Calculator-symbolic.svg"
tar -C "$theme_dir" -cf "$rpmbuild_dir/SOURCES/status-symbolics.tar" \
  symbolic/status
tar -C "$theme_dir" -cf "$rpmbuild_dir/SOURCES/window-symbolics.tar" \
  symbolic/actions
tar -C "$theme_dir" -cf "$rpmbuild_dir/SOURCES/places-icons.tar" \
  scalable/places
tar -C "$theme_dir" -cf "$rpmbuild_dir/SOURCES/app-icons.tar" \
  scalable/apps
install -m 0644 "$repo_root/packaging/gsettings/90-prairie.gschema.override" \
  "$rpmbuild_dir/SOURCES/"
install -m 0644 "$theme_dir/README.md" "$rpmbuild_dir/SOURCES/README.md"
install -m 0644 "$theme_dir/upstream/lucide/LICENSE.txt" \
  "$rpmbuild_dir/SOURCES/LUCIDE_LICENSE.txt"
tar -C "$theme_dir" -cf "$rpmbuild_dir/SOURCES/lucide-navigation-symbolics.tar" symbolic/actions
install -m 0644 "$theme_dir/upstream/lucide/LUCIDE_REFERENCE_LICENSE.txt" "$rpmbuild_dir/SOURCES/"
install -m 0644 "$theme_dir/upstream/lucide/navigation-manifest.json" "$rpmbuild_dir/SOURCES/manifest.json"
install -m 0644 "$theme_dir/upstream/lucide/lumaui-manifest.json" \
  "$rpmbuild_dir/SOURCES/lumaui-manifest.json"
tar -C "$theme_dir" -cf "$rpmbuild_dir/SOURCES/lucide-lumaui-sources.tar" \
  upstream/lucide/lumaui
# The transport row's exact upstream Lucide SVGs travel with the package, so
# the ISC geometry the manifest digests can be checked from the SRPM alone.
xmllint --noout "$theme_dir"/upstream/lucide/transport/*.svg
tar -C "$theme_dir" -cf "$rpmbuild_dir/SOURCES/lucide-transport-sources.tar" \
  upstream/lucide/transport
xmllint --noout "$theme_dir"/upstream/lucide/messages/*.svg
tar -C "$theme_dir" -cf "$rpmbuild_dir/SOURCES/lucide-messages-sources.tar" \
  upstream/lucide/messages
xmllint --noout "$theme_dir"/upstream/lucide/pan/*.svg
tar -C "$theme_dir" -cf "$rpmbuild_dir/SOURCES/lucide-pan-sources.tar" \
  upstream/lucide/pan
xmllint --noout "$theme_dir"/upstream/lucide/fills/*.svg
tar -C "$theme_dir" -cf "$rpmbuild_dir/SOURCES/lucide-fills-sources.tar" \
  upstream/lucide/fills
install -m 0644 "$theme_dir/application-manifest.json" "$rpmbuild_dir/SOURCES/"
# Explicit allowlist staging: unapproved studies cannot enter new contexts.
python3 - "$theme_dir" "$rpmbuild_dir/SOURCES" <<'PY_STAGE'
import json, pathlib, tarfile, shutil, sys
theme, sources = map(pathlib.Path, sys.argv[1:])
manifest = json.loads((theme / 'system-manifest.json').read_text())
integration = json.loads((theme / 'system-integration.json').read_text())
with tarfile.open(sources / 'system-icons.tar', 'w') as archive:
    for icon in manifest['icons']:
        archive.add(theme / icon['destination'], arcname=icon['destination'], recursive=False)
    # Documented aliases outside scalable/apps (which app-icons.tar carries).
    for alias in integration['aliases']:
        if not alias['destination'].startswith('scalable/apps/'):
            archive.add(theme / alias['destination'], arcname=alias['destination'], recursive=False)
with tarfile.open(sources / 'system-artwork.tar', 'w') as archive:
    paths = {i['source'] for i in manifest['icons']}
    paths.update(integration['snapshot_metadata_sha256'])
    paths.update('lucide-source/' + n for n in integration['lucide_reference_sha256'])
    for name in sorted(paths):
        path = 'artwork/' + integration['snapshot'] + '/' + name
        archive.add(theme / path, arcname=path, recursive=False)
for name in ['system-manifest.json', 'system-integration.json', 'SYSTEM_ARTWORK_NOTICE.md']:
    shutil.copyfile(theme / name, sources / name)
shutil.copyfile(theme.parent / 'tools/validate-system-icons.py', sources / 'validate-system-icons.py')
PY_STAGE
install -m 0644 "$repo_root/packaging/rpm/prairie-icon-theme.spec" \
  "$rpmbuild_dir/SPECS/"

"$repo_root/scripts/packages/run-in-rpm-builder.sh" \
  "$rpmbuild_dir" \
  "${LUMA_ICON_RPM_BUILD_CONTAINER:-$FEDORA_RPM_BUILD_CONTAINER}" '
    set -euo pipefail
    dnf5 -y install rpm-build cpio libxml2 python3
    rpmbuild -ba --define "_topdir $PWD" SPECS/prairie-icon-theme.spec
    rpm_path=$(find RPMS/noarch -maxdepth 1 -type f \
      -name "prairie-icon-theme-*.rpm" -print -quit)
    test -n "$rpm_path"
    verify_dir=$(mktemp -d)
    cd "$verify_dir"
    rpm2cpio "$OLDPWD/$rpm_path" >payload.cpio
    cpio -idm --quiet <payload.cpio
    rm -f payload.cpio
    theme=./usr/share/icons/Prairie
    python3 "$OLDPWD/SOURCES/validate-system-icons.py" --payload --theme "$theme" \
      --manifest "$OLDPWD/SOURCES/system-manifest.json" \
      --integration "$OLDPWD/SOURCES/system-integration.json"
    test -f "$theme/index.theme"
    test -f "$theme/scalable/apps/org.gnome.Nautilus.svg"
    test -f "$theme/symbolic/apps/org.gnome.Nautilus-symbolic.svg"
    test ! -e "$theme/scalable/apps/firefox.svg"
    test ! -e "$theme/scalable/apps/org.mozilla.firefox.svg"
    test -f "$theme/scalable/apps/org.gnome.Calculator.svg"
    test -f "$theme/symbolic/apps/org.gnome.Calculator-symbolic.svg"
    test -f "$theme/symbolic/actions/window-minimize-symbolic.svg"
    test -f "$theme/symbolic/actions/window-maximize-symbolic.svg"
    test -f "$theme/symbolic/actions/window-restore-symbolic.svg"
    test -f "$theme/symbolic/actions/window-close-symbolic.svg"
    for transport in media-playlist-shuffle media-skip-backward \
      media-playback-start media-playback-pause media-skip-forward \
      media-playlist-repeat; do
      test -f "$theme/symbolic/actions/$transport-symbolic.svg"
      grep -Fq "foreground-stroke" "$theme/symbolic/actions/$transport-symbolic.svg"
    done
    for glyph in mail-attachment mail-send call-start camera-video avatar-default \
      system-users luma-business system-search document-edit audio-input-microphone \
      emblem-favorite view-refresh dialog-warning emblem-ok go-next phone mail-message-new \
      edit-clear object-select go-previous; do
      grep -Fq "foreground-stroke" "$theme/symbolic/actions/$glyph-symbolic.svg"
    done
    for glyph in cloud-rain cloud-sun cloud-moon cloud-fog cloud-lightning snowflake; do
      test -f "$theme/symbolic/actions/lumaui-$glyph-symbolic.svg"
      grep -Fq "foreground-stroke" "$theme/symbolic/actions/lumaui-$glyph-symbolic.svg"
    done
    test -f "$theme/symbolic/actions/go-next-symbolic-rtl.svg"
    test -f "$theme/symbolic/actions/go-previous-symbolic-rtl.svg"
    for glyph in pan-down pan-up pan-start pan-end; do
      grep -Fq "foreground-stroke" "$theme/symbolic/actions/$glyph-symbolic.svg"
    done
    # Standard names Luma apps and the shell use that Adwaita 50 no longer
    # ships, drawn from Lucide as fills-only symbolics.
    for glyph in cursor-default draw-freehand edit-rename application-x-apk \
      utilities-system-monitor emblem-default emblem-shared emblem-synchronizing \
      notifications; do
      grep -Fq "fill=\"#2e3436\"" "$theme/symbolic/actions/$glyph-symbolic.svg"
      ! grep -Eqi "stroke|currentColor|<(style|mask|filter|image|use)[ >]|href=" \
        "$theme/symbolic/actions/$glyph-symbolic.svg"
    done
    test -f "$theme/symbolic/actions/pan-start-symbolic-rtl.svg"
    test -f "$theme/symbolic/actions/pan-end-symbolic-rtl.svg"
    test -f "$theme/symbolic/actions/media-skip-backward-symbolic-rtl.svg"
    test -f "$theme/symbolic/actions/media-skip-forward-symbolic-rtl.svg"
    test -f "$theme/scalable/places/user-trash.svg"
    test -f "$theme/scalable/places/user-trash-full.svg"
    test -f "$theme/scalable/apps/user-trash.svg"
    test -f "$theme/scalable/apps/user-trash-full.svg"
    cmp "$theme/scalable/places/user-trash.svg" \
      "$theme/scalable/apps/user-trash.svg"
    cmp "$theme/scalable/places/user-trash-full.svg" \
      "$theme/scalable/apps/user-trash-full.svg"
    test -f "$theme/symbolic/status/network-wireless-signal-excellent-symbolic.svg"
    test -f "$theme/symbolic/status/network-wireless-signal-good-symbolic.svg"
    test -f "$theme/symbolic/status/network-wireless-signal-ok-symbolic.svg"
    test -f "$theme/symbolic/status/network-wireless-signal-weak-symbolic.svg"
    test -f "$theme/symbolic/status/network-wireless-signal-none-symbolic.svg"
    test -f "$theme/symbolic/status/network-wired-symbolic.svg"
    test -f "$theme/symbolic/status/audio-volume-muted-symbolic.svg"
    test -f "$theme/symbolic/status/audio-volume-low-symbolic.svg"
    test -f "$theme/symbolic/status/audio-volume-medium-symbolic.svg"
    test -f "$theme/symbolic/status/audio-volume-high-symbolic.svg"
    test -f "$theme/symbolic/status/system-shutdown-symbolic.svg"
    test "$(find "$theme/symbolic/status" -maxdepth 1 -type f \
      -name "battery-*-symbolic.svg" | wc -l)" -eq 46
    test -f "$theme/symbolic/status/battery-level-0-symbolic.svg"
    test -f "$theme/symbolic/status/battery-level-100-symbolic.svg"
    test -f "$theme/symbolic/status/battery-level-90-charging-symbolic.svg"
    test -f "$theme/symbolic/status/battery-level-100-plugged-in-symbolic.svg"
    test -f "$theme/symbolic/status/battery-full-symbolic.svg"
    test -f "$theme/symbolic/status/battery-full-charging-symbolic.svg"
    test -f "$theme/symbolic/status/battery-missing-symbolic.svg"
    grep -Fxq "Inherits=Adwaita,hicolor" "$theme/index.theme"
    grep -Fxq "icon-theme='\''Prairie'\''" \
      ./usr/share/glib-2.0/schemas/90-prairie.gschema.override
    xmllint --noout "$theme/scalable/apps/org.gnome.Nautilus.svg" \
      "$theme/symbolic/apps/org.gnome.Nautilus-symbolic.svg" \
      "$theme/scalable/apps/org.gnome.Calculator.svg" \
      "$theme/symbolic/apps/org.gnome.Calculator-symbolic.svg" \
      "$theme"/symbolic/actions/*.svg \
      "$theme/scalable/places/user-trash.svg" \
      "$theme/scalable/places/user-trash-full.svg" \
      "$theme"/symbolic/status/*.svg
  '

stable_rpms="$output_dir/RPMS/noarch"
stable_srpms="$output_dir/SRPMS"
rm -rf "$output_dir/RPMS" "$output_dir/SRPMS" "$output_dir/SHA256SUMS"
install -d -m 0755 "$stable_rpms" "$stable_srpms"
mapfile -t built_rpms < <(find "$rpmbuild_dir/RPMS/noarch" -maxdepth 1 -type f \
  -name 'prairie-icon-theme-*.noarch.rpm' -print)
if [ "${#built_rpms[@]}" -ne 1 ]; then
  printf 'expected one built Prairie icon theme RPM; found %s\n' "${#built_rpms[@]}" >&2
  exit 1
fi
install -m 0644 "${built_rpms[0]}" "$stable_rpms/"
install -m 0644 "$rpmbuild_dir"/SRPMS/prairie-icon-theme-*.src.rpm \
  "$stable_srpms/"

(
  cd "$output_dir"
  find RPMS SRPMS -type f -name '*.rpm' -exec sha256sum {} + | sort >SHA256SUMS
)

printf 'Prairie icon-theme package: %s\n' "$output_dir"
