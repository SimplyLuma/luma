#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Sign assembled runtime components into the Luma remote.
#
#   publish-runtime.sh [COMPONENT...]    (default: Platform Sdk Platform.GL.default)
#
# Platform: its share/runtime/locale tree is split out into
#   org.projectluma.Platform.Locale (a locale-subset extension, so people
#   download only the languages they use), then both are exported.
# Sdk: the SDK mounts the same Platform.Locale extension, so its own copy of
#   the locale tree is dropped rather than published twice.
# GL.default and Office: the assembled commit is re-signed into the remote
#   unchanged with `flatpak build-commit-from`.
#
# Nothing is uploaded here; update-remote.sh regenerates the summary and
# deltas, and sync-remote.sh uploads.
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"
depot_require_signing_control

components=("$@")
[ "${#components[@]}" -gt 0 ] || components=(Platform Sdk Platform.GL.default)
fingerprint=$(depot_gpg_fingerprint)
gpg_args=(--gpg-sign="$fingerprint" --gpg-homedir="$DEPOT_KEYS/gnupg")
"$(dirname -- "$0")/init-remote.sh"

export_tree() {  # export_tree BUILD_DIR SUBJECT
  depot_in_signer flatpak build-export --runtime --files=files --disable-sandbox "${gpg_args[@]}" \
    --subject="$2" "$depot_repo" "$1" "$DEPOT_RUNTIME_BRANCH"
}

for component in "${components[@]}"; do
  case "$component" in Platform|Sdk|Platform.GL.default|Platform.Office) ;; *) depot_die "unknown runtime component" ;; esac
  result="$depot_out/runtime/$component"
  [ -d "$result/ostree" ] || depot_die "no assembled $component; run build-runtime.sh $component"
  build_id=$(cat "$result/BUILD_ID")
  ref="runtime/org.projectluma.$component/$DEPOT_ARCH/$DEPOT_RUNTIME_BRANCH"
  subject="org.projectluma.$component $DEPOT_RUNTIME_BRANCH build $build_id"

  case "$component" in
    Platform|Sdk)
      work="$DEPOT_ROOT/work/publish/$component"
      depot_in_signer rm -rf "$work"
      depot_in_signer install -d "$work"
      depot_log "checking out $ref"
      depot_in_signer ostree checkout --repo="$result/ostree" -U "$ref" "$work/runtime"
      if [ "$component" = Platform ]; then
        locale="$work/locale"
        depot_in_signer install -d "$locale/files"
        depot_in_signer sh -c "mv '$work/runtime/files/share/runtime/locale/'* '$locale/files/'"
        depot_in_signer sh -c "cat > '$locale/metadata'" <<META
[Runtime]
name=org.projectluma.Platform.Locale
runtime=org.projectluma.Platform/$DEPOT_ARCH/$DEPOT_RUNTIME_BRANCH
sdk=org.projectluma.Sdk/$DEPOT_ARCH/$DEPOT_RUNTIME_BRANCH

[ExtensionOf]
ref=runtime/org.projectluma.Platform/$DEPOT_ARCH/$DEPOT_RUNTIME_BRANCH
META
        depot_log "exporting org.projectluma.Platform.Locale"
        export_tree "$locale" "org.projectluma.Platform.Locale $DEPOT_RUNTIME_BRANCH build $build_id"
      else
        depot_in_signer sh -c "rm -rf '$work/runtime/files/share/runtime/locale/'*"
      fi
      depot_log "exporting $ref"
      export_tree "$work/runtime" "$subject"
      depot_in_signer rm -rf "$work"
      ;;
    *)
      depot_log "signing $ref into the remote"
      depot_in_signer flatpak build-commit-from --untrusted "${gpg_args[@]}" \
        --src-repo="$result/ostree" --subject="$subject" "$depot_repo" "$ref"
      ;;
  esac
done
depot_in_signer ostree refs --repo="$depot_repo" | sort
