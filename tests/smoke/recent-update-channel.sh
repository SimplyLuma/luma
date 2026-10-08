#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo_root/config/update/recent.env"
for tool in flock gpg ostree python3 rsync sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'SKIP  Recent transaction test requires %s\n' "$tool"
    exit 0
  }
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

report_reader="$repo_root/scripts/update/read-build-report-value.sh"
printf 'source_revision=abc\nsource_revision=abc\n' >"$work/report-identical"
test "$("$report_reader" "$work/report-identical" source_revision)" = abc
printf 'source_revision=abc\nsource_revision=def\n' >"$work/report-conflicting"
if "$report_reader" "$work/report-conflicting" source_revision >/dev/null 2>&1; then
  printf 'error: conflicting build-report values were accepted\n' >&2
  exit 1
fi

deployment_reader="$repo_root/scripts/update/read-booted-deployment-checksum.py"
deployment_checksum=$(printf 'a%.0s' {1..64})
printf '{"deployments":[{"booted":true,"checksum":"%s"}]}\n' \
  "$deployment_checksum" >"$work/deployment.json"
test "$("$deployment_reader" "$work/deployment.json")" = "$deployment_checksum"
printf '{"deployments":[{"booted":true,"checksum":"%s"},{"booted":true,"checksum":"%s"}]}\n' \
  "$deployment_checksum" "$deployment_checksum" >"$work/deployment-ambiguous.json"
if "$deployment_reader" "$work/deployment-ambiguous.json" >/dev/null 2>&1; then
  printf 'error: ambiguous booted deployment identity was accepted\n' >&2
  exit 1
fi

mkdir -m 0700 "$work/gnupg"
cat >"$work/key.batch" <<'EOF'
Key-Type: RSA
Key-Length: 2048
Name-Real: Project Luma Ephemeral Recent Test
Name-Email: recent-test@projectluma.invalid
Expire-Date: 1d
%no-protection
%commit
EOF
gpg --batch --homedir "$work/gnupg" --generate-key "$work/key.batch" >/dev/null 2>&1
key=$(gpg --batch --homedir "$work/gnupg" --with-colons --fingerprint |
  awk -F: '$1 == "fpr" { print $10; exit }')

ostree init --repo="$work/source" --mode=bare-user-only
mkdir "$work/tree"
printf 'base\n' >"$work/tree/release"
ostree commit --repo="$work/source" --branch=source/test \
  --tree="dir=$work/tree" --subject='Builder base' >/dev/null
printf 'first\n' >"$work/tree/release"
chmod 0750 "$work/tree/release"
ln -s release "$work/tree/current"
printf 'spaced file\n' >"$work/tree/file with spaces"
ln -s 'file with spaces' "$work/tree/link with spaces"
mkdir -p "$work/tree/usr/lib/sysimage/rpm-ostree-base-db"
printf 'ancestral rpmdb\n' >"$work/tree/usr/lib/sysimage/rpm-ostree-base-db/rpmdb.sqlite"
metadata_policy="$repo_root/scripts/update/ostree-export-metadata.py"
ostree commit --repo="$work/source" --branch=source/test \
  --add-metadata-string=rpmostree.test=preserved \
  --add-metadata=rpmostree.clientlayer=true \
  --add-metadata='rpmostree.clientlayer_version=uint32 6' \
  --add-metadata="rpmostree.packages=['builder-local-app']" \
  --add-metadata='rpmostree.modules=@as []' \
  --add-metadata='rpmostree.removed-base-packages=@av []' \
  --add-metadata='rpmostree.replaced-base-packages=@a(vv) []' \
  --add-metadata='rpmostree.replaced-base-remote-packages=@a{sv} {}' \
  --add-metadata-string=rpmostree.state-sha512=builder-state \
  --add-metadata="rpmostree.rpmdb.pkglist=[('luma-test', '0', '1.0', '1', 'noarch')]" \
  --add-metadata='ostree.bootable=true' \
  --add-metadata-string=ostree.linux=kernel-test \
  --add-metadata='ostree.composefs.v0=[byte 0x01, 0x02]' \
  --add-metadata="rpmostree.rpmmd-repos=[{'id': <'test-repo'>}]" \
  --tree="dir=$work/tree" --subject='Recent transaction test 1' >/dev/null
first=$(ostree rev-parse --repo="$work/source" source/test)
if python3 "$metadata_policy" verify "$work/source" "$first" "$first" 2>/dev/null; then
  printf 'error: an unnormalized client-layer export was accepted\n' >&2
  exit 1
fi
# Future client-layer semantics and incomplete inventory must fail closed.
unknown=$(ostree commit --repo="$work/source" --orphan --parent="$first" \
  --tree="ref=$first" --subject='Unknown layer metadata' \
  --add-metadata=rpmostree.clientlayer_future=true)
missing=$(ostree commit --repo="$work/source" --orphan --parent="$first" \
  --tree="ref=$first" --subject='Missing package inventory' \
  --add-metadata=rpmostree.clientlayer=true)
for rejected in "$unknown" "$missing"; do
  if python3 "$metadata_policy" keep "$work/source" "$rejected" >/dev/null 2>&1; then
    printf 'error: unsupported layer metadata was accepted\n' >&2
    exit 1
  fi
done
source_revision=0000000000000000000000000000000000000001
printf 'source_revision=%s\nsource_state=clean\n' \
  "$source_revision" >"$work/build-report.txt"
printf 'completed_utc=2026-08-28T12:00:00Z\n' >>"$work/build-report.txt"

LUMA_UPDATE_SKIP_STATIC_DELTA=1 \
  "$repo_root/scripts/update/promote-ostree-commit.sh" \
    --source-repo "$work/source" --commit "$first" \
    --repo "$work/published" --build-report "$work/build-report.txt" \
    --image-sha256 "$(printf first | sha256sum | awk '{print $1}')" \
    --source-revision "$source_revision" \
    --gpg-key "$key" --gpg-homedir "$work/gnupg" >/dev/null

first_published=$(ostree rev-parse --repo="$work/published" "$LUMA_UPDATE_REF")
test "$first_published" != "$first"
python3 "$metadata_policy" verify "$work/published" "$first" "$first_published"
# Root checksums protect file modes/symlinks/xattrs as well as visible contents.
if ostree ls --repo="$work/published" "$first_published" /usr/lib/sysimage/rpm-ostree-base-db >/dev/null 2>&1; then
  echo "error: stale builder RPMDB survived export" >&2
  exit 1
fi
test "$(ostree show --repo="$work/published" \
  --print-metadata-key=org.projectluma.export-policy "$first_published")" = "'base-image-v2'"
# Changing a filesystem after acceptance must be rejected by the export guard.
mkdir "$work/tampered-tree"
printf 'tampered\n' >"$work/tampered-tree/release"
tampered=$(ostree commit --repo="$work/published" --orphan \
  --tree="dir=$work/tampered-tree" --subject='Tampered tree')
if python3 "$metadata_policy" verify "$work/published" "$first" "$tampered" 2>/dev/null; then
  printf 'error: altered accepted filesystem was accepted\n' >&2
  exit 1
fi
metadata_lost=$(ostree commit --repo="$work/published" --orphan \
  --tree="ref=$first_published" --subject='Lost typed boot and package metadata')
if python3 "$metadata_policy" verify "$work/published" "$first" "$metadata_lost" 2>/dev/null; then
  printf 'error: lost accepted metadata was accepted\n' >&2
  exit 1
fi
# A rejected export may import immutable objects, but cannot change the advertised
# ref or signed summary. This uses the real publisher failure path.
summary_before=$(sha256sum "$work/published/summary")
if LUMA_UPDATE_SKIP_STATIC_DELTA=1 \
  "$repo_root/scripts/update/promote-ostree-commit.sh" \
    --source-repo "$work/source" --commit "$unknown" \
    --repo "$work/published" --build-report "$work/build-report.txt" \
    --image-sha256 "$(printf rejected | sha256sum | awk '{print $1}')" \
    --source-revision "$source_revision" \
    --gpg-key "$key" --gpg-homedir "$work/gnupg" >/dev/null 2>&1; then
  printf 'error: publisher accepted unsupported layer metadata\n' >&2
  exit 1
fi
test "$(ostree rev-parse --repo="$work/published" "$LUMA_UPDATE_REF")" = "$first_published"
test "$(sha256sum "$work/published/summary")" = "$summary_before"
# Already-normalized base commits are valid inputs; no local-layer marker needed.
python3 "$metadata_policy" verify "$work/published" "$first_published" "$first_published"
test "$(ostree show --repo="$work/published" \
  --print-metadata-key=rpmostree.test "$first_published")" = "'preserved'"
test -s "$work/published/summary"
test -s "$work/published/summary.sig"
test -s "$work/published/luma/releases/$first_published.json"
test -s "$work/published/luma/releases/$first_published.json.asc"
test -s "$work/published/luma/luma-recent.gpg"

printf 'second\n' >"$work/tree/release"
ostree commit --repo="$work/source" --branch=source/test \
  --add-metadata='rpmostree.spec=@a{sv} {}' \
  --add-metadata="rpmostree.rpmdb.pkglist=[('luma-test', '0', '2.0', '1', 'noarch')]" \
  --tree="dir=$work/tree" --subject='Recent transaction test 2 (legacy layer)' >/dev/null
second=$(ostree rev-parse --repo="$work/source" source/test)
sed -i 's/2026-08-28T12:00:00Z/2026-08-28T12:01:00Z/' "$work/build-report.txt"
LUMA_UPDATE_SKIP_STATIC_DELTA=1 \
  "$repo_root/scripts/update/promote-ostree-commit.sh" \
    --source-repo "$work/source" --commit "$second" \
    --repo "$work/published" --build-report "$work/build-report.txt" \
    --image-sha256 "$(printf second | sha256sum | awk '{print $1}')" \
    --source-revision "$source_revision" \
    --gpg-key "$key" --gpg-homedir "$work/gnupg" >/dev/null

second_published=$(ostree rev-parse --repo="$work/published" "$LUMA_UPDATE_REF")
test "$second_published" != "$second"
python3 - "$work/published/luma/releases/$second_published.json" \
  "$first_published" "$second" "$LUMA_UPDATE_CHANNEL" "$LUMA_UPDATE_REF" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    manifest = json.load(stream)
assert manifest["previous_commit"] == sys.argv[2]
assert manifest["accepted_deployment_commit"] == sys.argv[3]
assert manifest["channel"] == sys.argv[4]
assert manifest["ref"] == sys.argv[5]
PY

"$repo_root/scripts/update/activate-recent-webroot.sh" \
  --repo "$work/published" --webroot "$work/webroot" >/dev/null
test -L "$work/webroot/current"
test "$(ostree rev-parse --repo="$work/webroot/current" \
  "$LUMA_UPDATE_REF")" = "$second_published"

printf 'Recent signed OSTree transaction: PASS\n'
