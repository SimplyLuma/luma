# SPDX-License-Identifier: Apache-2.0
"""The models Ari can run here: the catalogue, what's installed, and downloads.

A model is downloaded only after the person agrees, from a pinned revision, and
is kept only if its SHA-256 matches the catalogue. A partial file is resumed,
never trusted.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import paths

CHUNK = 4 * 1024 * 1024


@dataclass(frozen=True)
class CatalogModel:
    id: str
    family: str
    name: str
    parameters: str
    quantisation: str
    licence: str
    source: str
    url: str
    revision: str
    file: str
    size: int
    sha256: str
    context: int
    knowledge_cutoff: str
    profile: str
    tiers: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        return f"{self.name} · {self.quantisation}"

    def path(self, root: Path | None = None) -> Path:
        return (root or paths.models_dir()) / self.file


def remove(model: CatalogModel, root: Path | None = None) -> None:
    for path in (model.path(root), model.path(root).with_suffix(".verified"),
                 model.path(root).with_suffix(model.path(root).suffix + ".part")):
        path.unlink(missing_ok=True)


def suite_results(directory: Path | None = None) -> dict[str, dict]:
    """The newest Phase 1 suite result for each model, from the results shipped with Ari."""
    found: dict[str, dict] = {}
    if directory is None:
        installed = paths.share_dir() / "eval"
        directory = installed if installed.is_dir() else Path(__file__).resolve().parents[1] / "eval/results"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        found[value.get("model", "")] = {"passed": value.get("passed", 0), "total": value.get("total", 0),
                                          "date": value.get("date", "")}
    return found


class DownloadError(RuntimeError):
    pass


def catalogue(path: Path | None = None) -> list[CatalogModel]:
    value = json.loads((path or paths.share_dir() / "models.json").read_text())
    if value.get("schema_version") != 1:
        raise ValueError("Unsupported model catalogue")
    models = []
    for row in value["models"]:
        if not row["url"].startswith("https://huggingface.co/") or len(row["sha256"]) != 64:
            raise ValueError(f"Model {row.get('id')} has no pinned source")
        models.append(CatalogModel(**{**row, "tiers": tuple(row.get("tiers", ()))}))
    return models


def find(model_id: str, models: list[CatalogModel] | None = None) -> CatalogModel | None:
    return next((m for m in (models or catalogue()) if m.id == model_id), None)


def installed(models: list[CatalogModel] | None = None, root: Path | None = None) -> list[CatalogModel]:
    """Models whose file is present and was verified when it finished downloading."""
    result = []
    for model in models or catalogue():
        verified = model.path(root).with_suffix(".verified")
        if model.path(root).is_file() and verified.is_file() and verified.read_text().strip() == model.sha256:
            result.append(model)
    return result


def default_for_tier(tier: str, models: list[CatalogModel] | None = None) -> CatalogModel:
    models = models or catalogue()
    return next((m for m in models if tier in m.tiers), models[0])


def download(model: CatalogModel, *, progress: Callable[[int, int], None] | None = None,
             cancelled: threading.Event | None = None, root: Path | None = None) -> Path:
    target = model.path(root)
    partial = target.with_suffix(target.suffix + ".part")
    have = partial.stat().st_size if partial.exists() else 0
    if have > model.size:
        partial.unlink()
        have = 0
    request = urllib.request.Request(model.url, headers={"User-Agent": "Luma-Ari/0.1"})
    if have:
        request.add_header("Range", f"bytes={have}-")
    try:
        response = urllib.request.urlopen(request, timeout=30)
    except OSError as error:
        raise DownloadError("The download couldn't start. Check the connection and try again.") from error
    if have and response.status != 206:
        have = 0  # The server ignored the range; start again.
    digest = hashlib.sha256()
    if have:
        with open(partial, "rb") as existing:
            while chunk := existing.read(CHUNK):
                digest.update(chunk)
    with response, open(partial, "ab" if have else "wb") as out:
        done = have
        while True:
            if cancelled is not None and cancelled.is_set():
                raise DownloadError("Download stopped.")
            try:
                chunk = response.read(CHUNK)
            except OSError as error:
                raise DownloadError("The download was interrupted. It will continue from here next time.") from error
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, model.size)
    if done != model.size or digest.hexdigest() != model.sha256:
        partial.unlink(missing_ok=True)
        raise DownloadError("The downloaded model didn't match its published checksum, so it was deleted.")
    os.replace(partial, target)
    target.with_suffix(".verified").write_text(model.sha256)
    return target


def remove(model: CatalogModel, root: Path | None = None) -> None:
    for suffix in ("", ".verified"):
        (model.path(root) if not suffix else model.path(root).with_suffix(suffix)).unlink(missing_ok=True)
    model.path(root).with_suffix(model.path(root).suffix + ".part").unlink(missing_ok=True)
