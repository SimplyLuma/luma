#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Restore file modes the OSTree container import changed.

The pipeline imports the image into a bare-user staging repository (a bare
repository would need to write SELinux labels the build host's policy does
not know). That import stores files its owner cannot read with the owner-read
bit added and set-user-ID and set-group-ID removed: /usr/bin/sudo (4111 in the
image) arrived as 0511 and sudo refused to run on every installed system.

  repair_modes.py check REPO COMMIT EXPECTED
      Print every path whose mode in COMMIT differs from EXPECTED; exit 1 if any.
  repair_modes.py tar REPO COMMIT EXPECTED OUTPUT.tar
      Write a tar holding each differing path with EXPECTED's mode and COMMIT's
      content, ownership and xattrs (no parent directories, so theirs stay
      unchanged), to overlay with `ostree commit --tree=ref=COMMIT
      --tree=tar=OUTPUT.tar`; print "PATH OLD NEW" for each (nothing when
      nothing differs).
  repair_modes.py verify REPO OLD NEW EXPECTED
      Exit 1 unless every EXPECTED path in NEW has its expected mode and OLD's
      exact content, ownership and xattrs.

EXPECTED lines are "OCTAL-MODE PATH" in the image's terms (for example
"4111 /usr/bin/sudo"); /etc paths are looked up under /usr/etc, where the
import puts them. Only permission bits (07777) are compared and set.
"""

import io
import sys
import tarfile

import gi

gi.require_version("OSTree", "1.0")
from gi.repository import Gio, GLib, OSTree  # noqa: E402


def tree_path(path):
    path = "/" + path.strip("/")
    if path == "/etc" or path.startswith("/etc/"):
        path = "/usr" + path
    return path


def read_expected(name):
    expected = []
    with open(name, encoding="utf-8") as stream:
        for line in stream:
            line = line.rstrip("\n")
            if not line:
                continue
            mode, _, path = line.partition(" ")
            expected.append((int(mode, 8) & 0o7777, tree_path(path)))
    return expected


def open_repo(path):
    repo = OSTree.Repo.new(Gio.File.new_for_path(path))
    repo.open(None)
    return repo


def mismatches(repo, commit, expected):
    _, root, _ = repo.read_commit(commit, None)
    found = []
    for mode, path in expected:
        node = root.resolve_relative_path(path.lstrip("/"))
        try:
            info = node.query_info("standard::type,unix::mode", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
        except GLib.Error:
            found.append((path, None, mode, None))
            continue
        actual = info.get_attribute_uint32("unix::mode") & 0o7777
        if actual != mode:
            found.append((path, actual, mode, node))
    return found


def read_all(stream):
    chunks = []
    while True:
        data = stream.read_bytes(1 << 20, None).get_data()
        if not data:
            return b"".join(chunks)
        chunks.append(data)


def xattr_headers(xattrs):
    headers = {}
    for name, value in (xattrs.unpack() if xattrs is not None else []):
        name = bytes(name).rstrip(b"\0").decode("utf-8")
        headers["SCHILY.xattr." + name] = bytes(value).decode("utf-8", "surrogateescape")
    return headers


def write_tar(repo, commit, expected, output):
    """Write a tar of the differing paths with the image's modes and the tree's
    content, ownership and xattrs, for `ostree commit --tree=ref=COMMIT
    --tree=tar=OUTPUT`. Parent directories are not in the archive, so their
    metadata stays the tree's. Returns the repaired (path, old, new) list."""
    found = mismatches(repo, commit, expected)
    missing = [path for path, actual, _, _ in found if actual is None]
    if missing:
        raise SystemExit("error: paths missing from the tree: " + ", ".join(missing))
    repaired = []
    with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as archive:
        for path, actual, mode, node in found:
            info = node.query_info("standard::type,unix::uid,unix::gid", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
            entry = tarfile.TarInfo(path.lstrip("/"))
            entry.uid = info.get_attribute_uint32("unix::uid")
            entry.gid = info.get_attribute_uint32("unix::gid")
            entry.mode = mode
            entry.mtime = 0
            entry.uname = entry.gname = ""
            kind = info.get_file_type()
            if kind == Gio.FileType.DIRECTORY:
                node.ensure_resolved()
                _, meta = repo.load_variant(OSTree.ObjectType.DIR_META, node.tree_get_metadata_checksum())
                xattrs = meta.get_child_value(3)
                entry.type = tarfile.DIRTYPE
                entry.pax_headers = xattr_headers(xattrs)
                archive.addfile(entry)
            elif kind == Gio.FileType.REGULAR:
                node.ensure_resolved()
                _, stream, _, xattrs = repo.load_file(node.get_checksum(), None)
                data = read_all(stream)
                entry.size = len(data)
                entry.pax_headers = xattr_headers(xattrs)
                archive.addfile(entry, io.BytesIO(data))
            else:
                raise SystemExit(f"error: {path} is neither a file nor a directory")
            repaired.append((path, actual, mode))
    return repaired


def node_record(repo, root, path):
    node = root.resolve_relative_path(path.lstrip("/"))
    info = node.query_info("standard::type,unix::uid,unix::gid,unix::mode", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
    node.ensure_resolved()
    if info.get_file_type() == Gio.FileType.DIRECTORY:
        _, meta = repo.load_variant(OSTree.ObjectType.DIR_META, node.tree_get_metadata_checksum())
        content, xattrs = None, meta.get_child_value(3)
    else:
        _, stream, _, xattrs = repo.load_file(node.get_checksum(), None)
        content = read_all(stream)
    return (info.get_attribute_uint32("unix::uid"), info.get_attribute_uint32("unix::gid"),
            info.get_attribute_uint32("unix::mode") & 0o7777, content,
            xattrs.unpack() if xattrs is not None else [])


def verify(repo, old, new, expected):
    """Every EXPECTED path in NEW has its expected mode and exactly OLD's
    content, ownership and xattrs."""
    _, old_root, _ = repo.read_commit(old, None)
    _, new_root, _ = repo.read_commit(new, None)
    problems = []
    for mode, path in expected:
        before = node_record(repo, old_root, path)
        after = node_record(repo, new_root, path)
        if after[2] != mode:
            problems.append(f"{path}: mode {after[2]:o}, image has {mode:o}")
        if before[:2] != after[:2] or before[3] != after[3] or before[4] != after[4]:
            problems.append(f"{path}: ownership, content or xattrs changed")
    return problems


def main(argv):
    action = argv[1] if len(argv) > 1 else ""
    if action == "check" and len(argv) == 5:
        repo, expected = open_repo(argv[2]), read_expected(argv[4])
        found = mismatches(repo, argv[3], expected)
        for path, actual, mode, _ in found:
            print(f"{path}: {'missing' if actual is None else format(actual, 'o')}, image has {mode:o}")
        return 1 if found else 0
    if action == "tar" and len(argv) == 6:
        repo, expected = open_repo(argv[2]), read_expected(argv[4])
        for path, actual, mode in write_tar(repo, argv[3], expected, argv[5]):
            print(f"{path} {actual:o} {mode:o}")
        return 0
    if action == "verify" and len(argv) == 6:
        repo, expected = open_repo(argv[2]), read_expected(argv[5])
        problems = verify(repo, argv[3], argv[4], expected)
        if problems:
            print("\n".join(problems))
        return 1 if problems else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
