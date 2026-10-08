# SPDX-License-Identifier: Apache-2.0
"""Fresh read-only Waydroid Binder application inventory, with bounded IO.

WayDroidService.getAppsInfo reads PackageManager.getInstalledApplications(0).
This path intentionally never falls back to cached launcher files.
"""
from __future__ import annotations
import os
import re
import selectors
import signal
import subprocess
import time
from .errors import RuntimeUnavailableError
from .activation_budget import REGISTRY_QUERY_SECONDS, REGISTRY_CLEANUP_SECONDS

MAX_STDOUT = 4 * 1024 * 1024
MAX_STDERR = 64 * 1024
MAX_APPS = 4096
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")


def bounded_output(command: list[str], timeout: float = REGISTRY_QUERY_SECONDS) -> str:
    deadline = time.monotonic() + timeout
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            env=dict(os.environ, PYTHONUNBUFFERED="1"))
    except OSError as error:
        raise RuntimeUnavailableError("Android's live application inventory is unavailable.") from error
    try:
        with selectors.DefaultSelector() as selector:
            for name in buffers:
                stream = getattr(process, name)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeUnavailableError("Android's live application inventory timed out.")
                for key, _ in selector.select(min(remaining, .2)):
                    limit = MAX_STDOUT if key.data == "stdout" else MAX_STDERR
                    # Read at most one byte beyond the remaining budget;
                    # refuse before appending an oversized result.
                    chunk = os.read(key.fd, min(8192, limit - len(buffers[key.data]) + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif len(buffers[key.data]) + len(chunk) > limit:
                        raise RuntimeUnavailableError("Android's live application inventory exceeded its bound.")
                    else:
                        buffers[key.data].extend(chunk)
            # Observe exit without reaping. An unreaped leader reserves its
            # PID/process-group identity until owned group cleanup completes.
            while True:
                exited = os.waitid(os.P_PID, process.pid,
                    os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if exited is not None:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeUnavailableError("Android's live application inventory timed out.")
                time.sleep(.01)
            if exited.si_code != os.CLD_EXITED or exited.si_status != 0:
                raise RuntimeUnavailableError("Android's live application inventory failed.")
            try:
                return buffers["stdout"].decode("utf-8", errors="strict")
            except UnicodeDecodeError as error:
                raise RuntimeUnavailableError("Android returned an invalid application inventory.") from error
    finally:
        # Own only this fresh process group, including descendants which retain
        # a pipe after their leader exits. Never leave a timed-out query alive.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=REGISTRY_CLEANUP_SECONDS)
        except subprocess.TimeoutExpired as error:
            raise RuntimeUnavailableError("Android's live inventory query could not be reaped within its cleanup bound.") from error
        finally:
            process.stdout.close()
            process.stderr.close()


def parse_live_packages(text: str) -> set[str]:
    packages: set[str] = set()
    phase = "name"
    current = None
    for line in text.splitlines():
        if len(line) > 16384:
            raise RuntimeUnavailableError("Android returned an oversized application record.")
        if not line:
            continue
        if line.startswith("Name: "):
            if phase not in {"name", "categories"} or not line[6:].strip() or len(packages) >= MAX_APPS:
                raise RuntimeUnavailableError("Android returned an invalid application record.")
            phase = "package"
        elif line.startswith("packageName: "):
            current = line[13:]
            if phase != "package" or len(current) > 255 or not PACKAGE.fullmatch(current) or current in packages:
                raise RuntimeUnavailableError("Android returned an invalid application identity.")
            phase = "header"
        elif line == "categories:":
            if phase != "header":
                raise RuntimeUnavailableError("Android returned an invalid application record.")
            packages.add(current)
            phase = "categories"
        elif line.startswith("\t") and phase == "categories":
            continue
        else:
            raise RuntimeUnavailableError("Android returned an invalid application inventory.")
    if not packages or phase != "categories":
        raise RuntimeUnavailableError("Android did not return a complete live application inventory.")
    return packages


def live_launchable_packages(executable: str) -> set[str]:
    return parse_live_packages(bounded_output([executable, "app", "list"]))
