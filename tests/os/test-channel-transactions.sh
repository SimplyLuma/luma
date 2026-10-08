#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Real OSTree transaction tests for publication, promotion and graph signing.
# Runs as root on a Linux host with ostree, gpg and podman (the build host),
# entirely inside a throwaway root with throwaway keys; it never touches the
# production repositories, keys or secrets.
#
#   tests/os/test-channel-transactions.sh [WORKDIR]

set -euo pipefail
repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
work=${1:-$(mktemp -d)}
work=$(realpath "$work")
export LUMA_OS_ROOT="$work/root" LUMA_OS_KEYS="$work/root/keys" LUMA_OS_SECRETS="$work/secrets"
export LUMA_OS_REQUIRE_VOLUME=0
# Reuse the production tools image by pointing at the production storage
# configuration read-only is not possible; build a private one here.
install -d -m 0700 "$LUMA_OS_ROOT" "$LUMA_OS_KEYS" "$LUMA_OS_SECRETS" "$LUMA_OS_ROOT/etc" \
  "$LUMA_OS_ROOT/tmp" "$LUMA_OS_ROOT/locks" "$LUMA_OS_ROOT/builds" "$LUMA_OS_ROOT/ostree"
cp "${LUMA_OS_TEST_STORAGE_CONF:-/mnt/luma-secondary/luma-build/os-release/fs/etc/storage.conf}" "$LUMA_OS_ROOT/etc/storage.conf"

pass=0
failures=0
ok() { printf 'PASS  %s\n' "$1"; pass=$((pass + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; failures=$((failures + 1)); }
expect_fail() {
  local description=$1
  shift
  if "$@" >"$work/last.log" 2>&1; then bad "$description (unexpectedly succeeded)"; else ok "$description"; fi
}
expect_ok() {
  local description=$1
  shift
  if "$@" >"$work/last.log" 2>&1; then ok "$description"; else bad "$description"; sed 's/^/      /' "$work/last.log" | tail -n 20; fi
}

# Throwaway release key with the same unlock path as production.
gnupg="$LUMA_OS_KEYS/os-release-gnupg"
install -d -m 0700 "$gnupg"
printf 'allow-preset-passphrase\npinentry-program /bin/false\n' >"$gnupg/gpg-agent.conf"
head -c 24 /dev/urandom | base64 -w0 >"$LUMA_OS_SECRETS/os-release-gpg.passphrase"
chmod 0600 "$LUMA_OS_SECRETS/os-release-gpg.passphrase"
gpg --batch --homedir "$gnupg" --pinentry-mode loopback \
  --passphrase-file "$LUMA_OS_SECRETS/os-release-gpg.passphrase" \
  --quick-generate-key 'Luma OS transaction test <test@invalid>' ed25519 cert,sign 1d 2>/dev/null
fpr=$(gpg --batch --homedir "$gnupg" --with-colons --list-secret-keys | awk -F: '$1=="fpr"{print $10; exit}')
printf '%s\n' "$fpr" >"$LUMA_OS_KEYS/os-release-fingerprint.txt"
gpg --batch --homedir "$gnupg" --export "$fpr" >"$LUMA_OS_KEYS/luma-os-release.gpg"
gpg --batch --homedir "$gnupg" --export --armor "$fpr" >"$LUMA_OS_KEYS/luma-os-release.asc"

. "$repo_root/scripts/os/lib/common.sh"
. "$repo_root/scripts/os/lib/channel.sh"
os="$repo_root/scripts/os"
ref_nightly="$LUMA_OS_REF_PREFIX/nightly"
candidates="$LUMA_OS_ROOT/ostree/candidate-repo"
ostree init --repo="$candidates" --mode=archive --collection-id="$LUMA_OS_COLLECTION_ID"

# Exercise the actual commit/sign helper with a real protected throwaway key.
# Expire the fixture agent after tree creation, reproducing loss of the preset
# without sleeping or changing production key policy.
install -d "$work/signing-tree" "$work/public-only"
printf 'signing boundary\n' >"$work/signing-tree/content"
expire_after_tree() (
  ostree() {
    local rc
    command ostree "$@"; rc=$?
    if [ "$1" = commit ]; then
      command gpgconf --homedir "$gnupg" --kill gpg-agent || return $?
    fi
    return "$rc"
  }
  luma_os_commit_and_sign "$candidates" --orphan --tree="dir=$work/signing-tree" \
    --bind-ref="$ref_nightly" --add-metadata-string=version=signing-expired-agent
)
if signed_after_tree=$(expire_after_tree 2>"$work/signing-after-tree.log") &&
   [[ "$signed_after_tree" =~ ^[0-9a-f]{64}$ ]] &&
   python3 - "$candidates" "$signed_after_tree" "$work/public-only" "$LUMA_OS_KEYS/luma-os-release.gpg" <<'VERIFY'
import sys,gi
gi.require_version('OSTree','1.0')
from gi.repository import Gio,OSTree
repo=OSTree.Repo.new(Gio.File.new_for_path(sys.argv[1]));repo.open(None)
result=repo.verify_commit_ext(sys.argv[2],Gio.File.new_for_path(sys.argv[3]),
                              Gio.File.new_for_path(sys.argv[4]),None)
assert result.count_valid()>0, 'Prepared commit has no valid public-key signature'
VERIFY
then ok 'tree creation precedes unlock; an expired agent is recovered and public signature verifies'
else bad 'expired signing agent was not recovered'; fi
if (
  luma_os_gpg_unlock() { :; }
  command gpgconf --homedir "$gnupg" --kill gpg-agent
  luma_os_commit_and_sign "$candidates" --orphan --tree="dir=$work/signing-tree" \
    --bind-ref="$ref_nightly" --add-metadata-string=version=signing-refused-agent
) >"$work/signing-refused.out" 2>"$work/signing-refused.log"; then
  bad 'signature failure unexpectedly returned a commit'
elif [ -s "$work/signing-refused.out" ]; then
  bad 'signature failure leaked an unsigned commit as success output'
else ok 'signature failure refuses the unsigned commit and emits no success checksum'; fi

# make_candidate BUILD_ID PARENT CONTENT GATE_PREVIOUS GATE_UPDATE GATE_ROLLBACK
make_candidate() {
  local id=$1 parent=$2 content=$3 previous=$4 update=$5 rollback=$6 tree build commit parent_args package_sha
  build="$LUMA_OS_ROOT/builds/$id"
  tree="$work/tree-$id"
  install -d "$tree/usr/lib" "$tree/usr/etc" "$build"
  printf 'NAME=Luma\nBUILD_ID=%s\n' "$id" >"$tree/usr/lib/os-release"
  printf '%s\n' "$content" >"$tree/usr/lib/luma-content"
  head -c 200000 /dev/urandom >"$tree/usr/lib/luma-payload-$id"
  printf 'bash\t0\t5.3\t1.fc44\tx86_64\thdr\tbash.src.rpm\tGPL\n' >"$build/packages-installed.tsv"
  # The publication record also requires the test package pin set.
  # This synthetic fixture never claims a real RPM gate.
  package_sha=$(sha256sum "$build/packages-installed.tsv" | awk '{print $1}')
  awk -F '\t' -v sha="$package_sha" '{print $1 "-" $3 "-" $4 "." $5, sha, $6}' \
    "$build/packages-installed.tsv" >"$build/luma-packages.manifest"
  parent_args=(--orphan)
  [ -z "$parent" ] || { ostree pull-local --repo="$candidates" --commit-metadata-only "$(luma_os_channel_repo nightly)" "$parent" >/dev/null 2>&1 || true; parent_args=(--orphan --parent="$parent"); }
  commit=$(ostree commit --repo="$candidates" "${parent_args[@]}" --tree=dir="$tree" \
    --owner-uid=0 --owner-gid=0 --bind-ref="$ref_nightly" \
    --add-metadata-string=version="1.0.0-nightly.$id" \
    --add-metadata=rpmostree.rpmdb.pkglist="@a(stsss) [('bash', uint64 0, '5.3', '1.fc44', 'x86_64')]" \
    --add-metadata-string=org.projectluma.build-id="$id")
  (luma_os_gpg_unlock; ostree gpg-sign --repo="$candidates" --gpg-homedir="$gnupg" "$commit" "$fpr"; luma_os_gpg_lock)
  printf 'LUMA_BUILD_ID=%s\nLUMA_OS_VERSION=1.0.0-nightly.%s\nLUMA_OS_CHANNEL=nightly\nLUMA_SOURCE_REVISION=%s\nLUMA_SOURCE_DIRTY=false\nLUMA_IMAGE_TAG=none\nLUMA_BUILD_COMPLETED=2026-09-15T00:00:00Z\n' \
    "$id" "$id" "$(printf '%040d' 1)" >"$build/build.env"
  printf 'LUMA_EXPORT_REF=%s\nLUMA_EXPORT_CANDIDATE=%s\nLUMA_EXPORT_PARENT=%s\n' "$ref_nightly" "$commit" "$parent" >"$build/export.env"
  python3 - "$build" "$id" "$commit" "$previous" "$update" "$rollback" <<'PY'
import json, sys
build, bid, commit, previous, update, rollback = sys.argv[1:7]
json.dump({"schema": "org.projectluma.os-gate/v1", "commit": commit, "previous_commit": previous or None,
           "result": "pass", "fresh": "pass", "no_account": "pass", "update": update, "rollback": rollback,
           "completed_utc": "2026-09-15T00:00:00Z"}, open(f"{build}/gate-result.json", "w"))
json.dump({"build_id": bid, "version": f"1.0.0-nightly.{bid}", "source": {"revision": "0" * 40, "dirty": False},
           "recipe": {"digest": "1" * 64}, "base_image": {"digest": "sha256:" + "2" * 64},
           "luma_packages": {"digest": "3" * 64}, "sbom": {"sha256": "4" * 64}}, open(f"{build}/provenance.json", "w"))
json.dump({"spdxVersion": "SPDX-2.3", "packages": []}, open(f"{build}/sbom.spdx.json", "w"))
open(f"{build}/os-release", "w").write("NAME=Luma\n")
PY
  printf '%s\n' "$commit"
}

# Exercise the native signing boundary without a product fault flag. Only
# the private fixture's first release-metadata copy can expire its own agent;
# the real cp and publication/signing implementations remain unchanged.
export LUMA_OS_TEST_TRANSACTION_WORK="$work"
cp() {
  command cp "$@" || return $?
  local test_root="$LUMA_OS_TEST_TRANSACTION_WORK/root"
  if [ "$#" -eq 5 ] && [ "$LUMA_OS_ROOT" = "$test_root" ] && \
      [ "$LUMA_OS_KEYS" = "$test_root/keys" ] && \
      [[ "$1" == "$test_root/builds/"*/provenance.json ]] && \
      [[ "$5" == "$test_root/publish/public-repo/luma/releases/"* ]] && \
      [ ! -e "$LUMA_OS_TEST_TRANSACTION_WORK/agent-expired-after-summary" ]; then
    [ -s "$test_root/publish/public-repo/summary.sig" ] || return 1
    command gpgconf --homedir "$LUMA_OS_KEYS/os-release-gnupg" --kill gpg-agent || return $?
    printf '%s\n' "$5" >"$LUMA_OS_TEST_TRANSACTION_WORK/agent-expired-after-summary"
  fi
}
export -f cp

public="$LUMA_OS_ROOT/publish/public-repo"
nightly_repo=$(luma_os_channel_repo nightly)
beta_repo=$(luma_os_channel_repo beta)
stable_repo=$(luma_os_channel_repo stable)
for channel in nightly beta stable; do
  [ "$(luma_os_channel_repo "$channel")" = "$public" ] && \
    ok "$channel routes to the public repository without a credential" || bad "$channel public repository routing"
done

# 1. First release of an empty channel.
a=$(make_candidate 20260915.1 '' one '' not-applicable not-applicable)
expect_ok 'first release publishes to an empty channel' "$os/publish-channel.sh" --build-id 20260915.1
[ -s "$work/agent-expired-after-summary" ] && ok 'test signing agent expired after the signed summary and before metadata signatures' || bad 'test agent-expiry hook did not run'
[ "$(ostree rev-parse --repo="$nightly_repo" "$ref_nightly")" = "$a" ] && ok 'nightly ref points to the first release' || bad 'nightly ref after first release'
gpg --batch --homedir "$gnupg" --verify "$nightly_repo/luma/releases/1.0.0-nightly.20260915.1/manifest.json.asc" "$nightly_repo/luma/releases/1.0.0-nightly.20260915.1/manifest.json" 2>/dev/null &&
  ok 'release manifest signature verifies' || bad 'release manifest signature'
if ostree static-delta list --repo="$nightly_repo" | grep -Eq "^$a\$"; then bad 'a from-empty delta was generated'; else ok 'no from-empty delta (consecutive deltas only)'; fi

# 2. A second release needs the update and rollback stages.
b_skip=$(make_candidate 20260915.2 "$a" two "$a" pass skipped)
expect_fail 'a gate with a skipped rollback is refused once the channel has releases' "$os/publish-channel.sh" --build-id 20260915.2
b_wrong=$(make_candidate 20260915.3 "$a" two "$(printf '%064d' 9)" pass pass)
expect_fail 'a gate that updated from a different head is refused' "$os/publish-channel.sh" --build-id 20260915.3
b=$(make_candidate 20260915.4 "$a" two "$a" pass pass)
expect_ok 'second release publishes with update and rollback proven' "$os/publish-channel.sh" --build-id 20260915.4
[ "$(ostree rev-parse --repo="$nightly_repo" "$ref_nightly^")" = "$a" ] && ok 'history is forward-only (parent is the previous head)' || bad 'history parent'
ostree static-delta list --repo="$nightly_repo" | grep -q "$a-$b" && ok "delta from the previous head generated" || bad 'delta from previous head'
[ "$(ostree static-delta list --repo="$nightly_repo" | grep -c .)" = 1 ] && ok 'only the delta from the previous head exists' || bad 'unexpected static deltas'

# 3. Forward-only: a candidate whose parent is not the head.
c=$(make_candidate 20260915.5 '' three "$b" pass pass)
expect_fail 'a candidate not descending from the head is refused' "$os/publish-channel.sh" --build-id 20260915.5
sed -i 's/LUMA_SOURCE_DIRTY=false/LUMA_SOURCE_DIRTY=true/' "$LUMA_OS_ROOT/builds/20260915.5/build.env"
expect_fail 'a dirty build is refused' "$os/publish-channel.sh" --build-id 20260915.5

# 4. Fresh client verification with only the public key.
client="$work/client"
ostree init --repo="$client" --mode=archive >/dev/null
ostree remote add --repo="$client" --gpg-import="$LUMA_OS_KEYS/luma-os-release.gpg" \
  --set=gpg-verify=true --set=gpg-verify-summary=true --collection-id="$LUMA_OS_COLLECTION_ID" \
  luma "file://$nightly_repo" "$ref_nightly"
expect_ok 'a fresh client pulls the nightly head through its delta' ostree pull --repo="$client" luma "$ref_nightly"
printf 'tamper' >>"$nightly_repo/summary"
expect_fail 'a tampered summary is rejected by the client' ostree pull --repo="$client" luma "$ref_nightly"
(luma_os_gpg_unlock; ostree summary --repo="$nightly_repo" --update --gpg-sign="$fpr" --gpg-homedir="$gnupg"; luma_os_gpg_lock)

# 5. Promotion.
expect_fail 'promotion before the soak period is refused' "$os/promote-channel.sh" --from nightly --to beta --commit "$b"
before=$(ostree refs --repo="$nightly_repo" | sort | tr '\n' ' ')
expect_ok 'promotion dry run succeeds' "$os/promote-channel.sh" --from nightly --to beta --commit "$b" --dry-run --soak-override test
[ "$(ostree refs --repo="$nightly_repo" | sort | tr '\n' ' ')" = "$before" ] && ok 'dry run changed no refs' || bad 'dry run changed refs'
expect_ok 'nightly -> beta promotion' "$os/promote-channel.sh" --from nightly --to beta --commit "$b" --soak-override 'transaction test' --summary 'Test'
beta=$(ostree rev-parse --repo="$beta_repo" "$LUMA_OS_REF_PREFIX/beta")
[ "$(ostree ls --repo="$beta_repo" --checksum --dironly "$beta" /)" = "$(ostree ls --repo="$nightly_repo" --checksum --dironly "$b" /)" ] &&
  ok 'beta commit carries the identical tree' || bad 'beta tree identity'
[ "$(ostree show --repo="$beta_repo" --print-metadata-key=version "$beta")" = "'1.0.0-beta.1'" ] && ok 'beta version is 1.0.0-beta.1' || bad 'beta version'
[ "$(ostree show --repo="$beta_repo" --print-metadata-key=org.projectluma.promoted-from "$beta")" = "'$b'" ] && ok 'beta records its source commit' || bad 'promoted-from metadata'
[ "$(python3 "$repo_root/scripts/os/lib/ostree_metadata.py" timestamp "$beta_repo" "$beta")" = "$(python3 "$repo_root/scripts/os/lib/ostree_metadata.py" timestamp "$nightly_repo" "$b")" ] &&
  ok 'the promoted commit keeps the source commit timestamp' || bad 'promoted commit timestamp differs from its source'
expect_fail 'promoting a commit that is not on the source channel is refused' "$os/promote-channel.sh" --from beta --to stable --commit "$b" --version 1.0.0 --soak-override test
expect_fail 'stable needs an explicit version' "$os/promote-channel.sh" --from beta --to stable --commit "$beta" --soak-override test
expect_fail 'nightly -> stable is not a promotion path' "$os/promote-channel.sh" --from nightly --to stable --commit "$b" --version 1.0.0
expect_ok 'beta -> stable promotion into the public repository' "$os/promote-channel.sh" --from beta --to stable --commit "$beta" --version 1.0.0 --soak-override 'transaction test'
stable=$(ostree rev-parse --repo="$stable_repo" "$LUMA_OS_REF_PREFIX/stable")
[ "$(ostree ls --repo="$public" --checksum --dironly "$stable" /)" = "$(ostree ls --repo="$nightly_repo" --checksum --dironly "$b" /)" ] &&
  ok 'stable commit carries the nightly tree' || bad 'stable tree identity'
for channel in nightly beta stable; do
  case "$channel" in nightly) expected=$b ;; beta) expected=$beta ;; stable) expected=$stable ;; esac
  ref="$LUMA_OS_REF_PREFIX/$channel"
  [ "$(ostree rev-parse --repo="$public" "$ref")" = "$expected" ] && \
    ok "public repository advertises exact $channel head" || bad "public $channel head"
  expect_ok "fresh public-key client verifies $channel summary and commit" \
    luma_os_verify_as_client "$public" "$ref" "$expected"
  version=$(ostree show --repo="$public" --print-metadata-key=version "$expected" | tr -d "'")
  gpg --batch --homedir "$gnupg" --verify "$public/luma/releases/$version/manifest.json.asc" \
    "$public/luma/releases/$version/manifest.json" 2>/dev/null && \
    ok "$channel public release manifest signature verifies" || bad "$channel public manifest signature"
done
expect_fail 'a stable version that does not increase is refused' "$os/promote-channel.sh" --from beta --to stable --commit "$beta" --version 1.0.0 --soak-override test

# 6. Graph signing with a throwaway minisign key.
install -d -m 0700 "$LUMA_OS_KEYS/update-graph-minisign"
head -c 24 /dev/urandom | base64 -w0 >"$LUMA_OS_SECRETS/update-graph-minisign.password"
chmod 0600 "$LUMA_OS_SECRETS/update-graph-minisign.password"
LUMA_OS_TOOLS_MOUNTS="$LUMA_OS_KEYS/update-graph-minisign" luma_os_tools sh -c '
  IFS= read -r pw || [ -n "$pw" ]; printf "%s\n%s\n" "$pw" "$pw" |
  minisign -G -f -p "$1/luma-update-graph.pub" -s "$1/luma-update-graph.key" >/dev/null' sh "$LUMA_OS_KEYS/update-graph-minisign" \
  <"$LUMA_OS_SECRETS/update-graph-minisign.password"
export LUMA_OS_GRAPH_KEY_ID=$(head -n 1 "$LUMA_OS_KEYS/update-graph-minisign/luma-update-graph.pub" | awk '{print $NF}')
now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
graph() {
  python3 - "$1" "$2" "$3" "${4:-stable}" <<'PY'
import json, sys
commit, version, generated, channel = sys.argv[1:5]
print(json.dumps({"schema_version": 1, "channel": channel, "arch": "x86_64", "generated_at": generated,
  "releases": [{"version": version, "commit": commit, "released_at": generated,
    "rollout": {"start_at": generated, "start_percentage": 0.05, "duration_minutes": 4320},
    "paused": False, "deadend": False, "deadend_reason": None, "barrier": False,
    "importance": "normal", "notes_url": None, "summary": "Test", "download_bytes_estimate": 1}]}))
PY
}
graph "$stable" 1.0.0 "$now" >"$work/graph-good.json"
graph "$b" 1.0.0 "$now" >"$work/graph-unpublished.json"
expect_ok 'graph signing dry run' "$os/sign-update-graph.sh" --channel stable --source "$work/graph-good.json" --dry-run --no-sync
[ ! -e "$LUMA_OS_ROOT/publish/graph/stable.json" ] && ok 'dry run installed nothing' || bad 'dry run installed a graph'
expect_fail 'a graph naming a commit not published on its channel is refused' "$os/sign-update-graph.sh" --channel stable --source "$work/graph-unpublished.json" --no-sync
expect_ok 'graph signing installs graph and signature' "$os/sign-update-graph.sh" --channel stable --source "$work/graph-good.json" --no-sync
[ ! -e "$LUMA_OS_ROOT/webroot/current" ] && ok 'staging a graph does not switch the served generation' || bad 'signing served a graph without explicit sync'
LUMA_OS_TOOLS_MOUNTS="$LUMA_OS_KEYS/update-graph-minisign $LUMA_OS_ROOT/publish/graph" luma_os_tools \
  minisign -V -p "$LUMA_OS_KEYS/update-graph-minisign/luma-update-graph.pub" -m "$LUMA_OS_ROOT/publish/graph/stable.json" >/dev/null 2>&1 &&
  ok 'installed graph signature verifies with the public key' || bad 'graph signature verification'
expect_ok 'an unchanged graph is a no-op' "$os/sign-update-graph.sh" --channel stable --source "$work/graph-good.json" --no-sync
# Change only Hub's envelope date, retaining release and rollout dates.
python3 - "$work/graph-good.json" "$work/graph-hub-date.json" <<'PY'
import json, sys
graph = json.load(open(sys.argv[1]))
graph["generated_at"] = "2026-01-01T00:00:00Z"
json.dump(graph, open(sys.argv[2], "w"))
PY
before=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generated_at"])' "$LUMA_OS_ROOT/publish/graph/stable.json")
expect_ok 'a graph differing only in Hub'"'"'s generated_at is a no-op within the re-sign window' "$os/sign-update-graph.sh" --channel stable --source "$work/graph-hub-date.json" --no-sync
[ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generated_at"])' "$LUMA_OS_ROOT/publish/graph/stable.json")" = "$before" ] &&
  ok 'the signed graph kept its date' || bad 'the signed graph changed within the re-sign window'
# Age the signed copy past the window: the same graph is signed again with a
# new date chosen by the signer, not Hub's.
python3 -c '
import json, sys
path = sys.argv[1]
graph = json.load(open(path))
graph["generated_at"] = "2026-01-01T00:00:00Z"
json.dump(graph, open(path, "w"))' "$LUMA_OS_ROOT/publish/graph/stable.json"
expect_fail 'a modified previous graph is rejected even inside its re-sign window' "$os/sign-update-graph.sh" --channel stable --source "$work/graph-hub-date.json" --no-sync
# Age a legitimately signed test generation, rather than bypassing the previous
# signature check by modifying bytes in place without their signature.
LUMA_OS_TOOLS_MOUNTS="$LUMA_OS_KEYS/update-graph-minisign $LUMA_OS_ROOT/publish/graph" luma_os_tools \
  minisign -S -s "$LUMA_OS_KEYS/update-graph-minisign/luma-update-graph.key" \
  -m "$LUMA_OS_ROOT/publish/graph/stable.json" \
  -x "$LUMA_OS_ROOT/publish/graph/stable.json.minisig" <"$LUMA_OS_SECRETS/update-graph-minisign.password"
expect_ok 'an unchanged graph older than the re-sign window is signed again' "$os/sign-update-graph.sh" --channel stable --source "$work/graph-hub-date.json" --no-sync
after=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generated_at"])' "$LUMA_OS_ROOT/publish/graph/stable.json")
[ "$after" != 2026-01-01T00:00:00Z ] && [[ "$after" > "$before" || "$after" = "$before" ]] &&
  ok 'the re-signed graph carries the signer'"'"'s date' || bad "re-signed graph date: $after"

# Every publicly selectable channel has its own signed graph. The public
# repository being shared never permits a cross-channel/version graph entry.
for channel in nightly beta; do
  case "$channel" in nightly) commit=$b; version=1.0.0-nightly.20260915.4 ;; beta) commit=$beta; version=1.0.0-beta.1 ;; esac
  graph "$commit" "$version" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$channel" >"$work/graph-$channel.json"
  expect_ok "$channel graph signs against its public published release" "$os/sign-update-graph.sh" \
    --channel "$channel" --source "$work/graph-$channel.json" --no-sync
  LUMA_OS_TOOLS_MOUNTS="$LUMA_OS_KEYS/update-graph-minisign $LUMA_OS_ROOT/publish/graph" luma_os_tools \
    minisign -V -p "$LUMA_OS_KEYS/update-graph-minisign/luma-update-graph.pub" \
    -m "$LUMA_OS_ROOT/publish/graph/$channel.json" >/dev/null 2>&1 && \
    ok "$channel graph signature verifies with only the public key" || bad "$channel graph signature"
  if python3 - "$LUMA_OS_ROOT/publish/graph/$channel.json" "$channel" "$commit" "$version" <<'PY'
import json, sys
graph = json.load(open(sys.argv[1]))
assert graph["channel"] == sys.argv[2]
assert [(r["commit"], r["version"]) for r in graph["releases"]] == [(sys.argv[3], sys.argv[4])]
PY
  then ok "$channel graph contains the exact published channel/version/commit"
  else bad "$channel graph content"
  fi
  printf '\n ' >>"$LUMA_OS_ROOT/publish/graph/$channel.json"
  if LUMA_OS_TOOLS_MOUNTS="$LUMA_OS_KEYS/update-graph-minisign $LUMA_OS_ROOT/publish/graph" luma_os_tools \
    minisign -V -p "$LUMA_OS_KEYS/update-graph-minisign/luma-update-graph.pub" \
    -m "$LUMA_OS_ROOT/publish/graph/$channel.json" >"$work/last.log" 2>&1; then
    bad "$channel graph tamper is rejected (unexpectedly verified)"
  else
    ok "$channel graph tamper is rejected"
  fi
done

if python3 - "$LUMA_OS_ROOT/publish/graph/stable.json" "$stable" <<'PY'
import json, sys
graph = json.load(open(sys.argv[1]))
assert graph["channel"] == "stable"
assert [(r["commit"], r["version"]) for r in graph["releases"]] == [(sys.argv[2], "1.0.0")]
PY
then ok 'stable graph contains the exact published channel/version/commit'
else bad 'stable graph content'
fi
printf '\n ' >>"$LUMA_OS_ROOT/publish/graph/stable.json"
if LUMA_OS_TOOLS_MOUNTS="$LUMA_OS_KEYS/update-graph-minisign $LUMA_OS_ROOT/publish/graph" luma_os_tools \
  minisign -V -p "$LUMA_OS_KEYS/update-graph-minisign/luma-update-graph.pub" \
  -m "$LUMA_OS_ROOT/publish/graph/stable.json" >"$work/last.log" 2>&1; then
  bad 'stable graph tamper is rejected (unexpectedly verified)'
else
  ok 'stable graph tamper is rejected'
fi

printf '\n%s passed, %s failed\n' "$pass" "$failures"
[ "$failures" -eq 0 ]
