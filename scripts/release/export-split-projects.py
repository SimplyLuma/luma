#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

"""Create reproducible independently consumable product snapshots."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config/release/split-projects.json"


def committed_projects(source: str) -> tuple[list[dict], str]:
    """The path selection is source, too; do not take it from a dirty checkout."""
    content = subprocess.check_output(
        ("git", "show", f"{source}:config/release/split-projects.json"), cwd=ROOT)
    manifest = json.loads(content)
    if manifest.get("schema") != 1 or not isinstance(manifest.get("projects"), list):
        raise SystemExit("unsupported split-project manifest")
    names, branches = set(), set()
    for project in manifest["projects"]:
        name, branch = project.get("name", ""), project.get("branch", "")
        paths = project.get("paths")
        source_root = project.get("source_root", "")
        if not re.fullmatch(r"[a-z][a-z0-9-]*", name) or name in names:
            raise SystemExit("invalid or duplicate split project name")
        if not branch.startswith("products/") or branch in branches:
            raise SystemExit("invalid or duplicate product branch")
        git("check-ref-format", "refs/heads/" + branch)
        git("check-ref-format", "refs/heads/" + project.get("publish_branch", branch))
        if not re.fullmatch(r"LUMA_[A-Z0-9_]+_REMOTE", project.get("remote_env", "")):
            raise SystemExit("invalid product remote environment variable")
        if not isinstance(paths, list) or not paths or len(paths) != len(set(paths)):
            raise SystemExit("empty or duplicate source paths")
        if source_root and source_root not in paths:
            raise SystemExit("product source root must be an exact selected path")
        for path in paths:
            if (not isinstance(path, str) or not path or path.startswith(("/", "-"))
                    or any(part in ("", ".", "..") for part in path.split("/"))
                    or any(char in path for char in "*?[\\\n\r\x00")):
                raise SystemExit("invalid split source path")
            git("cat-file", "-e", f"{source}:{path}")
        if any(a != b and b.startswith(a + "/") for a in paths for b in paths):
            raise SystemExit("overlapping split source paths")
        if source_root and git("cat-file", "-t", f"{source}:{source_root}") != "tree":
            raise SystemExit("product source root must be a directory")
        names.add(name); branches.add(branch)
    if not names:
        raise SystemExit("no split projects")
    return manifest["projects"], hashlib.sha256(content).hexdigest()


def git(*arguments: str, cwd: Path | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ("git", *arguments), cwd=cwd or ROOT, check=check, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="HEAD", help="immutable source commit/ref")
    parser.add_argument("--project", action="append", default=[])
    parser.add_argument("--publish", action="store_true", help="push configured split branches")
    parser.add_argument("--output", type=Path, default=ROOT / "build/split-releases")
    args = parser.parse_args()
    source = git("rev-parse", f"{args.source}^{{commit}}")
    if args.source == "HEAD" and git("status", "--porcelain"):
        raise SystemExit("refusing to export an uncommitted workspace; commit canonical source first")
    committed, manifest_digest = committed_projects(source)
    selected = set(args.project)
    projects = [p for p in committed if not selected or p["name"] in selected]
    unknown = selected - {p["name"] for p in projects}
    if unknown:
        raise SystemExit("unknown project: " + ", ".join(sorted(unknown)))
    for project in projects:
        export(project, source, args.output, args.publish, manifest_digest)
    return 0


def export(project: dict[str, object], source: str, output: Path, publish: bool,
           manifest_digest: str) -> None:
    name, branch = str(project["name"]), str(project["branch"])
    paths = [str(path) for path in project["paths"]]
    with tempfile.TemporaryDirectory(prefix=f"luma-split-{name}-") as temporary:
        temporary_path = Path(temporary)
        archive = temporary_path / "source.tar"
        with archive.open("wb") as stream:
            subprocess.run(("git", "--literal-pathspecs", "archive", "--format=tar",
                            source, "--", *paths), cwd=ROOT, check=True, stdout=stream)
        canonical = temporary_path / "canonical"; canonical.mkdir()
        with tarfile.open(archive) as package:
            package.extractall(canonical, filter="data")
        tree = temporary_path / "tree"
        source_root = str(project.get("source_root", ""))
        if source_root:
            # A dedicated product keeps its real Meson/application root. Host
            # packaging and provenance inputs live in a separate namespace,
            # preserving the product's own packaging, scripts and licenses.
            shutil.copytree(canonical / source_root, tree, symlinks=True)
            if (tree / ".luma-integration").exists() or (tree / ".luma-integration").is_symlink():
                raise SystemExit("source already owns reserved .luma-integration namespace")
            for path in paths:
                if path == source_root:
                    continue
                destination_path = tree / ".luma-integration" / path
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                source_path = canonical / path
                if source_path.is_dir() and not source_path.is_symlink():
                    shutil.copytree(source_path, destination_path, symlinks=True)
                else:
                    shutil.copy2(source_path, destination_path, follow_symlinks=False)
        else:
            canonical.rename(tree)
        for path in tree.rglob("*"):
            if not path.is_symlink():
                continue
            try:
                target = path.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise SystemExit(f"invalid exported symlink: {path.relative_to(tree)}") from error
            if not target.is_relative_to(tree.resolve()):
                raise SystemExit(f"exported symlink escapes product: {path.relative_to(tree)}")
        if (tree / "LUMA-SOURCE.json").exists() or (tree / "LUMA-SOURCE.json").is_symlink():
            raise SystemExit("source already owns reserved LUMA-SOURCE.json receipt")
        source_epoch = int(git("show", "-s", "--format=%ct", source))
        generated_at = datetime.fromtimestamp(source_epoch, timezone.utc).isoformat(timespec="seconds")
        receipt = {
            "schema": 1,
            "project": name,
            "canonical_repository": git("remote", "get-url", "origin"),
            "canonical_commit": source,
            "selection_manifest_sha256": manifest_digest,
            "generated_at": generated_at,
            "paths": paths,
            "source_root": source_root or None,
        }
        (tree / "LUMA-SOURCE.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        destination = output / name; destination.mkdir(parents=True, exist_ok=True)
        tar_path = destination / f"{name}-{source[:12]}.tar.gz"
        raw_tar = temporary_path / "product.tar"
        with tarfile.open(raw_tar, "w", format=tarfile.PAX_FORMAT) as package:
            for path in sorted(tree.rglob("*")):
                info = package.gettarinfo(path, arcname=path.relative_to(tree))
                info.uid = info.gid = 0; info.uname = info.gname = "root"; info.mtime = source_epoch
                with path.open("rb") if path.is_file() else _null_context() as stream:
                    package.addfile(info, stream if path.is_file() else None)
        with raw_tar.open("rb") as source_stream, tar_path.open("wb") as destination_stream:
            with gzip.GzipFile(filename="", mode="wb", fileobj=destination_stream, mtime=source_epoch) as compressed:
                shutil.copyfileobj(source_stream, compressed)
        digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
        tar_path.with_suffix(tar_path.suffix + ".sha256").write_text(
            f"{digest}  {tar_path.name}\n", encoding="utf-8"
        )
        print(f"{name}: {tar_path} sha256={digest}")
        if publish:
            publish_tree(project, tree, source, str(project.get("publish_branch", branch)))


def publish_tree(project: dict[str, object], tree: Path, source: str, branch: str) -> None:
    remote = os.environ.get(str(project["remote_env"]))
    if not remote:
        raise SystemExit(f"publishing requires explicit {project['remote_env']}; no implicit origin push")
    repository = tree.parent / "repository"; repository.mkdir()
    git("init", "-q", cwd=repository)
    git("config", "user.name", "Project Luma Release Automation", cwd=repository)
    git("config", "user.email", "maintainers@projectluma.org", cwd=repository)
    git("remote", "add", "publish", remote, cwd=repository)
    lookup = subprocess.run(("git", "ls-remote", "--exit-code", "--heads", "publish",
                             "refs/heads/" + branch), cwd=repository,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if lookup.returncode not in (0, 2):
        raise SystemExit("cannot inspect publication remote; refusing to treat an access failure as a new branch")
    if lookup.returncode == 0:
        git("fetch", "-q", "publish", "refs/heads/" + branch, cwd=repository)
        git("checkout", "-q", "-B", branch, "FETCH_HEAD", cwd=repository)
        for child in repository.iterdir():
            if child.name != ".git":
                if child.is_symlink() or not child.is_dir():
                    child.unlink()
                else:
                    shutil.rmtree(child)
    else:
        git("checkout", "-q", "--orphan", branch, cwd=repository)
    shutil.copytree(tree, repository, dirs_exist_ok=True, symlinks=True)
    git("add", "-A", cwd=repository)
    changed = subprocess.run(("git", "diff", "--cached", "--quiet"), cwd=repository).returncode != 0
    if not changed:
        print(f"{project['name']}: split branch already matches {source}")
        return
    git("commit", "-q", "-m", f"Release {project['name']} from Project Luma {source}", cwd=repository)
    git("push", "publish", f"HEAD:refs/heads/{branch}", cwd=repository)


class _null_context:
    def __enter__(self):
        return None

    def __exit__(self, *_arguments) -> None:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
