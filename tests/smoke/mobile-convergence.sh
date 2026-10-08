#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/desktop/inputs.env"

failures=0
check() {
  description=$1
  shift
  if "$@"; then
    printf 'PASS  %s\n' "$description"
  else
    printf 'FAIL  %s\n' "$description"
    failures=$((failures + 1))
  fi
}

decision="$repo_root/docs/decisions/014-fp6-mobile-shell-bakeoff.md"
desktop_packages="$repo_root/config/desktop/packages.txt"
platform_packages="$repo_root/config/shared/platform-packages.txt"
application_packages="$repo_root/config/shared/application-packages.txt"
mobile_compose="$repo_root/scripts/mobile/compose-fp6-rootfs.sh"
desktop_compose="$repo_root/scripts/vm/compose-desktop-image.sh"
mobile_defaults="$repo_root/config/mobile/luma-shell-handheld.dconf"
greetd="$repo_root/config/mobile/fp6-physical/overlay/etc/greetd/config.toml"
mobile_packages="$repo_root/config/mobile/packages.txt"
phone_capability_packages="$repo_root/config/mobile/phone-capability-packages.txt"
mobile_ui_packages="$repo_root/config/mobile/ui-packages.txt"
phosh_session="$repo_root/scripts/mobile/luma-phosh-session"

check 'ADR-014 is accepted' grep -Fqx -- '- **Status:** Accepted' "$decision"
check 'ADR-014 selects Phosh handheld rendering' \
  grep -Fq 'Use **Phosh/Phoc as Luma' "$decision"
check 'ADR-007 points to its superseding physical decision' \
  grep -Fq 'Superseded by [ADR-014]' \
  "$repo_root/docs/decisions/007-converged-adaptive-luma-shell.md"
check 'shared shell-state role is architecture independent' \
  grep -Fqx 'luma-shell-state' "$platform_packages"
check 'shared Search role is architecture independent' \
  grep -Fqx 'luma-search' "$platform_packages"
check 'desktop package pin matches the shared-state input' \
  grep -Fqx "$LUMA_SHELL_STATE_NEVRA" "$desktop_packages"
check 'desktop package pin matches the shared Search input' \
  grep -Fqx "$LUMA_SEARCH_NEVRA" "$desktop_packages"
check 'developer platform is a shared application role' \
  grep -Fqx 'luma-developer-platform' "$application_packages"
check 'Darkroom is one shared adaptive application role' \
  grep -Fqx 'luma-darkroom' "$application_packages"
check 'desktop package pin matches the developer-platform input' \
  grep -Fqx "$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA" "$desktop_packages"
check 'mobile composition consumes the exact developer-platform RPM' \
  grep -Fq 'build/packages/luma-developer-platform/aarch64/RPMS/$LUMA_DEVELOPER_PLATFORM_AARCH64_NEVRA.rpm' \
  "$mobile_compose"
check 'desktop composition consumes the exact developer-platform RPM' \
  grep -Fq 'build/packages/luma-developer-platform/x86_64/RPMS/$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA.rpm' \
  "$desktop_compose"
check 'mobile composition consumes the shared Darkroom RPM' \
  grep -Fq 'build/packages/luma-darkroom/RPMS/noarch/$LUMA_DARKROOM_NEVRA.rpm' \
  "$mobile_compose"
check 'desktop composition consumes the shared Darkroom RPM' \
  grep -Fq 'build/packages/luma-darkroom/RPMS/noarch/$LUMA_DARKROOM_NEVRA.rpm' \
  "$desktop_compose"
check 'Tide is one shared adaptive application role' \
  grep -Fqx 'luma-tide' "$application_packages"
check 'desktop package pin matches the shared Tide input' \
  grep -Fqx "$LUMA_TIDE_NEVRA" "$desktop_packages"
check 'mobile composition consumes the shared Tide RPM' \
  grep -Fq 'build/packages/luma-tide/RPMS/noarch/$LUMA_TIDE_NEVRA.rpm' \
  "$mobile_compose"
check 'desktop composition consumes the shared Tide RPM' \
  grep -Fq 'build/packages/luma-tide/RPMS/noarch/$LUMA_TIDE_NEVRA.rpm' \
  "$desktop_compose"
check 'mobile composition consumes the platform manifest' \
  grep -Fq 'config/shared/platform-packages.txt' "$mobile_compose"
check 'mobile composition consumes the exact shared-state RPM' \
  grep -Fq 'build/packages/luma-shell-state/RPMS/noarch/$LUMA_SHELL_STATE_NEVRA.rpm' \
  "$mobile_compose"
check 'mobile composition consumes the exact shared Search RPM' \
  grep -Fq 'build/packages/luma-search/RPMS/noarch/$LUMA_SEARCH_NEVRA.rpm' \
  "$mobile_compose"
check 'mobile composition consumes the exact native Luma Phosh RPM' \
  grep -Fq 'build/packages/luma-phosh/aarch64/RPMS/$LUMA_PHOSH_AARCH64_NEVRA.rpm' \
  "$mobile_compose"
check 'mobile composition consumes the exact native Luma Phoc RPM' \
  grep -Fq 'build/packages/phoc/aarch64/RPMS/$LUMA_PHOC_AARCH64_NEVRA.rpm' \
  "$mobile_compose"
check 'Phoc keeps authenticated startup on the canonical Prism artwork' sh -c \
  'grep -Fq "LUMA_PRISM_PATH" "$1" && grep -Fq "phoc_cairo_texture_new" "$1" && grep -Fq "self->show_spinner = FALSE" "$2"' _ \
  "$repo_root/patches/phoc/0002-luma-render-prism-during-shell-startup.patch" \
  "$repo_root/patches/phoc/0001-luma-quiet-graphical-session-shield.patch"
check 'Presence compositor preserves the final Prism scanout' sh -c \
  'grep -Fq "LUMA_CAGE_SCANOUT_HANDOFF" "$1" && grep -Fq "_exit(ret)" "$1"' _ \
  "$repo_root/patches/cage/0001-luma-preserve-authenticated-scanout.patch"
check 'quick options commits folded before its first frame' sh -c \
  'grep -Fq "PHOSH_DRAG_SURFACE_STATE_FOLDED" "$1" && grep -Fq "zphoc_draggable_layer_surface_v1_set_state (priv->drag_surface, drag_state)" "$1"' _ \
  "$repo_root/patches/phosh/0006-luma-commit-initial-folded-drag-state.patch"
check 'mobile composition consumes the exact shared Prairie icon RPM' \
  grep -Fq 'build/packages/prairie-icon-theme/RPMS/noarch/$PRAIRIE_ICON_THEME_NEVRA.rpm' \
  "$mobile_compose"
check 'mobile composition consumes the exact GTK 4 downstream RPM' \
  grep -Fq 'build/packages/gtk4/RPMS/aarch64/$GTK4_AARCH64_NEVRA.rpm' \
  "$mobile_compose"
check 'mobile composition consumes the exact GTK 3 downstream RPM' \
  grep -Fq 'build/packages/gtk3/RPMS/aarch64/$GTK3_AARCH64_NEVRA.rpm' \
  "$mobile_compose"
check 'mobile composition consumes the exact libhandy downstream RPM' \
  grep -Fq 'build/packages/libhandy/RPMS/aarch64/$LIBHANDY_AARCH64_NEVRA.rpm' \
  "$mobile_compose"
check 'mobile composition consumes the exact libadwaita downstream RPM' \
  grep -Fq 'build/packages/libadwaita/RPMS/aarch64/$LIBADWAITA_AARCH64_NEVRA.rpm' \
  "$mobile_compose"
check 'desktop composition consumes the exact shared-state RPM' \
  grep -Fq 'build/packages/luma-shell-state/RPMS/noarch/$LUMA_SHELL_STATE_NEVRA.rpm' \
  "$desktop_compose"
check 'desktop composition consumes the exact shared Search RPM' \
  grep -Fq 'build/packages/luma-search/RPMS/noarch/$LUMA_SEARCH_NEVRA.rpm' \
  "$desktop_compose"
check 'physical handheld starts the canonical authenticated Phosh session' sh -c \
  'grep -Fq "command = \"/usr/local/bin/luma-phosh-session\"" "$1" && grep -Fq "user = \"luma\"" "$1" && grep -Fq "exec /opt/luma/phosh/bin/phosh-session" "$2"' _ \
  "$greetd" "$phosh_session"
check 'Phosh exports explicit handheld application capabilities' \
  sh -c 'grep -Fq "LUMA_PRESENTATION_MODE" "$1" && grep -Fq "LUMA_INPUT_MODE" "$1" && grep -Fq "exec /opt/luma/phosh/bin/phosh-session" "$1"' _ "$phosh_session"
check 'authenticated session uses a dedicated display-manager VT' sh -c \
  'grep -Fqx "vt = 7" "$1" && grep -Fq "Conflicts=getty@tty7.service" "$2"' _ \
  "$greetd" "$repo_root/packaging/systemd/30-luma-greetd-vt.conf"
check 'authenticated Phosh session does not force verbose compositor output' \
  grep -Fq '0003-luma-quiet-session-handoff.patch' \
  "$repo_root/packaging/rpm/luma-phosh.spec"
check 'handheld lock background is the packaged Prism image' sh -c \
  'grep -Fqx "picture-uri='"'"'file:///usr/share/backgrounds/luma/luma-prism.png'"'"'" "$1" && grep -Fqx "picture-options='"'"'zoom'"'"'" "$1"' _ \
  "$mobile_defaults"
check 'native Luma Phosh owns the single handheld lock gate' \
  grep -Fqx 'ExecStart=/opt/luma/phosh/libexec/phosh --locked' \
  "$repo_root/packaging/systemd/90-luma-phosh.conf"
check 'physical handheld includes network time synchronization' \
  grep -Fqx 'chrony' "$mobile_packages"
check 'physical handheld enables network time synchronization' \
  grep -Fq 'chronyd.service' "$mobile_compose"
check 'Phosh exposes the complete shared application catalog' \
  grep -Fqx 'app-filter-mode=@as []' "$mobile_defaults"
check 'Phosh retains hardware cutout layout' \
  grep -Fqx "shell-layout='device'" "$mobile_defaults"
check 'shared desktop profile selects the Luma GTK/Phosh theme' \
  grep -Fqx "gtk-theme='Luma'" \
  "$repo_root/config/desktop/dconf/db/luma.d/00-luma-desktop"
check 'handheld capability composition has no competing Chatty SMS app' \
  sh -c '! grep -Eq "(^|[[:space:]])chatty($|[[:space:]-])" "$1"' _ \
  "$phone_capability_packages"
check 'handheld UI composition has no competing Chatty SMS app' \
  sh -c '! grep -Eq "(^|[[:space:]])chatty($|[[:space:]-])" "$1"' _ \
  "$mobile_ui_packages"

python3 -m unittest \
  tests.unit.test_luma_shell_state \
  tests.unit.test_luma_search \
  tests.unit.test_luma_design_contract >/dev/null || failures=$((failures + 1))

if [ "$failures" -ne 0 ]; then
  printf '\nMobile convergence smoke test: FAIL (%s checks)\n' "$failures"
  exit 1
fi

printf '\nMobile convergence smoke test: PASS\n'
