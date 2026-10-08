#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# make-release.sh VERSION REF BASE [KIND]
#   BASE: an OSTree checksum or ref already in the rig repository, or
#         "booted" for the booted deployment's full (layered) tree.
#   KIND: plain (default) | break-agent (luma-updated exits at once)
# RIG_KARGS, if set, is a space-separated list of kernel arguments written to
# /usr/lib/bootc/kargs.d/60-luma-update-rig.toml in the release, as a release
# declares them for bootc.
# RIG_PAYLOAD, if set, is a directory (an extracted luma-update RPM) laid over
# the tree, so a rebuilt agent can be tested without re-layering the VM.
# Prints the new commit checksum.
set -euo pipefail
rig=/var/lib/luma-update-test
version=$1 ref=$2 base=$3 kind=${4:-plain}
export GNUPGHOME=$rig/gnupg
fpr=$(cat "$rig/gpg-fingerprint")
if [ "$base" = booted ]; then
  base=$(rpm-ostree status --json | python3 -c 'import json,sys; print(next(d["checksum"] for d in json.load(sys.stdin)["deployments"] if d["booted"]))')
  ostree --repo="$rig/repo" pull-local /ostree/repo "$base" >&2
fi
overlay=$rig/overlays/$version
rm -rf "$overlay"
install -d "$overlay/usr/share/luma-update-rig"
printf '%s\n' "$version" > "$overlay/usr/share/luma-update-rig/release"
if [ -n "${RIG_KARGS:-}" ]; then
  install -d "$overlay/usr/lib/bootc/kargs.d"
  python3 - "$overlay/usr/lib/bootc/kargs.d/60-luma-update-rig.toml" $RIG_KARGS <<'PY'
import json, sys
with open(sys.argv[1], "w") as stream:
    stream.write("# luma-update VM rig\nkargs = " + json.dumps(sys.argv[2:]) + "\n")
PY
fi
if [ "$kind" = break-agent ]; then
  install -d "$overlay/usr/libexec"
  printf '#!/bin/sh\necho "rig: this release deliberately breaks luma-updated" >&2\nexit 1\n' > "$overlay/usr/libexec/luma-updated"
  chmod 0755 "$overlay/usr/libexec/luma-updated"
fi
parent=$(ostree --repo="$rig/repo" rev-parse "$ref" 2>/dev/null || true)
commit=$(ostree --repo="$rig/repo" commit --branch="$ref" \
  ${parent:+--parent="$parent"} \
  --tree=ref="$base" ${RIG_PAYLOAD:+--tree=dir="$RIG_PAYLOAD"} --tree=dir="$overlay" --selinux-policy-from-base \
  --bootable --subject="Luma $version (rig)" \
  --add-metadata-string=version="$version" \
  --gpg-sign="$fpr" --gpg-homedir="$GNUPGHOME")
ostree --repo="$rig/repo" summary --update --gpg-sign="$fpr" --gpg-homedir="$GNUPGHOME" >&2
echo "$commit"
