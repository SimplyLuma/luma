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

check 'developer platform is a shared application role' \
  grep -Fqx 'luma-developer-platform' \
  "$repo_root/config/shared/application-packages.txt"
check 'desktop package manifest carries the exact x86_64 pin' \
  grep -Fqx "$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA" \
  "$repo_root/config/desktop/packages.txt"
check 'developer-platform CI uses the canonical x86_64 Fedora builder digest' \
  grep -Fq "container: $FEDORA_RPM_BUILD_CONTAINER" \
  "$repo_root/.github/workflows/luma-developer-platform.yml"
check 'desktop build produces the platform before core applications' \
  awk '
    /build-luma-developer-platform.sh/ { platform = NR }
    /build-prairie-core-apps.sh/ { core = NR }
    END { exit !(platform > 0 && core > platform) }
  ' "$repo_root/scripts/build-luma-desktop.sh"
check 'desktop composition consumes the exact platform RPM' \
  grep -Fq 'build/packages/luma-developer-platform/x86_64/RPMS/$LUMA_DEVELOPER_PLATFORM_X86_64_NEVRA.rpm' \
  "$repo_root/scripts/vm/compose-desktop-image.sh"
check 'mobile composition consumes the exact platform RPM' \
  grep -Fq 'build/packages/luma-developer-platform/aarch64/RPMS/$LUMA_DEVELOPER_PLATFORM_AARCH64_NEVRA.rpm' \
  "$repo_root/scripts/mobile/compose-fp6-rootfs.sh"
check 'cross-architecture RPM builders select the requested image architecture' \
  grep -Fq 'architecture_options=(--arch "$builder_architecture")' \
  "$repo_root/scripts/packages/run-in-rpm-builder.sh"
check 'cached RPM builders fail closed on an architecture mismatch' \
  grep -Fq "actual_architecture=\$(podman image inspect --format '{{.Architecture}}'" \
  "$repo_root/scripts/packages/run-in-rpm-builder.sh"
check 'container architecture names normalize to the Fedora RPM vocabulary' \
  grep -Fq 'arm64) actual_architecture=aarch64' \
  "$repo_root/scripts/packages/run-in-rpm-builder.sh"
check 'core apps require the shared runtime' \
  grep -Fq 'Requires:       luma-developer-platform >= 0.1.0' \
  "$repo_root/packaging/rpm/prairie-core-apps.spec"
check 'Notes publishes the semantic application root' \
  grep -Fq 'LumaSemantics.SemanticObject.new("notes", "application", "Notes")' \
  "$repo_root/src/prairie-core/prairie_apps/notes.py"
check 'Notes publishes through the authenticated broker boundary' \
  grep -Fq 'SemanticPublisher(' \
  "$repo_root/src/prairie-core/prairie_apps/notes.py"
check 'Calendar publishes a bounded Live Extension from the shared app' \
  grep -Fq 'LiveExtensionPublisher(' \
  "$repo_root/src/prairie-core/prairie_apps/calendar.py"
check 'desktop and mobile pins remain one shared core-app package' \
  grep -Fqx "$PRAIRIE_CORE_APPS_NEVRA" \
  "$repo_root/config/desktop/packages.txt"
check 'broker ships a session-bus activation contract' \
  grep -Fq 'Name=org.projectluma.SemanticBroker1' \
  "$repo_root/src/luma-platform/broker/data/org.projectluma.SemanticBroker1.service"
check 'broker service is hardened as a per-user service' \
  grep -Fq 'ProtectSystem=strict' \
  "$repo_root/src/luma-platform/broker/data/luma-semantic-broker.service"
check 'generated design tokens are current' \
  python3 "$repo_root/scripts/developer/generate-luma-platform-tokens.py" --check
check 'AppKit owns a distinct inactive-window elevation' \
  grep -Fq '.luma-app-window:backdrop {' \
  "$repo_root/src/luma-platform/appkit/luma-appkit.css"
check 'inactive AppKit title rows never draw a false divider' \
  grep -Fq '.luma-titlebar:backdrop { border: 0; box-shadow: none; }' \
  "$repo_root/src/luma-platform/appkit/luma-appkit.css"
check 'desktop composer consumes shared application roles' \
  grep -Fq 'config/shared/application-packages.txt' \
  "$repo_root/scripts/vm/compose-desktop-image.sh"
check 'system Flatpak replacement policy is explicit and narrow' \
  grep -Eq '^org\.gnome\.TextEditor [a-z0-9-]+$' \
  "$repo_root/config/desktop/system-flatpak-replacements.txt"

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.unit.test_luma_developer_platform \
  tests.unit.test_luma_semantic_broker || failures=$((failures + 1))

if [ "$failures" -ne 0 ]; then
  printf '\nLuma Developer Platform convergence: FAIL (%s checks)\n' "$failures"
  exit 1
fi

printf '\nLuma Developer Platform convergence: PASS\n'
