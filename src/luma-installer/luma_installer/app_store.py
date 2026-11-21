# SPDX-License-Identifier: Apache-2.0
"""One store for every application tree (ADR-052, Applications 3).

Trees are commits in a single OSTree repository in ``bare-user-only`` mode, the
mode Flatpak uses for per-person installations: every file is an ordinary file
named by the checksum of its content and mode, so a file that two applications
contain is stored once. A tree is used through a checkout made of hard links to
those files; the launcher mounts it read-only. No mount service and no
privilege is involved.

* A new version of an application is a new commit on its ref; the previous
  commit stays as its parent, and rolling back moves the ref to it.
* Space is reclaimed by ref: removing an application deletes its refs and
  prunes whatever no other ref reaches.
* An application's SELinux category pair is not kept here: it is allocated with
  every other installed application's (``capsule_identity``).

A ``bare-user-only`` repository keeps each file's mode in the stored file's own
mode bits. Nothing else may change them: a recursive ``chmod`` or an ACL change
over the store alters what every tree in it means. ``verify`` checks for that.

"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from . import closure
from .errors import InstallerError

FILE_TYPE = "container_ro_file_t"   # readable and executable by every application domain, writable by none
ROLLBACK_DEPTH = 1                  # commits kept behind each ref


def default_root() -> Path:
    return Path(os.environ.get("LUMA_APP_STORE") or Path.home() / ".local/share/luma/apps")


def selinux_enabled() -> bool:
    return Path("/sys/fs/selinux/enforce").exists()


class Store:
    def __init__(self, root: Path | None = None, fsync: bool = True) -> None:
        self.root = (root or default_root()).absolute()
        self.repo = self.root / "repo"
        self.checkouts = self.root / "checkouts"
        self.packages = self.root / "packages"
        self.state_path = self.root / "state.json"
        self.fsync = fsync

    # -- plumbing ---------------------------------------------------------------------

    def _ostree(self, *arguments: str, userns: bool = False, timeout: int = 3600, check: bool = True) -> subprocess.CompletedProcess[str]:
        prefix = closure.userns_prefix() if userns else []
        return closure._run([*prefix, "ostree", f"--repo={self.repo}", *arguments], timeout=timeout, check=check)

    def ensure(self) -> "Store":
        if (self.repo / "config").is_file():
            return self
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for folder in (self.checkouts, self.packages):
            folder.mkdir(exist_ok=True)
        closure._run(["ostree", f"--repo={self.repo}", "init", "--mode=bare-user-only"])
        if selinux_enabled():
            # New objects and checkouts inherit the type of the folder they are made in.
            subprocess.run(["chcon", "-R", "-t", FILE_TYPE, str(self.repo), str(self.checkouts)],
                           capture_output=True, check=False)
        return self

    @contextmanager
    def _locked(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(self.root / ".lock", "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def _state(self) -> dict[str, object]:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _write_state(self, state: dict[str, object]) -> None:
        partial = self.state_path.with_suffix(".partial")
        partial.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
        partial.replace(self.state_path)

    # -- refs --------------------------------------------------------------------------

    @staticmethod
    def app_ref(app_id: str, architecture: str = "") -> str:
        return f"app/{app_id}/{architecture or closure.host_architecture()}"

    @staticmethod
    def userland_ref(name: str, architecture: str = "") -> str:
        return f"userland/{name}/{architecture or closure.host_architecture()}"

    @staticmethod
    def graphics_ref(name: str, architecture: str = "") -> str:
        return f"graphics/{name}/{architecture or closure.host_architecture()}"

    @staticmethod
    def pending_ref(app_id: str, architecture: str = "") -> str:
        """Where a new tree of an application waits until it has been tried and the application is closed."""
        return f"pending/{app_id}/{architecture or closure.host_architecture()}"

    def promote(self, ref: str, commit: str) -> None:
        """Make ``commit`` the tree ``ref`` names; its parent stays behind it for rollback."""
        with self._locked():
            self._ostree("reset", ref, commit)

    def delete_ref(self, ref: str) -> None:
        with self._locked():
            self._ostree("refs", "--delete", ref, check=False)

    def has_commit(self, commit: str) -> bool:
        return bool(commit) and self._ostree("show", commit, check=False).returncode == 0

    def refs(self, prefix: str = "") -> list[str]:
        if not (self.repo / "config").is_file():
            return []
        listed = self._ostree("refs").stdout.split()
        return sorted(ref for ref in listed if ref.startswith(prefix))

    def has_ref(self, ref: str) -> bool:
        return self.resolve(ref) != ""

    def resolve(self, ref: str) -> str:
        if not (self.repo / "config").is_file():
            return ""
        result = self._ostree("rev-parse", ref, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    def parent(self, commit: str) -> str:
        result = self._ostree("rev-parse", f"{commit}^", check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    def history(self, ref: str) -> list[dict[str, str]]:
        """Newest first: the commit the ref names, then what is kept behind it."""
        entries, commit = [], self.resolve(ref)
        while commit:
            shown = self._ostree("show", commit, check=False)
            if shown.returncode != 0:
                break  # pruned
            # After the header lines comes a blank line, then the indented subject.
            subject = next((line.strip() for line in shown.stdout.splitlines() if line.startswith("    ")), "")
            date = next((line.split(":", 1)[1].strip() for line in shown.stdout.splitlines() if line.startswith("Date:")), "")
            entries.append({"commit": commit, "date": date, "subject": subject})
            commit = self.parent(commit)
        return entries

    # -- trees -------------------------------------------------------------------------

    def commit_tree(self, tree: Path, ref: str, record: dict[str, object], subject: str = "", parent: str = "") -> str:
        """Commit a built root. The record travels inside the tree.

        ``parent`` is the commit kept behind this one when it is made on another ref than its predecessor's."""
        self.ensure()
        closure._write_in_tree(tree, closure.RECORD_PATH, closure.record_json(record))
        arguments = ["commit", f"--branch={ref}", f"--tree=dir={tree}", "--canonical-permissions", "--no-xattrs",
                     f"--subject={subject or ref}"]
        if not self.fsync:
            arguments.append("--fsync=false")
        if parent:
            arguments.append(f"--parent={parent}")
        with self._locked():
            return self._ostree(*arguments, userns=True).stdout.strip()

    def read_record(self, ref: str) -> dict[str, object]:
        result = self._ostree("cat", ref, "/" + closure.RECORD_PATH, check=False)
        if result.returncode != 0:
            raise InstallerError(f"{ref} is not in the application store.")
        return json.loads(result.stdout)

    def pull(self, source: Path, refs: list[str] | None = None) -> list[str]:
        """Copy refs, with their history, from another store's repository on this device."""
        self.ensure()
        wanted = refs or closure._run(["ostree", f"--repo={source}", "refs"]).stdout.split()
        with self._locked():
            # Every object is checked against its name on the way in: a repository of this
            # kind keeps a file's mode in the file itself, and a source whose modes were
            # changed behind its back must not be copied as it stands.
            # In the build's ID mapping: a tree can hold files its owner may not read (mode 0000).
            self._ostree("pull-local", "--untrusted", f"--depth={ROLLBACK_DEPTH}",
                         *([] if self.fsync else ["--disable-fsync"]), str(source), *wanted, timeout=7200, userns=True)
        return wanted

    def export(self, ref: str, destination: Path) -> None:
        """A private, writable copy of a tree, to build on."""
        self._ostree("checkout", "--user-mode", "--force-copy", ref, str(destination), userns=True)

    def compose(self, commits: list[str], salt: str = "", finish: Callable[[Path], None] | None = None,
                unions: list[tuple[str, str]] | None = None) -> Path:
        """A read-only checkout of ``commits[0]`` with the later ones laid over it.

        Every file is a hard link into the repository, so a composed checkout
        costs directory entries only. ``unions`` are (commit, subpath) pairs
        laid over the tree after the whole commits. ``finish`` may add or
        replace entries (never write through one) before the checkout is
        published under its final name.
        """
        key = hashlib.sha256("\n".join([*commits, *[f"{c}:{p}" for c, p in unions or []], salt]).encode()).hexdigest()[:40]
        final = self.checkouts / key
        if final.is_dir():
            return final
        with self._locked():
            if final.is_dir():
                return final
            partial = self.checkouts / f".{key}.partial"
            remove_checkout(partial)
            self._ostree("checkout", "--user-mode", "--require-hardlinks", "--fsync=false", commits[0], str(partial))
            for commit in commits[1:]:
                self._ostree("checkout", "--user-mode", "--require-hardlinks", "--fsync=false", "--union", commit, str(partial))
            for commit, subpath in unions or []:
                self._ostree("checkout", "--user-mode", "--require-hardlinks", "--fsync=false", "--union", f"--subpath={subpath}",
                             commit, str(partial / subpath.lstrip("/")))
            owner_writable(partial)
            if finish is not None:
                finish(partial)
            (self.checkouts / f"{key}.json").write_text(json.dumps({"commits": commits, "unions": unions or [], "salt": salt}) + "\n")
            partial.rename(final)
        return final

    # -- lifecycle ---------------------------------------------------------------------

    def keep_package(self, path: Path, sha256: str) -> Path:
        """Keep the file an application arrived as: it is rebuilt from it."""
        self.ensure()
        kept = self.packages / f"{sha256}{path.suffix.lower()}"
        if not kept.exists():
            try:
                # Valet's staged copy and this one are the same bytes on the same disk: one file, two names.
                os.link(path, kept)
            except OSError:
                shutil.copyfile(path, kept)
        return kept

    def kept_package(self, record: dict[str, object]) -> str:
        origin = dict(record.get("origin") or {})  # type: ignore[arg-type]
        for kept in self.packages.glob(f"{origin.get('sha256') or 'none'}.*"):
            return str(kept)
        return ""

    def rollback(self, ref: str) -> str:
        """Move the ref to the commit kept behind it."""
        current = self.resolve(ref)
        previous = self.parent(current) if current else ""
        if not previous or self._ostree("show", previous, check=False).returncode != 0:
            raise InstallerError("There is no earlier version of this application to return to.")
        with self._locked():
            self._ostree("reset", ref, previous)
        return previous

    def remove(self, app_id: str) -> None:
        refs = self.refs(f"app/{app_id}/")
        if not refs:
            raise InstallerError(f"{app_id} is not in the application store.")
        records = []
        for ref in refs:
            try:
                records.append(self.read_record(ref))
            except (InstallerError, ValueError):
                pass
        with self._locked():
            for ref in refs:
                self._ostree("refs", "--delete", ref)
            for ref in self.refs(f"pending/{app_id}/"):
                self._ostree("refs", "--delete", ref)
        for record in records:
            kept = self.kept_package(record)
            if kept and not self._package_in_use(Path(kept).stem):
                Path(kept).unlink(missing_ok=True)
        self.collect()

    def _package_in_use(self, sha256: str) -> bool:
        for ref in self.refs("app/"):
            try:
                if dict(self.read_record(ref).get("origin") or {}).get("sha256") == sha256:  # type: ignore[arg-type]
                    return True
            except (InstallerError, ValueError):
                continue
        return False

    def collect(self) -> dict[str, int]:
        """Drop checkouts no ref needs and prune objects no ref reaches."""
        if not (self.repo / "config").is_file():
            return {"checkouts": 0, "freed_bytes": 0}
        wanted: set[str] = set()
        for ref in self.refs():
            commit = self.resolve(ref)
            for _depth in range(ROLLBACK_DEPTH + 1):
                if not commit:
                    break
                wanted.add(commit)
                commit = self.parent(commit)
        removed = 0
        with self._locked():
            for sidecar in sorted(self.checkouts.glob("*.json")):
                try:
                    described = json.loads(sidecar.read_text())
                    commits = list(described["commits"]) + [c for c, _p in described.get("unions") or []]
                except (OSError, ValueError, KeyError):
                    commits = []
                if commits and all(commit in wanted for commit in commits):
                    continue
                remove_checkout(self.checkouts / sidecar.stem)
                sidecar.unlink(missing_ok=True)
                removed += 1
            for stale in self.checkouts.glob(".*.partial"):
                remove_checkout(stale)
            before = self.disk_bytes()
            self._ostree("prune", "--refs-only", f"--depth={ROLLBACK_DEPTH}")
            freed = before - self.disk_bytes()
            self._drop_empty_ref_folders()
        if not self.refs("app/") and not self.refs("pending/"):
            # No application left: the userlands and graphics layers that served them go
            # too, and the store itself, so removing the last one leaves no file.
            remove_checkout(self.root)
        return {"checkouts": removed, "freed_bytes": max(freed, 0)}

    def _drop_empty_ref_folders(self) -> None:
        """A deleted ref leaves the folders it was named in (refs/heads/app/ID); an application that is gone leaves none."""
        heads = self.repo / "refs/heads"
        for folder, _folders, _files in os.walk(heads, topdown=False):
            if Path(folder) != heads:
                try:
                    os.rmdir(folder)
                except OSError:
                    pass  # not empty: still names a ref

    def verify(self) -> bool:
        """Whether every object still matches its checksum (content and mode)."""
        return self._ostree("fsck", "--quiet", check=False, timeout=7200).returncode == 0

    # -- measurement -------------------------------------------------------------------

    def disk_bytes(self) -> int:
        """Bytes the repository's objects occupy, each file once."""
        total, seen = 0, set()
        for folder, _dirs, files in os.walk(self.repo / "objects"):
            for name in files:
                try:
                    status = os.lstat(os.path.join(folder, name))
                except OSError:
                    continue
                if status.st_ino not in seen:
                    seen.add(status.st_ino)
                    total += status.st_blocks * 512
        return total

    def listing(self, ref: str) -> dict[str, int]:
        """Content checksum -> size, for every regular file in a tree."""
        sizes: dict[str, int] = {}
        for line in self._ostree("ls", "-R", "-C", ref, timeout=1800).stdout.splitlines():
            parts = line.split(None, 5)
            if len(parts) >= 6 and parts[0].startswith("-") and parts[3].isdigit():
                sizes[parts[4]] = int(parts[3])
        return sizes

    def sharing(self, prefix: str = "app/") -> dict[str, object]:
        """How much of each tree is also in another one, from the repository itself."""
        listings = {ref: self.listing(ref) for ref in self.refs(prefix)}
        owners: dict[str, int] = {}
        for sizes in listings.values():
            for checksum in sizes:
                owners[checksum] = owners.get(checksum, 0) + 1
        union: dict[str, int] = {}
        rows = []
        for ref, sizes in listings.items():
            union.update(sizes)
            shared = sum(size for checksum, size in sizes.items() if owners[checksum] > 1)
            rows.append({"ref": ref, "files": len(sizes), "bytes": sum(sizes.values()), "shared_bytes": shared,
                         "own_bytes": sum(sizes.values()) - shared})
        return {"trees": rows, "sum_bytes": sum(row["bytes"] for row in rows), "stored_bytes": sum(union.values()),
                "files": sum(row["files"] for row in rows), "stored_files": len(union)}

    def delta(self, old: str, new: str) -> dict[str, int]:
        """What a new commit added to the store over the one before it."""
        before, after = self.listing(old), self.listing(new)
        fresh = {checksum: size for checksum, size in after.items() if checksum not in before}
        return {"new_files": len(fresh), "new_bytes": sum(fresh.values()), "kept_files": len(after) - len(fresh),
                "kept_bytes": sum(after.values()) - sum(fresh.values())}


def owner_writable(root: Path) -> None:
    """Make every directory of a checkout writable by its owner.

    Distributions ship read-only directories (Fedora's / and /usr/bin are
    0555). In a checkout they are real directories owned by the person: they
    have to accept the launcher's few additions, and be removable."""
    try:
        os.chmod(root, (os.lstat(root).st_mode & 0o7777) | 0o700)
    except OSError:
        return
    for folder, directories, _files in os.walk(root):
        for name in directories:
            path = os.path.join(folder, name)
            try:
                mode = os.lstat(path).st_mode
                if not os.path.islink(path) and mode & 0o700 != 0o700:
                    os.chmod(path, (mode & 0o7777) | 0o700)
            except OSError:
                continue


def remove_checkout(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        owner_writable(path)
    shutil.rmtree(path, ignore_errors=True)


def stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
