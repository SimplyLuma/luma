#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate every device under devices/ and every SoC family under socs/.

A device directory holds exactly: device.toml, boot.toml, firmware.manifest,
INSTALL.md and tests/. Manifests are checked against their JSON Schemas, the
firmware manifest and initramfs sources against the hashes device.toml records, and the whole tree
against the rules that keep it public: text only, no firmware blobs, no IMEI
or ICCID values, and an owner in .github/CODEOWNERS for every directory.
Each device's own tests then run.

Usage:  tools/check-devices.py [--root DIR] [--no-tests]
Exit:   0 valid, 1 invalid, 2 could not run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
import unittest
from pathlib import Path, PurePosixPath

try:
    import jsonschema
except ImportError:  # pragma: no cover - reported as exit 2
    jsonschema = None

DEVICE_FILES = {"device.toml", "boot.toml", "firmware.manifest", "INSTALL.md", "tests"}
SOC_FILES = {"soc.toml"}
SCHEMA_FILES = {"device.schema.json", "boot.schema.json", "soc.schema.json"}
MAX_FILE_BYTES = 512 * 1024
BLOB_SUFFIXES = {
    ".bin", ".b00", ".mbn", ".mdt", ".elf", ".img", ".fw", ".hex", ".dtb", ".dtbo",
    ".ko", ".so", ".zst", ".xz", ".gz", ".zip", ".tar", ".apk", ".rpm", ".deb",
    ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".minisign", ".gpg", ".asc",
}
MANIFEST_PATH = re.compile(r"^[A-Za-z0-9_+-][A-Za-z0-9_./+-]*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DIGITS = re.compile(r"(?<![0-9A-Za-z])[0-9]{15,20}(?![0-9A-Za-z])")
REQUIRED_ENFORCING = "enforcing=1"


def luhn_valid(number: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(number)):
        digit = int(char)
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def looks_like_identifier(number: str) -> str | None:
    """Return a label when a bare digit run could be an IMEI or ICCID."""
    if len(number) == 15 and luhn_valid(number):
        return "IMEI"
    if number.startswith("89") and len(number) in (19, 20):
        return "ICCID"
    return None


class Checker:
    def __init__(self, root: Path):
        self.root = root
        self.errors: list[str] = []
        self.schemas: dict[str, dict] = {}

    def error(self, path: Path | str, message: str) -> None:
        shown = path.relative_to(self.root) if isinstance(path, Path) else path
        self.errors.append(f"{shown}: {message}")

    def load_schema(self, path: Path) -> dict:
        key = str(path)
        if key not in self.schemas:
            schema = json.loads(path.read_text())
            jsonschema.Draft202012Validator.check_schema(schema)
            self.schemas[key] = schema
        return self.schemas[key]

    def validate(self, document: dict, schema_path: Path, where: Path) -> None:
        validator = jsonschema.Draft202012Validator(self.load_schema(schema_path))
        for problem in sorted(validator.iter_errors(document), key=lambda e: list(e.path)):
            location = "/".join(str(part) for part in problem.path) or "(top)"
            self.error(where, f"{location}: {problem.message}")

    def load_toml(self, path: Path) -> dict | None:
        try:
            return tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            self.error(path, f"cannot parse: {exc}")
            return None

    # -- tree-wide rules -------------------------------------------------

    def check_public_tree(self, base: Path) -> None:
        for path in sorted(base.rglob("*")):
            if path.is_symlink():
                self.error(path, "symlinks are not allowed")
                continue
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            if path.suffix.lower() in BLOB_SUFFIXES:
                self.error(path, "binary, archive or key file type is not allowed here")
                continue
            data = path.read_bytes()
            if len(data) > MAX_FILE_BYTES:
                self.error(path, f"larger than {MAX_FILE_BYTES} bytes; blobs stay out of git")
                continue
            if b"\0" in data:
                self.error(path, "contains NUL bytes; only text belongs here")
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                self.error(path, "not UTF-8 text")
                continue
            for match in DIGITS.finditer(text):
                label = looks_like_identifier(match.group())
                if label:
                    self.error(path, f"contains a number shaped like an {label}; never commit device identifiers")

    def check_codeowners(self, directories: list[tuple[Path, list[str]]]) -> None:
        codeowners = self.root / ".github" / "CODEOWNERS"
        if not codeowners.is_file():
            self.error(".github/CODEOWNERS", "missing")
            return
        rules: dict[str, set[str]] = {}
        for line in codeowners.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                pattern, *owners = line.split()
                rules[pattern.rstrip("/")] = set(owners)
        for directory, maintainers in directories:
            key = "/" + str(directory.relative_to(self.root))
            owners = rules.get(key)
            if owners is None:
                self.error(".github/CODEOWNERS", f"no entry for {key}/")
            elif not set(maintainers) <= owners:
                self.error(".github/CODEOWNERS", f"{key}/ must list its maintainers {sorted(maintainers)}")

    # -- SoC families ----------------------------------------------------

    def check_socs(self) -> dict[str, dict]:
        socs: dict[str, dict] = {}
        base = self.root / "socs"
        schema = base / "soc.schema.json"
        for vendor in sorted(p for p in base.iterdir() if p.is_dir()):
            for family in sorted(p for p in vendor.iterdir() if p.is_dir()):
                extra = {p.name for p in family.iterdir()} - SOC_FILES
                if extra:
                    self.error(family, f"unexpected entries {sorted(extra)}; a SoC holds only soc.toml")
                document = self.load_toml(family / "soc.toml") if (family / "soc.toml").is_file() else None
                if document is None:
                    self.error(family, "missing soc.toml")
                    continue
                self.validate(document, schema, family / "soc.toml")
                if (document.get("vendor"), document.get("family")) != (vendor.name, family.name):
                    self.error(family / "soc.toml", "vendor/family must match the directory path")
                socs[f"{vendor.name}/{family.name}"] = document
        for stray in sorted(p for p in base.iterdir() if p.is_file() and p.name not in SCHEMA_FILES | {"README.md"}):
            self.error(stray, "unexpected file in socs/")
        return socs

    # -- devices ---------------------------------------------------------

    def check_firmware_manifest(self, device_dir: Path, firmware: dict) -> None:
        path = device_dir / "firmware.manifest"
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != firmware.get("manifest_sha256"):
            self.error(path, "SHA-256 differs from firmware.manifest_sha256 in device.toml")
        lines = data.decode("utf-8", "replace").splitlines()
        if not lines:
            self.error(path, "empty")
            return
        header = lines[0].split("\t")
        if len(header) != 5 or header[0] != "H" or not header[1].isdigit() or header[2:] != ["-", "-", "-"]:
            self.error(path, "first line must be H<TAB><file count><TAB>-<TAB>-<TAB>-")
            return
        seen: set[str] = set()
        files = 0
        for number, line in enumerate(lines[1:], start=2):
            fields = line.split("\t")
            if len(fields) != 5:
                self.error(path, f"line {number}: expected 5 tab-separated fields")
                continue
            kind, mode, digest, relative, target = fields
            pure = PurePosixPath(relative)
            if not MANIFEST_PATH.match(relative) or ".." in pure.parts:
                self.error(path, f"line {number}: unsafe path {relative!r}")
            if relative in seen:
                self.error(path, f"line {number}: duplicate path {relative!r}")
            seen.add(relative)
            if kind == "D":
                ok = mode in ("755",) and digest == "-" and target == "-"
            elif kind == "F":
                ok = mode == "644" and bool(SHA256.match(digest)) and target == "-"
                files += 1
            elif kind == "L":
                resolved = PurePosixPath(*pure.parent.parts, *PurePosixPath(target).parts)
                ok = (mode == "777" and digest == "-" and not target.startswith("/")
                      and MANIFEST_PATH.match(target.replace("../", "")) is not None)
                depth = 0
                for part in resolved.parts:
                    depth += -1 if part == ".." else 1
                    ok = ok and depth >= 0
            else:
                ok = False
            if not ok:
                self.error(path, f"line {number}: malformed {kind!r} row")
        if files != int(header[1]):
            self.error(path, f"header says {header[1]} files but {files} F rows are listed")
        if files != firmware.get("files"):
            self.error(path, f"{files} files listed but device.toml says {firmware.get('files')}")

    def check_initramfs(self, device_dir: Path, initramfs: dict | None) -> None:
        """Repository files the boot image's initramfs is built from must exist as recorded."""
        if not initramfs:
            return
        where = device_dir / "device.toml"
        script = initramfs.get("init_functions")
        if script:
            path = self.root / script
            if not path.is_file():
                self.error(where, f"initramfs.init_functions {script!r} does not exist")
            elif hashlib.sha256(path.read_bytes()).hexdigest() != initramfs.get("init_functions_sha256"):
                self.error(where, f"{script} differs from initramfs.init_functions_sha256")
        source = initramfs.get("ostree_source")
        if source:
            path = self.root / source
            if not path.is_file():
                self.error(where, f"initramfs.ostree_source {source!r} does not exist")
            else:
                pins = json.loads(path.read_text())
                package = f"ostree-{pins.get('version')}-{pins.get('release')}"
                if not initramfs.get("ostree_package", "").startswith(package + "."):
                    self.error(where, f"initramfs.ostree_package does not match {source} ({package})")

    def check_boot(self, device_dir: Path, boot: dict, soc: dict | None, tier: str) -> None:
        path = device_dir / "boot.toml"
        cmdline = boot.get("cmdline", {})
        args = cmdline.get("args", [])
        for forbidden in cmdline.get("forbidden", []):
            if any(arg == forbidden or (forbidden.endswith("=") and arg.startswith(forbidden)) for arg in args):
                self.error(path, f"cmdline.args contains forbidden {forbidden!r}")
        keys = [arg.split("=", 1)[0] for arg in args]
        for generated in cmdline.get("generated", []):
            if generated in keys:
                self.error(path, f"{generated!r} is generated per image; do not fix it in args")
        if tier != "experimental" and REQUIRED_ENFORCING not in args:
            self.error(path, f"cmdline.args must contain {REQUIRED_ENFORCING!r} (SELinux enforcing)")
        soc_boot = (soc or {}).get("boot", {})
        if "format" not in boot and "format" not in soc_boot:
            self.error(path, "boot format is set neither here nor in the SoC")
        if boot.get("format", soc_boot.get("format", "")).startswith("android-boot"):
            if "page_size" not in boot and "page_size" not in soc_boot:
                self.error(path, "page_size is set neither here nor in the SoC")

    def check_install_guide(self, device_dir: Path, install: dict) -> None:
        path = device_dir / "INSTALL.md"
        text = path.read_text(encoding="utf-8")
        for partition in install.get("preserve", []):
            pattern = re.compile(rf"fastboot\s+(flash|erase|format)(:\S+)?\s+{re.escape(partition)}\b")
            if pattern.search(text):
                self.error(path, f"writes or erases preserved partition {partition!r}")

    def check_imager(self, device_dir: Path, device: dict) -> None:
        """The [imager] table must describe something Imager can actually do safely."""
        imager = device.get("imager")
        if not imager:
            return
        where = device_dir / "device.toml"
        method = imager.get("method")
        install = device.get("install", {})
        releases = device.get("releases", [])
        if imager.get("status") == "available":
            if method == "none":
                self.error(where, "imager.status 'available' needs an install method other than 'none'")
            if not releases:
                self.error(where, "imager.status 'available' needs a [[releases]] entry")
            if not imager.get("guide"):
                self.error(where, "imager.status 'available' needs imager.guide steps")
        if method == "fastboot_bundle":
            if install.get("method") != "fastboot":
                self.error(where, "imager.method 'fastboot_bundle' needs install.method 'fastboot'")
            if not device.get("fastboot_product"):
                self.error(where, "imager.method 'fastboot_bundle' needs fastboot_product")
        for release in releases:
            for key in ("boot_image", "system_image", "checksums"):
                if key in release and f"{key}_size_bytes" not in release and imager.get("status") == "available":
                    self.error(where, f"release {release.get('name')!r}: {key}_size_bytes is needed as the "
                                      "catalog's fallback size")
        texts = [imager.get("summary", "")] + list(imager.get("needs", []))
        for step in imager.get("guide", []):
            texts += [step.get("title", ""), step.get("body", "")]
        for text in texts:
            if "!" in text:
                self.error(where, f"imager text must not use exclamation marks: {text[:40]!r}")
            for partition in install.get("preserve", []):
                pattern = re.compile(rf"fastboot\s+(flash|erase|format)(:\S+)?\s+{re.escape(partition)}\b")
                if pattern.search(text):
                    self.error(where, f"imager.guide writes or erases preserved partition {partition!r}")

    def check_tests(self, device_dir: Path, vendor: str, codename: str) -> list[Path]:
        tests = device_dir / "tests"
        found = sorted(tests.glob("test_*.py")) if tests.is_dir() else []
        if not found:
            self.error(device_dir, "tests/ must hold at least one test_*.py")
        prefix = f"test_{vendor}_{codename}"
        for test in found:
            if not test.stem.startswith(prefix):
                self.error(test, f"device test files must be named {prefix}*.py so names stay unique")
        extra = {p.name for p in tests.iterdir()} - {p.name for p in found} - {"__pycache__"} if tests.is_dir() else set()
        if extra:
            self.error(tests, f"unexpected entries {sorted(extra)}")
        return found

    def check_devices(self, socs: dict[str, dict]) -> tuple[list[Path], list[tuple[Path, list[str]]]]:
        base = self.root / "devices"
        test_files: list[Path] = []
        owned: list[tuple[Path, list[str]]] = []
        codenames: dict[str, Path] = {}
        for stray in sorted(p for p in base.iterdir() if p.is_file() and p.name not in SCHEMA_FILES | {"README.md"}):
            self.error(stray, "unexpected file in devices/")
        for vendor in sorted(p for p in base.iterdir() if p.is_dir()):
            for device_dir in sorted(p for p in vendor.iterdir() if p.is_dir()):
                if device_dir.name == "__pycache__":
                    continue
                entries = {p.name for p in device_dir.iterdir()}
                if entries != DEVICE_FILES:
                    missing, extra = DEVICE_FILES - entries, entries - DEVICE_FILES
                    self.error(device_dir, f"must hold exactly {sorted(DEVICE_FILES)}"
                               + (f"; missing {sorted(missing)}" if missing else "")
                               + (f"; unexpected {sorted(extra)}" if extra else ""))
                    if not {"device.toml", "boot.toml"} <= entries:
                        continue
                device = self.load_toml(device_dir / "device.toml")
                boot = self.load_toml(device_dir / "boot.toml")
                if device is None or boot is None:
                    continue
                self.validate(device, base / "device.schema.json", device_dir / "device.toml")
                self.validate(boot, base / "boot.schema.json", device_dir / "boot.toml")
                if (device.get("vendor"), device.get("codename")) != (vendor.name, device_dir.name):
                    self.error(device_dir / "device.toml", "vendor/codename must match the directory path")
                codename = device.get("codename", device_dir.name)
                if codename in codenames:
                    self.error(device_dir, f"codename also used by {codenames[codename].relative_to(self.root)}")
                codenames[codename] = device_dir
                soc = socs.get(device.get("soc", ""))
                if soc is None:
                    self.error(device_dir / "device.toml", f"soc {device.get('soc')!r} has no socs/ directory")
                tag = device.get("kernel", {}).get("tag", "")
                if tag and not tag.startswith(f"luma-{codename}-"):
                    self.error(device_dir / "device.toml", f"kernel.tag must start with 'luma-{codename}-'")
                release = device.get("kernel", {}).get("release", "")
                if tag and release and not tag.endswith("-" + release):
                    self.error(device_dir / "device.toml", "kernel.tag must end with the kernel release")
                self.check_initramfs(device_dir, device.get("initramfs"))
                if (device_dir / "firmware.manifest").is_file():
                    self.check_firmware_manifest(device_dir, device.get("firmware", {}))
                self.check_boot(device_dir, boot, soc, device.get("tier", ""))
                if (device_dir / "INSTALL.md").is_file():
                    self.check_install_guide(device_dir, device.get("install", {}))
                self.check_imager(device_dir, device)
                test_files += self.check_tests(device_dir, vendor.name, device_dir.name)
                owned.append((device_dir, device.get("maintainers", [])))
        return test_files, owned

    def run(self) -> list[Path]:
        for base in ("devices", "socs"):
            if not (self.root / base).is_dir():
                self.error(base, "missing directory")
                return []
            self.check_public_tree(self.root / base)
        socs = self.check_socs()
        test_files, owned = self.check_devices(socs)
        soc_dirs = [(self.root / "socs" / key, []) for key in socs]
        self.check_codeowners(owned + soc_dirs)
        return test_files


def run_device_tests(test_files: list[Path]) -> bool:
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for test in test_files:
        suite.addTests(loader.discover(str(test.parent), pattern=test.name, top_level_dir=str(test.parent)))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return result.wasSuccessful()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--no-tests", action="store_true", help="skip each device's own tests")
    args = parser.parse_args(argv)
    if jsonschema is None:
        print("check-devices: the jsonschema module is required (pip install jsonschema)", file=sys.stderr)
        return 2
    checker = Checker(args.root.resolve())
    test_files = checker.run()
    for message in checker.errors:
        print(f"error: {message}")
    if checker.errors:
        print(f"{len(checker.errors)} problem(s) found")
        return 1
    print(f"devices: {len({p.parent.parent for p in test_files})} valid")
    if not args.no_tests and not run_device_tests(test_files):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
