# SPDX-License-Identifier: Apache-2.0
"""Publish the installed Depot's catalog (catalog-4.json, catalog-3.json) from the Hub.

The Hub renders every listing into catalogue v1 (catalogue.py) and into the
schema 4 and 3 snapshots this signs. Each round runs the repository's own
signer, scripts/depot/sign-catalog.py, from the deployed checkout: it fetches
the snapshots, copies listing pictures the site lacks from the Hub, validates
both with the rules installed Depots run, signs them with the catalog key and,
only when a document changed, uploads pictures and then documents with
sync-remote.sh. A round is skipped while another Depot publish holds the
publication lock; the next one catches up.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import subprocess
import time

from .config import Config


def log(message: str) -> None:
    print(time.strftime("%H:%M:%S"), "catalog-4:", message, flush=True)


@contextlib.contextmanager
def try_locks(paths):
    handles = []
    try:
        for path in paths:
            handle = open(path, "a")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                yield False
                return
            handles.append(handle)
        yield True
    finally:
        for handle in reversed(handles):
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()


def command(config: Config) -> list[str]:
    scripts = config.source / "scripts/depot"
    keys = config.depot_keys / "minisign"
    argv = ["python3", "-I", str(scripts / "sign-catalog.py"), "--hub", config.hub,
            "--token-file", str(config.token_file), "--key", str(keys / "luma-depot-catalog.key"),
            "--public-key", str(keys / "luma-depot-catalog.pub"), "--site", str(config.depot_root / "site")]
    if config.sync_remote:
        sync = scripts / "sync-remote.sh"
        argv += ["--upload", f"bash '{sync}' --media && bash '{sync}' --catalog"]
    return argv


def publish(config: Config) -> bool | None:
    """True when a document was signed, False when unchanged, None when skipped."""
    with try_locks(config.publication_locks) as held:
        if not held:
            return None
        env = {**os.environ, "DEPOT_ROOT": str(config.depot_root), "DEPOT_KEYS": str(config.depot_keys)}
        result = subprocess.run(command(config), env=env, capture_output=True, text=True, timeout=900,
                                stdin=subprocess.DEVNULL)
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        raise RuntimeError(f"signer exit {result.returncode}: {output[-2000:]}")
    signed = "signed catalog-" in output
    if signed or "media: copied" in output:
        for line in output.splitlines():
            if line.startswith(("signed ", "media: ", "icons: ", "install paths: ")):
                log(line)
    return signed
