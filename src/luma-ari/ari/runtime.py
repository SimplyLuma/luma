# SPDX-License-Identifier: Apache-2.0
"""llama-server as Ari's child process: one model at a time, local only."""
from __future__ import annotations

import json
import os
import shutil
import secrets
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from . import paths
from .hardware import Hardware
from .models import CatalogModel


class RuntimeError_(RuntimeError):
    pass


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class LocalRuntime:
    def __init__(self, hardware: Hardware) -> None:
        self.hardware = hardware
        self.process: subprocess.Popen | None = None
        self.model: CatalogModel | None = None
        self.port = 0
        self.key = ""
        self.tokens_per_second = 0.0
        self.on_gpu = False
        self._lock = threading.Lock()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def running(self, model: CatalogModel | None = None) -> bool:
        alive = self.process is not None and self.process.poll() is None
        return alive and (model is None or self.model == model)

    def ensure(self, model: CatalogModel, *, timeout: float = 180) -> None:
        with self._lock:
            if self.running(model):
                return
            self._stop()
            binary = paths.runtime_binary()
            if binary is None:
                raise RuntimeError_("Ari's model runtime isn't installed on this machine.")
            # Integrated GPUs share memory with everything else on the machine,
            # so a model that fits one day may not the next. Fall back to the
            # CPU rather than leave the person without an answer.
            attempts = [True, False] if self.hardware.backend == "vulkan" else [False]
            for gpu in attempts:
                failure = self._start(binary, model, gpu=gpu, timeout=timeout)
                if failure is None:
                    return
                self._stop()
            raise RuntimeError_(failure)

    def _start(self, binary, model: CatalogModel, *, gpu: bool, timeout: float) -> str | None:
        self.port = _free_port()
        self.key = secrets.token_urlsafe(24)
        # Working memory for the context grows with its length; Ari's turns are
        # short, and 16k cost Qwen3 4B about 2.4 GB of a 16 GB laptop.
        context = min(model.context, 8192 if self.hardware.tier in ("minimal", "standard") else 16384)
        command = [str(binary), "--model", str(model.path()), "--host", "127.0.0.1",
                   "--port", str(self.port), "--api-key", self.key, "--ctx-size", str(context),
                   "--jinja", "--no-webui", "--threads", str(max(2, self.hardware.cores // 2)),
                   "--parallel", "1"]
        command += ["--n-gpu-layers", "999"] if gpu else ["--n-gpu-layers", "0", "--device", "none"]
        log = (paths.data_dir() / "runtime.log").open("ab")
        log.write(f"\n# starting {model.id} on the {'GPU' if gpu else 'CPU'}\n".encode())
        log.flush()
        # Under systemd the model runs with a low CPU and I/O weight, so answering
        # never takes the desktop's share when both want it.
        if shutil.which("systemd-run") and os.environ.get("XDG_RUNTIME_DIR"):
            command = ["systemd-run", "--user", "--scope", "--quiet", "--collect",
                       f"--unit=luma-ari-model-{os.getpid()}-{self.port}",
                       "-p", "CPUWeight=30", "-p", "IOWeight=30", "--", *command]
        self.process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True)
        log.close()
        self.model = model
        self.on_gpu = gpu
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                return "The model runtime stopped while loading. See runtime.log in Ari's data folder."
            try:
                request = urllib.request.Request(f"http://127.0.0.1:{self.port}/health",
                                                 headers={"Authorization": f"Bearer {self.key}"})
                with urllib.request.urlopen(request, timeout=2) as response:
                    if json.loads(response.read() or b"{}").get("status") == "ok":
                        return None
            except OSError:
                pass
            time.sleep(0.5)
        return "The model took too long to load."

    def stop(self) -> None:
        with self._lock:
            self._stop()

    def _stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        self.model = None
