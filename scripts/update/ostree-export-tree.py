#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Normalize and verify the filesystem tree used for an OSTree base export."""

import argparse
import subprocess
import sys

import gi

gi.require_version("OSTree", "1.0")
from gi.repository import Gio, GLib, OSTree  # noqa: E402


REMOVED_PATH = "/usr/lib/sysimage/rpm-ostree-base-db"
REMOVED_PARTS = REMOVED_PATH.strip("/").split("/")


def open_repo(path):
    repo = OSTree.Repo.new(Gio.File.new_for_path(path))
    repo.open(None)
    return repo


def normalize(repo_path, source):
    repo = open_repo(repo_path)
    mtree = OSTree.MutableTree.new_from_commit(repo, source)
    try:
        # walk() returns the parent when given the full path: its recursion
        # stops before the final component.
        _found, parent = mtree.walk(REMOVED_PARTS, 0)
        found, file_checksum, subdir = parent.lookup(REMOVED_PARTS[-1])
    except GLib.Error as error:
        if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND):
            return source
        raise
    if not found:
        return source
    if not found or file_checksum is not None or subdir is None:
        raise ValueError(f"accepted tree path is not a directory: {REMOVED_PATH}")
    parent.remove(REMOVED_PARTS[-1], False)
    _written, root = repo.write_mtree(mtree, None)
    metadata = GLib.Variant("a{sv}", {})
    _written, commit = repo.write_commit(
        None,
        "Normalized Project Luma base export tree",
        f"Removed builder-owned {REMOVED_PATH}",
        metadata,
        root,
        None,
    )
    return commit


def tree_entries(repo_path, commit):
    repo = open_repo(repo_path)
    _found, root, _resolved = repo.read_commit(commit, None)
    entries = {}
    changed_ancestors = {"/", "/usr", "/usr/lib", "/usr/lib/sysimage"}

    def visit(node, path):
        info = node.query_info(
            "standard::*,unix::*", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None
        )
        file_type = info.get_file_type()
        _found_xattrs, xattrs = node.get_xattrs(None)
        record = [
            int(file_type),
            info.get_attribute_uint32("unix::mode"),
            info.get_attribute_uint32("unix::uid"),
            info.get_attribute_uint32("unix::gid"),
            xattrs.print_(False),
        ]
        if file_type == Gio.FileType.DIRECTORY:
            record.append(node.tree_get_metadata_checksum())
            if path not in changed_ancestors:
                record.append(node.tree_get_contents_checksum())
        else:
            record.extend((info.get_size(), node.get_checksum()))
            if file_type == Gio.FileType.SYMBOLIC_LINK:
                record.append(info.get_symlink_target())
        entries[path] = tuple(record)
        if file_type != Gio.FileType.DIRECTORY:
            return
        enumerator = node.enumerate_children(
            "standard::name", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None
        )
        while True:
            child_info = enumerator.next_file(None)
            if child_info is None:
                break
            name = child_info.get_name()
            child_path = f"/{name}" if path == "/" else f"{path}/{name}"
            if child_path == REMOVED_PATH:
                continue
            visit(node.get_child(name), child_path)
        enumerator.close(None)

    visit(root, "/")
    return entries


def verify(repo_path, source, exported):
    try:
        subprocess.run(
            ["ostree", f"--repo={repo_path}", "ls", exported, REMOVED_PATH],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        pass
    else:
        raise ValueError(f"export retains builder-owned {REMOVED_PATH}")
    source_entries = tree_entries(repo_path, source)
    exported_entries = tree_entries(repo_path, exported)
    if source_entries.keys() != exported_entries.keys():
        missing = sorted(source_entries.keys() - exported_entries.keys())
        added = sorted(exported_entries.keys() - source_entries.keys())
        raise ValueError(f"export tree paths changed; missing={missing}, added={added}")
    changed = [path for path in source_entries
               if source_entries[path] != exported_entries[path]]
    if changed:
        raise ValueError(f"export changed file content or directory metadata: {changed}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("normalize", "verify"))
    parser.add_argument("repo")
    parser.add_argument("source")
    parser.add_argument("exported", nargs="?")
    args = parser.parse_args()
    try:
        if args.action == "normalize":
            if args.exported:
                parser.error("normalize does not accept an exported commit")
            print(normalize(args.repo, args.source))
        elif not args.exported:
            parser.error("verify requires the exported commit")
        else:
            verify(args.repo, args.source, args.exported)
    except (GLib.Error, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"error: OSTree export tree: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
