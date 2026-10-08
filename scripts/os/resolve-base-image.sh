#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Print "<tag> <digest>" for the newest Fedora Silverblue 44 x86_64 bootable
# container published under the contract repository
# (LUMA_OS_BASE_IMAGE_REPOSITORY), e.g.
#   44.20260914.0-x86_64 sha256:d5dd...
# The nightly build uses it with --base latest and records the digest in the
# build's provenance; the pinned digest in config/os/release.env changes only
# by a reviewed commit.

set -euo pipefail
. "$(dirname -- "$0")/lib/common.sh"

repository=${LUMA_OS_BASE_IMAGE_REPOSITORY#quay.io/}
[ "$repository" != "$LUMA_OS_BASE_IMAGE_REPOSITORY" ] ||
  luma_os_die "only quay.io base repositories are supported: $LUMA_OS_BASE_IMAGE_REPOSITORY"
curl --fail --silent --show-error --max-time 60 \
  "https://quay.io/api/v1/repository/$repository/tag/?onlyActiveTags=true&limit=100&filter_tag_name=like:${LUMA_OS_FEDORA_RELEASE}." |
python3 -c '
import json, re, sys
release = sys.argv[1]
pattern = re.compile(rf"^{release}\.(\d{{8}})\.(\d+)-x86_64$")
best = None
for tag in json.load(sys.stdin).get("tags", []):
    match = pattern.match(tag.get("name", ""))
    digest = tag.get("manifest_digest", "")
    if match and re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        key = (match.group(1), int(match.group(2)))
        if best is None or key > best[0]:
            best = (key, tag["name"], digest)
if best is None:
    sys.exit("no x86_64 tag found for Fedora " + release)
print(best[1], best[2])
' "$LUMA_OS_FEDORA_RELEASE"
