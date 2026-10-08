# SPDX-License-Identifier: MPL-2.0
"""A throwaway session layout: data dirs, desktop entries, declarations, Flatpaks."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import textwrap
from pathlib import Path

from luma_background.registry import Layout


class Tree:
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="luma-background-test-"))
        self.system = self.root / "usr/share"
        self.home = self.root / "home"
        self.bin = self.root / "usr/bin"
        # $XDG_CONFIG_DIRS: never the machine's own /etc/xdg.
        self.xdg = self.root / "etc/xdg"
        for directory in (self.system / "applications", self.system / "luma/background",
                          self.home / ".local/share/applications", self.home / ".config/autostart",
                          self.bin, self.root / "var/lib/flatpak/app", self.xdg / "autostart"):
            directory.mkdir(parents=True, exist_ok=True)
        self.system_paths: set[Path] = set()

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    @property
    def layout(self) -> Layout:
        return Layout(
            data_dirs=(self.system,),
            data_home=self.home / ".local/share",
            config_home=self.home / ".config",
            home=self.home,
            flatpak_system=self.root / "var/lib/flatpak",
            config_dirs=(self.xdg,),
        )

    def autostart(self, name: str, lines: list[str], *, system: bool = True) -> Path:
        """An autostart entry, in the system's folder or the person's."""
        directory = (self.xdg if system else self.home / ".config") / "autostart"
        path = directory / f"{name}.desktop"
        path.write_text("\n".join(["[Desktop Entry]", "Type=Application", *lines]) + "\n")
        return path

    def is_system(self, path: Path) -> bool:
        resolved = Path(path)
        return any(resolved == item or item in resolved.parents for item in self.system_paths)

    def program(self, name: str) -> Path:
        path = self.bin / name
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
        return path

    def desktop(self, app_id: str, exec_line: str, *, user: bool = False, name: str = "",
                flatpak: bool = False, system: bool = True) -> Path:
        directory = (self.home / ".local/share/applications") if user else (self.system / "applications")
        if flatpak:
            directory = self.root / "var/lib/flatpak/exports/share/applications"
            directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{app_id}.desktop"
        lines = ["[Desktop Entry]", "Type=Application", f"Name={name or app_id.rsplit('.', 1)[-1]}",
                 f"Icon={app_id}", f"Exec={exec_line}"]
        if flatpak:
            lines.append(f"X-Flatpak={app_id}")
        path.write_text("\n".join(lines) + "\n")
        if system and not user:
            self.system_paths.add(path)
        return path

    def declaration(self, app_id: str, body: str, *, user: bool = False, flatpak: bool = False,
                    system: bool = True) -> Path:
        if flatpak:
            directory = self.root / "var/lib/flatpak/app" / app_id / "current/active/files/share/luma/background"
        elif user:
            directory = self.home / ".local/share/luma/background"
        else:
            directory = self.system / "luma/background"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{app_id}.toml"
        path.write_text(f'[application]\nid = "{app_id}"\nname = "{app_id.rsplit(".", 1)[-1]}"\n\n'
                        + textwrap.dedent(body))
        if system and not user:
            self.system_paths.add(path)
        return path

    def flatpak_layout(self) -> Layout:
        layout = self.layout
        return Layout(
            data_dirs=(self.system, self.root / "var/lib/flatpak/exports/share"),
            data_home=layout.data_home, config_home=layout.config_home, home=layout.home,
            flatpak_system=layout.flatpak_system, config_dirs=layout.config_dirs,
        )
