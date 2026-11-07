# SPDX-License-Identifier: Apache-2.0
"""Publish an approved submission, or withdraw a release, through the sealed chain.

Nothing here signs anything itself. It prepares an unsigned app repository
from the reviewed bundle, seals it as the signer's artifact snapshot, and runs
the existing publisher scripts from a sealed control snapshot in a networkless
signer container:

  bundle → import (review container, no network) → seal-signing-control.py
  seal-inputs → tools-container.sh --signing → publish-app.sh --third-party →
  update-remote.sh → sync-remote.sh

Withdrawal is the same chain with withdraw-app.sh in place of publish-app.sh.
Both hold the Depot publication locks, so they never interleave with a
nightly's Depot publish.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .config import Config


class PublishError(RuntimeError):
    pass


def log(message: str) -> None:
    print(time.strftime("%H:%M:%S"), "publish:", message, flush=True)


@contextlib.contextmanager
def publication_locks(config: Config):
    handles = []
    try:
        for path in config.publication_locks:
            handle = open(path, "a")
            log(f"waiting for {path}")
            fcntl.flock(handle, fcntl.LOCK_EX)
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()


def run(command: list[str], env: dict | None = None, timeout: int = 3600, cwd: str | None = None) -> str:
    log("$ " + " ".join(command[:6]) + (" …" if len(command) > 6 else ""))
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                            stdin=subprocess.DEVNULL)
    output = (result.stdout + result.stderr)[-6000:]
    if result.returncode != 0:
        raise PublishError(f"{Path(command[0]).name} {' '.join(command[1:3])} failed ({result.returncode}):\n{output}")
    return result.stdout


def protected_dir(path: Path) -> Path:
    path.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chmod(path, 0o755)
    return path


def control_snapshot(config: Config) -> tuple[Path, str]:
    """Seal (once per source revision) the publisher control from the deployed checkout."""
    revision = run(["git", "-C", str(config.source), "rev-parse", "HEAD"]).strip()
    root = protected_dir(config.publish_root)
    target = root / f"control-{revision[:12]}"
    digest_file = root / f"control-{revision[:12]}.sha256"
    if not target.exists():
        digest = run(["env", "-u", "PYTHONPATH", "python3", "-B", "-I",
                      str(config.source / "scripts/depot/seal-signing-control.py"), "seal",
                      str(config.source), str(target)]).strip()
        digest_file.write_text(digest + "\n")
    digest = digest_file.read_text().strip()
    run(["env", "-u", "PYTHONPATH", "python3", "-B", "-I", str(target / "scripts/depot/seal-signing-control.py"),
         "verify", str(target), digest])
    return target, digest


def review_container(config: Config, mounts: list[tuple[Path, str, str]], command: list[str],
                     timeout: int = 1800) -> str:
    args = ["podman", "run", "--rm", "--network", "none", "--cap-drop", "ALL", "--security-opt",
            "label=disable", "--security-opt", "no-new-privileges", "--pids-limit", "1024", "--env", "HOME=/tmp",
            "--memory", "4g", "--cpus", "2", "--read-only", "--tmpfs", "/tmp:size=1g", "--tmpfs", "/var/tmp:size=1g"]
    for host, guest, mode in mounts:
        args += ["--volume", f"{host}:{guest}:{mode}"]
    env = dict(os.environ, CONTAINERS_STORAGE_CONF=str(config.storage_conf))
    return run(args + [config.review_image] + command, env=env, timeout=timeout)


def import_bundle(config: Config, bundle: Path, app_id: str, arch: str, build_root: Path,
                  submission_id: str, sha256: str) -> Path:
    """Unsigned repository holding exactly app/ID/ARCH/stable from the reviewed bundle."""
    out = build_root / "apps" / app_id
    out.mkdir(parents=True)
    script = f"""set -euo pipefail
ostree init --mode=archive-z2 --repo=/work/tmp
flatpak build-import-bundle --no-update-summary /work/tmp /in/bundle.flatpak >/dev/null
src=$(ostree refs --repo=/work/tmp | grep '^app/{app_id}/{arch}/' )
test "$(printf '%s\\n' "$src" | wc -l)" = 1
ostree init --mode=archive-z2 --repo=/work/out/repo
flatpak build-commit-from --no-update-summary --src-repo=/work/tmp --src-ref="$src" /work/out/repo app/{app_id}/{arch}/{config.branch}
commit=$(ostree rev-parse --repo=/work/out/repo app/{app_id}/{arch}/{config.branch})
ostree cat --repo=/work/out/repo "$commit" /metadata > /work/out/metadata
rm -rf /work/tmp
echo "$commit"
"""
    work = build_root / "work"
    work.mkdir()
    shutil.move(str(out), str(work / "out"))
    commit = review_container(config, [(bundle, "/in/bundle.flatpak", "ro"), (work, "/work", "rw")],
                              ["bash", "-c", script]).strip().splitlines()[-1]
    shutil.move(str(work / "out"), str(out))
    shutil.rmtree(work)
    (out / "SOURCES").write_text(f"depot-submission {submission_id} sha256:{sha256}\n")
    return out


class Signer:
    """A sealed, networkless signer for one artifact snapshot."""

    def __init__(self, config: Config, control: Path, control_sha: str, build_root: Path, name: str, arch: str):
        self.config, self.control, self.control_sha = config, control, control_sha
        self.inputs = protected_dir(config.publish_root / "inputs") / name
        if self.inputs.exists():
            raise PublishError(f"inputs {self.inputs} already exist")
        self.inputs_sha = run(["env", "-u", "PYTHONPATH", "python3", "-B", "-I",
                               str(control / "scripts/depot/seal-signing-control.py"), "seal-inputs",
                               str(build_root), str(self.inputs)]).strip()
        self.container = f"depot-review-signer-{name}"
        self.env = {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LANG": "C.UTF-8",
            "CONTAINERS_STORAGE_CONF": str(config.storage_conf),
            "DEPOT_SIGNING_CONTROL": str(control), "DEPOT_SIGNING_CONTROL_SHA256": control_sha,
            "DEPOT_ROOT": str(config.depot_root), "DEPOT_KEYS": str(config.depot_keys),
            "DEPOT_ARCH": arch, "DEPOT_TOOLS_ARCH": "x86_64",
            "DEPOT_TOOLS_IMAGE": config.signer_image,
            "DEPOT_TOOLS_CONTAINER": self.container, "DEPOT_SIGNING_CONTAINER": self.container,
            "DEPOT_BUILD_ROOT": str(build_root),
            "DEPOT_SIGNING_INPUTS": str(self.inputs), "DEPOT_SIGNING_INPUTS_SHA256": self.inputs_sha,
            "DEPOT_TOOLS_CPUS": "2", "DEPOT_TOOLS_MEMORY": "4g", "DEPOT_TOOLS_MEMORY_SWAP": "5g",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def __enter__(self):
        run(["podman", "rm", "-f", self.container], env=self.env)
        run(["nice", "-n", "10", str(self.control / "scripts/depot/tools-container.sh"), "--signing"], env=self.env)
        return self

    def script(self, name: str, *args: str, timeout: int = 1800, ok_codes=(0,)) -> str:
        command = ["nice", "-n", "10", "ionice", "-c2", "-n6", "/bin/bash",
                   str(self.control / "scripts/depot" / name), *args]
        log("$ " + name + " " + " ".join(args))
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=timeout,
                                stdin=subprocess.DEVNULL)
        if result.returncode not in ok_codes:
            raise PublishError(f"{name} failed ({result.returncode}):\n{(result.stdout + result.stderr)[-6000:]}")
        return result.stdout

    def __exit__(self, *exc):
        subprocess.run(["podman", "rm", "-f", self.container], env=self.env, capture_output=True)
        subprocess.run(["chmod", "-R", "u+w", str(self.inputs)], capture_output=True)
        shutil.rmtree(self.inputs, ignore_errors=True)


def sync(config: Config) -> None:
    if not config.sync_remote:
        log("sync skipped (sync_remote is false)")
        return
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "DEPOT_ROOT": str(config.depot_root),
           "DEPOT_KEYS": str(config.depot_keys), "DEPOT_TOOLS_CONTAINER": "unused-rsync-path"}
    run(["/bin/bash", str(config.source / "scripts/depot/sync-remote.sh")], env=env, timeout=3600)


def check_depot_root(config: Config) -> None:
    root = config.depot_root
    if not (root / "repo/config").is_file():
        raise PublishError(f"the Depot root {root} is not there (the bind mount is lost after a reboot)")


def published_head(config: Config, ref: str) -> str | None:
    result = subprocess.run(["ostree", "rev-parse", f"--repo={config.depot_root / 'repo'}", ref],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def publish(config: Config, submission_id: str, app_id: str, bundles: list[dict]) -> dict:
    """bundles: [{arch, path, sha256}] (reviewed Flatpak bundles). Returns per-arch refs and commits."""
    check_depot_root(config)
    control, control_sha = control_snapshot(config)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    results = {}
    with publication_locks(config):
        for index, bundle in enumerate(bundles):
            arch = bundle["arch"]
            name = f"{submission_id}-{arch}-{stamp}".replace("/", "_")[:80]
            build_root = config.work / "publish" / name
            if build_root.exists():
                shutil.rmtree(build_root)
            build_root.mkdir(parents=True)
            try:
                import_bundle(config, Path(bundle["path"]), app_id, arch, build_root, submission_id,
                              bundle["sha256"])
                with Signer(config, control, control_sha, build_root, name, arch) as signer:
                    output = signer.script("publish-app.sh", app_id, "--third-party", "--approve-permission-changes")
                    last = [line for line in output.splitlines() if line.startswith("{")][-1]
                    published = json.loads(last)
                    if index == len(bundles) - 1:
                        signer.script("update-remote.sh", timeout=3600)
                results[arch] = {"ref": published["published"], "commit": published["commit"]}
            finally:
                shutil.rmtree(build_root, ignore_errors=True)
        sync(config)
    log(f"published {app_id}: {results}")
    return results


def withdraw(config: Config, request_id: str, app_id: str, arch: str, target: str | None,
             reason: str, current: str | None = None) -> dict:
    """Move app/ID/ARCH/stable back to `target` (an earlier commit), or end-of-life it."""
    check_depot_root(config)
    ref = f"app/{app_id}/{arch}/{config.branch}"
    head = published_head(config, ref)
    if head is None:
        raise PublishError(f"{ref} is not published")
    if current and current != head:
        raise PublishError(f"{ref} is at {head[:12]}, not the release {current[:12]} asked to be withdrawn")
    if target is None:
        parent = subprocess.run(["ostree", "rev-parse", f"--repo={config.depot_root / 'repo'}", head + "^"],
                                capture_output=True, text=True)
        target = parent.stdout.strip() if parent.returncode == 0 else None
    control, control_sha = control_snapshot(config)
    name = f"withdraw-{request_id}-{arch}-{time.strftime('%Y%m%dT%H%M%S')}".replace("/", "_")[:80]
    build_root = config.work / "publish" / name
    build_root.mkdir(parents=True)
    (build_root / "withdraw.json").write_text(json.dumps({
        "app_id": app_id, "arch": arch, "branch": config.branch, "commit": head,
        "target": target, "reason": reason or "Withdrawn"}) + "\n")
    try:
        with publication_locks(config):
            with Signer(config, control, control_sha, build_root, name, arch) as signer:
                output = signer.script("withdraw-app.sh")
                result = json.loads([line for line in output.splitlines() if line.startswith("{")][-1])
                signer.script("update-remote.sh", timeout=3600)
            sync(config)
    finally:
        shutil.rmtree(build_root, ignore_errors=True)
    log(f"withdrew {ref}: {result}")
    return result


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
