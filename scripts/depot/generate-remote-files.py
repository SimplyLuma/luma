#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Write the Luma remote's descriptor files: luma.flatpakrepo, .flatpakref files, keys.

  generate-remote-files.py --repo REPO --site SITE --gpg-key KEY.gpg
                           [--catalog-key depot-catalog.pub] [--base-url URL]

Layout written under SITE (everything but the OSTree repository itself, which
is synced from REPO to <base-url>/repo/):

  luma.flatpakrepo                     the remote, with its signing key inline
  apps/<app-id>.flatpakref             beta branch (only when a beta ref exists)
  apps/<app-id>.beta.flatpakref        beta branch
  keys/luma-depot.gpg                  OpenPGP public key, binary
  keys/luma-depot.asc                  OpenPGP public key, armored
  keys/depot-catalog.pub               minisign public key for catalog snapshots
  apps/index.json                      refs, commits, root dirtrees, sizes and computed
                                       permissions: read by Hub and the CDN counter

Display names come from --titles (packaging/flatpak/apps/titles.json);
homepages follow the web Depot's /apps/<slug> pattern (ADR-028 section 3).
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REMOTE_TITLE = "Luma"
REMOTE_COMMENT = "Apps made for Luma, for every Linux desktop"
REMOTE_DESCRIPTION = (
    "The Luma remote carries Project Luma's own apps, apps from verified Luma "
    "developers, and the Luma Platform runtime they run on. Every commit and "
    "the summary are signed with the Luma Depot key."
)
HOMEPAGE = "https://simplyluma.com/apps"
REMOTE_NAME = "luma"


def ostree(repo: Path, *args: str) -> str:
    return subprocess.run(["ostree", f"--repo={repo}", *args], check=True,
                          capture_output=True, text=True).stdout


def app_refs(repo: Path) -> list[tuple[str, str, str]]:
    refs = []
    for line in ostree(repo, "refs").split():
        parts = line.split("/")
        if len(parts) == 4 and parts[0] == "app":
            refs.append((parts[1], parts[2], parts[3]))
    return sorted(refs)


def end_of_life(repo: Path, ref: str) -> str | None:
    """The reason a ref's head commit is marked end-of-life, or None.

    A withdrawn app keeps its ref, so installed copies are told through
    Flatpak's standard ostree.endoflife metadata, but it gets no .flatpakref
    and no index entry: nothing new is pointed at it.
    """
    result = subprocess.run(["ostree", f"--repo={repo}", "show",
                             "--print-metadata-key=ostree.endoflife", ref],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip().strip("'") or None


def slug(app_id: str) -> str:
    return app_id.rsplit(".", 1)[-1].lower()


def flatpakref(app_id: str, branch: str, key_b64: str, base_url: str, title: str) -> str:
    lines = [
        "[Flatpak Ref]",
        "Version=1",
        f"Name={app_id}",
        f"Branch={branch}",
        f"Title={title}",
        "IsRuntime=false",
        f"Url={base_url}/repo/",
        f"SuggestRemoteName={REMOTE_NAME}",
        f"RuntimeRepo={base_url}/{REMOTE_NAME}.flatpakrepo",
        f"Homepage={HOMEPAGE}/{slug(app_id)}",
        f"GPGKey={key_b64}",
    ]
    return "\n".join(lines) + "\n"


def flatpakrepo(key_b64: str, base_url: str) -> str:
    lines = [
        "[Flatpak Repo]",
        "Version=1",
        f"Title={REMOTE_TITLE}",
        f"Url={base_url}/repo/",
        f"Homepage={HOMEPAGE}",
        f"Comment={REMOTE_COMMENT}",
        f"Description={REMOTE_DESCRIPTION}",
        f"Icon={base_url}/media/luma-remote.svg",
        "DefaultBranch=beta",
        f"GPGKey={key_b64}",
    ]
    return "\n".join(lines) + "\n"


def _flatpak_size(repo: Path, commit: str, key: str) -> int | None:
    """Read the typed size already decoded by OSTree's metadata printer.

    The CLI converts the serialized GVariant to its numeric value. Applying
    another byte swap corrupts small application sizes into exabytes.
    """
    try:
        text = ostree(repo, "show", f"--print-metadata-key={key}", commit).strip()
    except subprocess.CalledProcessError:
        return None
    match = re.fullmatch(r'uint64 ([0-9]+)', text)
    if match is None:
        raise ValueError(f'Invalid typed Flatpak size metadata: {key}')
    value = int(match.group(1))
    if value > (1 << 64) - 1:
        raise ValueError(f'Flatpak size metadata exceeds uint64: {key}')
    return value


def _commit_tree(repo: Path, commit: str) -> tuple[str | None, str | None]:
    """(root dirtree checksum, parent commit) from the commit object."""
    import gi
    gi.require_version("OSTree", "1.0")
    from gi.repository import Gio, OSTree

    handle = OSTree.Repo.new(Gio.File.new_for_path(str(repo)))
    handle.open(None)
    try:
        _, variant = handle.load_variant(OSTree.ObjectType.COMMIT, commit)
    except Exception:  # pruned parent
        return None, None
    root = OSTree.checksum_from_bytes(variant.get_child_value(6).get_data_as_bytes().get_data())
    return root, OSTree.commit_get_parent(variant)


def commit_info(repo: Path, ref: str, history: int = 3) -> dict:
    commit = ostree(repo, "rev-parse", ref).strip()
    info: dict = {"ref": ref, "commit": commit}
    for key, field in (("xa.installed-size", "installed_bytes"),
                       ("xa.download-size", "download_bytes")):
        value = _flatpak_size(repo, commit, key)
        if value is not None:
            info[field] = value
    # The CDN counter attributes a pull by its root dirtree or delta target;
    # clients may still be pulling a commit or two behind the newest.
    root, parent = _commit_tree(repo, commit)
    info["root_dirtree"] = root
    info["previous_commits"] = []
    info["previous_root_dirtrees"] = []
    while parent and len(info["previous_commits"]) < history:
        root, next_parent = _commit_tree(repo, parent)
        if root is None:
            break
        info["previous_commits"].append(parent)
        info["previous_root_dirtrees"].append(root)
        parent = next_parent
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--gpg-key", type=Path, required=True, help="binary OpenPGP public key")
    parser.add_argument("--gpg-key-armored", type=Path)
    parser.add_argument("--catalog-key", type=Path)
    parser.add_argument("--remote-icon", type=Path)
    parser.add_argument("--base-url", default="https://dl.simplyluma.com")
    parser.add_argument("--releases", type=Path,
                        help="directory of <app-id>/release.json written by publish-app.sh")
    parser.add_argument("--titles", type=Path,
                        help="JSON object mapping app id to its display name")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    key_b64 = base64.b64encode(args.gpg_key.read_bytes()).decode()
    titles = json.loads(args.titles.read_text()) if args.titles else {}

    site = args.site
    (site / "apps").mkdir(parents=True, exist_ok=True)
    (site / "keys").mkdir(parents=True, exist_ok=True)
    (site / "media").mkdir(parents=True, exist_ok=True)

    (site / f"{REMOTE_NAME}.flatpakrepo").write_text(flatpakrepo(key_b64, base_url))
    shutil.copyfile(args.gpg_key, site / "keys/luma-depot.gpg")
    if args.gpg_key_armored:
        shutil.copyfile(args.gpg_key_armored, site / "keys/luma-depot.asc")
    if args.catalog_key:
        shutil.copyfile(args.catalog_key, site / "keys/depot-catalog.pub")
    if args.remote_icon:
        shutil.copyfile(args.remote_icon, site / "media/luma-remote.svg")

    index = {"remote": f"{base_url}/repo/", "apps": []}
    wanted = set()
    withdrawn = []
    for app_id, arch, branch in app_refs(args.repo):
        if end_of_life(args.repo, f"app/{app_id}/{arch}/{branch}"):
            withdrawn.append(f"{app_id}//{branch}")
            continue
        title = titles.get(app_id, app_id.rsplit(".", 1)[-1])
        name = f"{app_id}.{branch}.flatpakref"
        wanted.add(name)
        descriptor = flatpakref(app_id, branch, key_b64, base_url, title)
        (site / "apps" / name).write_text(descriptor)
        if branch == "beta":
            default_name = f"{app_id}.flatpakref"
            wanted.add(default_name)
            (site / "apps" / default_name).write_text(descriptor)
        entry = commit_info(args.repo, f"app/{app_id}/{arch}/{branch}")
        entry.update({"app_id": app_id, "arch": arch, "branch": branch})
        release = args.releases / app_id / "release.json" if args.releases else None
        if release and release.is_file():
            data = json.loads(release.read_text())
            if data.get("commit") == entry["commit"] and data.get("branch") == branch:
                entry["permissions"] = data.get("permissions", [])
                entry["permission_changes"] = data.get("permission_changes", [])
                entry["published_at"] = data.get("published_at")
        index["apps"].append(entry)
    for stale in (site / "apps").glob("*.flatpakref"):
        if stale.name not in wanted:
            stale.unlink()
    (site / "apps/index.json").write_text(json.dumps(index, indent=2) + "\n")
    print(json.dumps({"flatpakref": sorted(wanted), "apps": len(index["apps"]),
                      "end_of_life": sorted(withdrawn)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
