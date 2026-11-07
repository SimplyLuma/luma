#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Sign a built app into the Luma remote.
#
#   publish-app.sh APP_ID [--approve-permission-changes] [--third-party]
#
# The app must have been built by build-app.sh (out/apps/APP_ID/repo). Before
# anything is written, its permissions are compared with the commit currently
# published on the same branch. ADR-028 section 7: a release whose permission
# set grows is held for human review, so the publish stops with exit status 3
# and prints the changes unless --approve-permission-changes is given (the
# reviewer's decision, recorded in release.json).
#
# --third-party publishes a developer's app from a Depot submission
# (tools/depot-review). Third-party apps live on the stable branch, can never
# use an id Luma owns (reserved prefixes, packaging/flatpak/apps/titles.json, or
# any id the remote already carries without a third-party mark), and bring their listing media through
# the signed Depot catalogue instead of share/luma-app-media. A Luma publish
# refuses an id that a third-party release already holds, so neither side can
# shadow the other.
#
# Writes out/apps/APP_ID/release.json (commit, branch, permissions, changes,
# sources) which update-remote.sh folds into the public apps/index.json.
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"
depot_require_signing_control

app_id=${1:?usage: publish-app.sh APP_ID [--approve-permission-changes] [--third-party]}
[[ "$app_id" =~ ^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z][A-Za-z0-9_-]*){2,}$ ]] || depot_die "invalid app identity"
approve=0
third_party=0
for option in "${@:2}"; do
  case "$option" in
    --approve-permission-changes) approve=1 ;;
    --third-party) third_party=1 ;;
    *) depot_die "unknown option $option" ;;
  esac
done
out="$depot_out/apps/$app_id"
release_out="$DEPOT_ROOT/releases/$app_id"
install -d -m 0700 "$release_out"
[ -d "$out/repo" ] || depot_die "no build for $app_id; run build-app.sh first"
fingerprint=$(depot_gpg_fingerprint)
"$(dirname -- "$0")/init-remote.sh"

# Validate the complete immutable export before any app or companion is signed.
depot_in_signer python3 -B scripts/depot/validate-app-snapshot.py \
  --repo "$out/repo" --app-id "$app_id" --arch "$DEPOT_ARCH" >"$release_out/source-refs.json"
ref=$(depot_in_signer python3 -B -c 'import json,sys; print(json.load(open(sys.argv[1]))[0]["ref"])' "$release_out/source-refs.json")
source_commit=$(depot_in_signer python3 -B -c 'import json,sys; print(json.load(open(sys.argv[1]))[0]["source_commit"])' "$release_out/source-refs.json")
# Permissions are taken from the immutable object that will be signed.
depot_in_signer ostree cat --repo="$out/repo" "$source_commit" /metadata >"$release_out/metadata"
cmp "$out/metadata" "$release_out/metadata" || depot_die "build metadata differs from actual source commit"
branch=${ref##*/}
owner_mark="$release_out/THIRD-PARTY"
if [ "$third_party" = 1 ]; then
  [ "$branch" = stable ] || depot_die "third-party apps publish to the stable branch, not $branch"
  case "${app_id,,}" in
    org.projectluma.*|io.luma.*|com.simplyluma.*|org.simplyluma.*|com.projectluma.*)
      depot_die "$app_id uses an id prefix reserved for Luma" ;;
  esac
  depot_in_signer python3 -B -c 'import json,sys; sys.exit(sys.argv[2] in json.load(open(sys.argv[1])))' \
    packaging/flatpak/apps/titles.json "$app_id" || depot_die "$app_id is one of Luma's own apps"
  # The first third-party release of an id claims it only if the remote has
  # never carried that id (any branch, any CPU, or a companion runtime).
  if [ ! -e "$owner_mark" ]; then
    remote_refs=$(depot_in_signer ostree refs --repo="$depot_repo")
    if grep -Eq "^(app/${app_id//./\\.}/|runtime/${app_id//./\\.}\.[^/]+/)" <<<"$remote_refs"; then
      depot_die "$app_id already has a Luma release; a third-party app cannot shadow it"
    fi
  fi
  printf '%s\n' "$app_id" >"$owner_mark"
else
  [ ! -e "$owner_mark" ] || depot_die "$app_id is a third-party app; publish it through Depot review"
  depot_in_signer python3 scripts/depot/stage-app-media.py --repo "$out/repo" \
    --commit "$source_commit" --app-id "$app_id" --site "$DEPOT_ROOT/site" \
    --base-url "$DEPOT_PUBLIC_URL" >"$release_out/media-stage.json"
fi
depot_in_signer python3 scripts/depot/flatpak-permissions.py "$release_out/metadata" >"$release_out/permissions.json"

changes='[]'
if depot_in_signer ostree rev-parse --repo="$depot_repo" "$ref" >/dev/null 2>&1; then
  depot_in_signer ostree cat --repo="$depot_repo" "$ref" /metadata >"$release_out/previous-metadata"
  set +e
  depot_in_signer python3 scripts/depot/flatpak-permissions.py "$release_out/metadata" \
    --previous "$release_out/previous-metadata" >"$release_out/permission-diff.json"
  status=$?
  set -e
  case "$status" in
    0) ;;
    3)
      if [ "$approve" != 1 ]; then
        cat "$release_out/permission-diff.json" >&2
        printf 'held for review: %s asks for more than the published release\n' "$app_id" >&2
        exit 3
      fi
      depot_log "permission changes approved by the publisher for $app_id"
      ;;
    *) depot_die "permission comparison failed for $app_id" ;;
  esac
  changes=$(depot_in_signer python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["permission_changes"]))' "$release_out/permission-diff.json")
fi

depot_log "signing $ref into the remote"
depot_in_signer flatpak build-commit-from --untrusted --no-update-summary \
  --gpg-sign="$fingerprint" --gpg-homedir="$DEPOT_KEYS/gnupg" \
  --src-repo="$out/repo" --src-ref="$source_commit" "$depot_repo" "$ref"
commit=$(depot_in_signer ostree rev-parse --repo="$depot_repo" "$ref")

# A Debug companion is imported only after exact application declaration and
# same-app/arch/branch ExtensionOf validation. It is never an application role.
depot_in_signer python3 -B -c 'import json,sys; [print(r["ref"]+" "+r["source_commit"]) for r in json.load(open(sys.argv[1]))[1:]]' "$release_out/source-refs.json" >"$release_out/companion-refs"
while read -r companion_ref companion_commit; do
  [ -n "$companion_ref" ] || continue
  depot_in_signer flatpak build-commit-from --untrusted --no-update-summary \
    --gpg-sign="$fingerprint" --gpg-homedir="$DEPOT_KEYS/gnupg" \
    --src-repo="$out/repo" --src-ref="$companion_commit" "$depot_repo" "$companion_ref"
done <"$release_out/companion-refs"

depot_in_signer python3 - "$release_out" "$out" "$app_id" "$branch" "$DEPOT_ARCH" "$commit" "$approve" "$changes" <<'PY'
import datetime, json, sys
from pathlib import Path
out, source, app_id, branch, arch, commit, approve, changes = sys.argv[1:]
release = {
    "app_id": app_id,
    "branch": branch,
    "arch": arch,
    "commit": commit,
    "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "permissions": json.loads(Path(out, "permissions.json").read_text())["permissions"],
    "permission_changes": json.loads(changes),
    "permission_changes_approved": approve == "1",
    "sources": Path(source, "SOURCES").read_text().split("\n")[:-1],
}
Path(out, "release.json").write_text(json.dumps(release, indent=2) + "\n")
print(json.dumps({"published": f"app/{app_id}/{arch}/{branch}", "commit": commit}))
PY
