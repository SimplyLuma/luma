#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""SPDX 2.3 JSON from a Luma build's rpm inventory (the syft fallback).

Input is the packages-installed.tsv that scripts/os/build-image.sh writes from
the image's own rpm database: NAME, EPOCHNUM, VERSION, RELEASE, ARCH,
SHA256HEADER, SOURCERPM, LICENSE. Each RPM becomes an SPDX package with a
purl external reference (pkg:rpm/fedora/...) and the header digest as its
checksum, so the document carries the same identities syft reports for RPMs.
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from urllib.parse import quote


def packages(path):
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 8 or fields[0] == "gpg-pubkey":
                continue
            yield dict(zip(("name", "epoch", "version", "release", "arch", "header", "srpm", "license"), fields))


def document(rows, name, version):
    items = []
    for index, row in enumerate(rows):
        evr = f"{row['version']}-{row['release']}"
        qualifiers = f"arch={row['arch']}"
        if row["epoch"] not in ("", "0"):
            qualifiers += f"&epoch={row['epoch']}"
        if row["srpm"] and row["srpm"] != "(none)":
            qualifiers += f"&upstream={quote(row['srpm'])}"
        items.append({
            "name": row["name"],
            "SPDXID": f"SPDXRef-Package-rpm-{index}",
            "versionInfo": evr if row["epoch"] in ("", "0") else f"{row['epoch']}:{evr}",
            "supplier": "NOASSERTION",
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "comment": f"rpm License tag: {row['license']}",
            "checksums": [{"algorithm": "SHA256", "checksumValue": row["header"]}],
            "externalRefs": [{
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": f"pkg:rpm/fedora/{quote(row['name'])}@{quote(evr)}?{qualifiers}",
            }],
        })
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{name}-{version}",
        "documentNamespace": f"https://simplyluma.com/spdx/{name}-{version}-{uuid.uuid4()}",
        "creationInfo": {
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "creators": ["Tool: luma-rpmdb-spdx"],
        },
        "packages": items,
        "relationships": [
            {"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": item["SPDXID"]}
            for item in items
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    json.dump(document(list(packages(args.packages)), args.name, args.version), sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
