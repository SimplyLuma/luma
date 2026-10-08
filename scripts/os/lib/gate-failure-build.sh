#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Gate-only: build a deliberately unhealthy copy of a candidate for the VM
# gate's automatic-rollback stage.
#
#   gate-failure-build.sh --source-repo REPO --commit SHA --work DIR --ref REF
#                         [--into-repo REPO --version V --collection-id ID]
#
# By default the build goes into a throwaway repository under DIR. With
# --into-repo it is committed into that (gate-only) repository instead, bound
# to REF and the collection, with SHA as its parent and version V, so a device
# following REF is offered it as an ordinary update; the gate then adds the
# throwaway key to that VM's remote only.
#
# The commit is the candidate's exact tree plus one required greenboot check
# that always fails, labelled with the candidate's own SELinux policy. It is
# signed with a throwaway key generated here, in a throwaway repository under
# DIR, and served only to the gate VM through a separate remote. It is never
# signed with the Luma OS Release key and never enters a published repository.

set -euo pipefail

source_repo=
commit=
work=
ref=
into_repo=
version=gate-forced-failure
collection=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --into-repo) into_repo=${2:?}; shift 2 ;;
    --version) version=${2:?}; shift 2 ;;
    --collection-id) collection=${2:?}; shift 2 ;;
    --source-repo) source_repo=${2:?}; shift 2 ;;
    --commit) commit=${2:?}; shift 2 ;;
    --work) work=${2:?}; shift 2 ;;
    --ref) ref=${2:?}; shift 2 ;;
    *) printf 'usage: %s --source-repo REPO --commit SHA --work DIR --ref REF\n' "$0" >&2; exit 2 ;;
  esac
done
[ -n "$source_repo" ] && [ -n "$commit" ] && [ -n "$work" ] && [ -n "$ref" ] || exit 2

rm -rf "$work"
install -d -m 0700 "$work"
# A short keyring path: with no /run/user/0 (no root login open) gpg puts the
# agent's socket in GNUPGHOME, and a path under the pipeline volume is longer
# than a Unix socket name may be, so the agent cannot start (20260917.9:
# "failed to start gpg-agent", "No agent running").
GNUPGHOME=$(mktemp -d /tmp/lgf.XXXXXX)
chmod 0700 "$GNUPGHOME"
export GNUPGHOME
trap 'gpgconf --kill gpg-agent >/dev/null 2>&1 || true; rm -rf "$GNUPGHOME"' EXIT
gpg --batch --passphrase '' --quick-generate-key \
  'Luma gate forced failure (disposable test key)' ed25519 sign 1d
fingerprint=$(gpg --batch --with-colons --list-secret-keys | awk -F: '$1 == "fpr" { print $10; exit }')
gpg --batch --export "$fingerprint" >"$work/gate-key.gpg"

overlay="$work/overlay"
check="$overlay/usr/lib/greenboot/check/required.d/00-luma-gate-forced-failure.sh"
install -d -m 0755 "$(dirname "$check")"
cat >"$check" <<'SCRIPT'
#!/usr/bin/bash
# Gate-only build: this deployment must be judged unhealthy.
echo 'Luma gate: forced health-check failure' >&2
exit 1
SCRIPT
chmod 0755 "$check"

if [ -n "$into_repo" ]; then
  failure=$(ostree commit --repo="$into_repo" --orphan --parent="$commit" \
    --bind-ref="$ref" \
    --tree="ref=$commit" --tree="dir=$overlay" \
    --owner-uid=0 --owner-gid=0 --selinux-policy-from-base \
    --subject='Luma gate forced-failure build (never published)' \
    --add-metadata-string=version="$version" \
    --add-metadata-string=org.projectluma.gate-forced-failure-of="$commit" \
    --gpg-sign="$fingerprint" --gpg-homedir="$GNUPGHOME")
else
  ostree init --repo="$work/repo" --mode=archive
  ostree pull-local --repo="$work/repo" --untrusted "$source_repo" "$commit"
  failure=$(ostree commit --repo="$work/repo" --branch="$ref" \
    --tree="ref=$commit" --tree="dir=$overlay" \
    --owner-uid=0 --owner-gid=0 --selinux-policy-from-base \
    --subject='Luma gate forced-failure build (never published)' \
    --add-metadata-string=version="$version" \
    --add-metadata-string=org.projectluma.gate-forced-failure-of="$commit" \
    --gpg-sign="$fingerprint" --gpg-homedir="$GNUPGHOME")
  ostree summary --repo="$work/repo" --update --gpg-sign="$fingerprint" --gpg-homedir="$GNUPGHOME"
fi
gpgconf --kill gpg-agent >/dev/null 2>&1 || true
printf '%s\n' "$failure" >"$work/commit"
printf 'forced-failure build %s (tree of %s plus a failing required check)\n' "$failure" "$commit"
