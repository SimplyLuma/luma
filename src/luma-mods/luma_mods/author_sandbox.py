"""Rootless, networkless, reproducible author-side payload builds."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Sequence

from .author_artifact import ArtifactIdentity, identity
from .errors import LumaModsError

IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,511}@sha256:[0-9a-f]{64}$")
Runner = Callable[[Sequence[str]], None]


def _run(command: Sequence[str]) -> None:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            timeout=7200,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise LumaModsError(f"sandbox build could not start: {error}") from error
    if result.returncode != 0:
        raise LumaModsError(f"sandbox build failed with status {result.returncode}")


def _command(source: Path, output: Path, image: str, argv: Sequence[str]) -> tuple[str, ...]:
    return (
        "podman", "run", "--rm", "--network=none", "--read-only",
        "--cap-drop=all", "--security-opt=no-new-privileges",
        "--pids-limit=512", "--memory=4g", "--cpus=4",
        "--userns=keep-id", "--workdir=/src",
        "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=1g",
        "--env=HOME=/tmp", "--env=SOURCE_DATE_EPOCH=1",
        f"--volume={source}:/src:ro,Z", f"--volume={output}:/out:rw,Z",
        image, *argv,
    )


def reproducible_build(
    source: Path,
    output: Path,
    image: str,
    argv: Sequence[str],
    *,
    runner: Runner = _run,
    require_rootless: bool = True,
) -> ArtifactIdentity:
    try:
        source_info = source.lstat()
    except OSError as error:
        raise LumaModsError(f"build source is unavailable: {error}") from error
    if not stat.S_ISDIR(source_info.st_mode) or stat.S_ISLNK(source_info.st_mode):
        raise LumaModsError("build source must be a real directory")
    if output.exists() or output.is_symlink():
        raise LumaModsError("build output already exists")
    if not IMAGE_PATTERN.fullmatch(image):
        raise LumaModsError("sandbox image must be pinned by sha256 digest")
    if not argv or len(argv) > 128 or any(
        not isinstance(token, str) or not token or "\x00" in token or len(token) > 4096
        for token in argv
    ):
        raise LumaModsError("sandbox build argv is invalid or unbounded")
    if require_rootless:
        if os.geteuid() == 0:
            raise LumaModsError("sandbox payload builds must run as an unprivileged author")
        try:
            probe = subprocess.run(
                ["podman", "info", "--format", "{{.Host.Security.Rootless}}"],
                check=False, capture_output=True, text=True, timeout=30,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C.UTF-8"},
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise LumaModsError(f"cannot verify rootless Podman: {error}") from error
        if probe.returncode != 0 or probe.stdout.strip().lower() != "true":
            raise LumaModsError("sandbox payload builds require rootless Podman")

    output.parent.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix=f".{output.name}-builds-", dir=output.parent))
    first = workspace / "first"
    second = workspace / "second"
    first.mkdir(mode=0o700)
    second.mkdir(mode=0o700)
    try:
        runner(_command(source.resolve(), first.resolve(), image, argv))
        runner(_command(source.resolve(), second.resolve(), image, argv))
        first_identity = identity(first)
        second_identity = identity(second)
        if first_identity != second_identity:
            raise LumaModsError(
                "independent sandbox builds differ; refusing to publish an output"
            )
        os.replace(first, output)
        return first_identity
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
