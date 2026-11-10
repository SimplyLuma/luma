#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""SELinux labels for the physical root of an OSTree system.

ostree labels what it writes inside a deployment and the stateroot's /var, but
`ostree admin init-fs` and `os-init` leave the physical root itself unlabeled:
/, /boot, /ostree, /ostree/repo, /ostree/deploy, the boot.N and root.<slot>
links. Under an enforcing policy rpm-ostree cannot then build a deployment
(bubblewrap is denied on unlabeled_t). Installers label these paths; an image
built by hand has to as well.

usage: label-ostree-physical-root.py --file-contexts FC [--var PATH]... [--check] ROOT

ROOT is the physical root (an image's mounted root filesystem, or /sysroot on a
running phone). Each path gets the label FC assigns to it as seen from the
physical root (/ostree/..., /boot/...). Left as they are: deployment trees and
their .origin files, the deployments' composefs backing directories,
repository objects, the contents of ostree/repo/tmp, the
kernels in /boot/ostree (hard links to repository objects) and the stateroot's
/var, except for each --var PATH below it (for example home), which is labeled
as /var/PATH. --check only reports paths whose label differs and exits 1 if
there are any. Uses libselinux through ctypes; no SELinux Python bindings.
"""
import argparse
import ctypes
import ctypes.util
import os
import stat
import sys

SELABEL_CTX_FILE = 0
SELABEL_OPT_PATH = 3
XATTR = "security.selinux"


class _Opt(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("value", ctypes.c_char_p)]


class Labels:
    def __init__(self, file_contexts: str):
        name = ctypes.util.find_library("selinux") or "libselinux.so.1"
        self.lib = ctypes.CDLL(name, use_errno=True)
        self.lib.selabel_open.restype = ctypes.c_void_p
        self.lib.selabel_open.argtypes = [ctypes.c_int, ctypes.POINTER(_Opt), ctypes.c_uint]
        self.lib.selabel_lookup_raw.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p),
                                                ctypes.c_char_p, ctypes.c_int]
        self.lib.freecon.argtypes = [ctypes.c_char_p]
        opt = _Opt(SELABEL_OPT_PATH, file_contexts.encode())
        self.handle = self.lib.selabel_open(SELABEL_CTX_FILE, ctypes.byref(opt), 1)
        if not self.handle:
            raise OSError(ctypes.get_errno(), f"cannot load {file_contexts}")

    def lookup(self, path: str, mode: int) -> str | None:
        con = ctypes.c_char_p()
        if self.lib.selabel_lookup_raw(self.handle, ctypes.byref(con), path.encode(),
                                       stat.S_IFMT(mode)) != 0:
            return None  # <<none>>: the policy leaves this path alone
        value = con.value.decode()
        return value


def kept(rel: str) -> bool:
    """Paths whose labels ostree owns (relative to the physical root)."""
    parts = rel.split("/")
    if parts[:3] == ["ostree", "repo", "objects"] and len(parts) > 4:
        return True
    if parts[:3] == ["ostree", "repo", "tmp"] and len(parts) > 3:
        return True
    if len(parts) > 4 and parts[:2] == ["ostree", "deploy"] and parts[3] in ("deploy", "backing"):
        return True
    if len(parts) > 3 and parts[:2] == ["boot", "ostree"]:
        return True
    return False


def walk(root: str, var_paths: list[str]):
    """Yield (path on disk, path the policy names, mode) for every labeled path."""
    dev = os.lstat(root).st_dev

    def tree(path: str, logical: str):
        st = os.lstat(path)
        yield path, logical, st.st_mode
        if not stat.S_ISDIR(st.st_mode) or st.st_dev != dev:
            return
        for entry in sorted(os.listdir(path)):
            yield from tree(os.path.join(path, entry), logical.rstrip("/") + "/" + entry)

    def physical(path: str, rel: str):
        st = os.lstat(path)
        parts = rel.split("/") if rel else []
        if len(parts) == 4 and parts[:2] == ["ostree", "deploy"] and parts[3] == "var":
            # The stateroot's /var: ostree labels it; only the named paths here.
            for sub in var_paths:
                target = os.path.join(path, sub)
                if os.path.lexists(target):
                    yield from tree(target, "/var/" + sub)
            return
        if rel and kept(rel):
            return
        yield path, "/" + rel, st.st_mode
        if not stat.S_ISDIR(st.st_mode) or st.st_dev != dev:
            return
        for entry in sorted(os.listdir(path)):
            yield from physical(os.path.join(path, entry), f"{rel}/{entry}" if rel else entry)

    yield from physical(root, "")


def current(path: str) -> str:
    try:
        return os.getxattr(path, XATTR, follow_symlinks=False).rstrip(b"\0").decode()
    except OSError:
        return ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--file-contexts", required=True)
    parser.add_argument("--var", action="append", default=[], metavar="PATH")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("root")
    args = parser.parse_args(argv)
    for sub in args.var:
        if sub.startswith("/") or ".." in sub.split("/"):
            parser.error(f"--var {sub}: a path below /var")
    labels = Labels(args.file_contexts)
    changed = checked = 0
    for path, logical, mode in walk(os.path.abspath(args.root), args.var):
        want = labels.lookup(logical, mode)
        if want is None:
            continue
        checked += 1
        have = current(path)
        if have == want:
            continue
        changed += 1
        if args.check:
            print(f"{logical}: {have or 'no label'} (want {want})")
            continue
        os.setxattr(path, XATTR, want.encode(), follow_symlinks=False)
    verb = "differ" if args.check else "relabeled"
    print(f"{changed} of {checked} paths {verb}", file=sys.stderr)
    return 1 if args.check and changed else 0


if __name__ == "__main__":
    sys.exit(main())
