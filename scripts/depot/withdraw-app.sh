#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Withdraw an app's current release from the Luma remote.
#
#   withdraw-app.sh
#
# The request is the sealed artifact snapshot's withdraw.json:
#   {"app_id": ID, "arch": ARCH, "branch": BRANCH, "commit": CURRENT,
#    "target": PREVIOUS | null, "reason": TEXT}
#
# `commit` must still be the published head, so a stale request cannot undo a
# newer release. With a `target` (an ancestor of the head that is still in the
# repository), the ref moves forward to a new signed commit holding exactly the
# target's content: clients update to it like any release, because OSTree
# refuses to move a ref backwards. Without one (the app's first release), the
# head is re-signed as end-of-life, which keeps installed copies working and
# removes the app from AppStream. Run update-remote.sh and sync-remote.sh after.
set -euo pipefail
. "$(dirname -- "$0")/lib.sh"
depot_require_signing_control

request="$DEPOT_SIGNING_INPUTS/withdraw.json"
[ -f "$request" ] || depot_die "no withdraw.json in the sealed inputs"
fingerprint=$(depot_gpg_fingerprint)
read -r app_id arch branch current target reason < <(depot_in_signer python3 -B - "$request" <<'PY'
import json, re, sys
r = json.load(open(sys.argv[1]))
ident = re.compile(r"[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z][A-Za-z0-9_-]*){2,}\Z")
commit = re.compile(r"[0-9a-f]{64}\Z")
assert ident.fullmatch(r["app_id"]), "invalid app id"
assert r["arch"] in ("x86_64", "aarch64"), "invalid arch"
assert r["branch"] in ("stable", "beta", "nightly"), "invalid branch"
assert commit.fullmatch(r["commit"]), "invalid current commit"
target = r.get("target") or "-"
assert target == "-" or commit.fullmatch(target), "invalid target commit"
reason = re.sub(r"[^A-Za-z0-9 .,'()-]", "", r.get("reason") or "Withdrawn")[:120].replace(" ", "_") or "Withdrawn"
print(r["app_id"], r["arch"], r["branch"], r["commit"], target, reason)
PY
)
reason=${reason//_/ }
ref="app/$app_id/$arch/$branch"
head=$(depot_in_signer ostree rev-parse --repo="$depot_repo" "$ref") || depot_die "$ref is not published"
[ "$head" = "$current" ] || depot_die "$ref moved to $head since the withdrawal was requested; nothing changed"

if [ "$target" != - ]; then
  # The target must be the content of an earlier release of this same ref.
  walk=$head found=0
  for _ in $(seq 1 32); do
    walk=$(depot_in_signer ostree rev-parse --repo="$depot_repo" "$walk^" 2>/dev/null) || break
    [ "$walk" = "$target" ] && { found=1; break; }
  done
  [ "$found" = 1 ] || depot_die "$target is not an earlier release of $ref still in the repository"
  depot_log "withdrawing $ref: $head -> content of $target"
  depot_in_signer flatpak build-commit-from --untrusted --no-update-summary --force \
    --gpg-sign="$fingerprint" --gpg-homedir="$DEPOT_KEYS/gnupg" \
    --subject="Withdraw release ${head:0:12}: $reason" \
    --src-repo="$depot_repo" --src-ref="$target" "$depot_repo" "$ref"
else
  depot_log "withdrawing the only release of $ref (end of life)"
  depot_in_signer flatpak build-commit-from --untrusted --no-update-summary --force \
    --gpg-sign="$fingerprint" --gpg-homedir="$DEPOT_KEYS/gnupg" \
    --end-of-life="$reason" --subject="Withdraw release ${head:0:12}: $reason" \
    --src-repo="$depot_repo" --src-ref="$head" "$depot_repo" "$ref"
fi
new=$(depot_in_signer ostree rev-parse --repo="$depot_repo" "$ref")
install -d -m 0700 "$DEPOT_ROOT/releases/$app_id"
printf '{"app_id":"%s","ref":"%s","withdrawn":"%s","restored":"%s","commit":"%s","at":"%s"}\n' \
  "$app_id" "$ref" "$head" "$target" "$new" "$(date -u +%FT%TZ)" \
  >>"$DEPOT_ROOT/releases/$app_id/withdrawals.jsonl"
printf '{"ref":"%s","withdrawn":"%s","restored":"%s","commit":"%s"}\n' "$ref" "$head" "$target" "$new"
