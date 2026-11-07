#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build a phone's device kit from devices/<vendor>/<codename>/device.toml.

A phone downloads two things: the generic phone userdata image
(scripts/mobile/build-phone-userdata.sh, one per ARM build) and its device kit:

  luma-<codename>-<version>-boot.img   the device's boot image (kernel, DT,
                                       initramfs with the shared OSTree resolver)
  luma-<codename>-<version>-kit.sparse the device packages, offline: a LumaBoot
                                       filesystem (FAT32, volume ID 032D-57AD)
                                       holding luma-device-kit/ (kit.json,
                                       kit.json.asc, repo/), as an Android sparse
                                       image that writes only LumaBoot's range of
                                       userdata (DONT_CARE elsewhere)
  dtbo-blank.img                       when boot.toml [dtbo] policy is "blank"
  kit.json, FLASH.txt, SHA256SUMS

FLASH.txt is device.toml [kit] flash, in order, as fastboot commands. On the
first boot luma-device-layer.service (luma-update) checks kit.json and the
repository against the Luma OS Release key, checks [kit] compatible against
the device tree, layers [device_repo] (override replace, then install) from
the local copy and restarts once; updates then follow [device_repo] url.

Usage:
  build-device-kit.py --device VENDOR/CODENAME --boot-image FILE --version V --output DIR
      [--device-repo DIR --key RELEASE_KEY.asc --sign-homedir GNUPGHOME --sign-key FPR]
      [--boot-cmdline FILE] [--manifest FILE] [--userdata-name NAME]

Runs on Linux with gpg, gpgv, rpm, mkfs.vfat and mcopy (mtools).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT_UUID = "03797c05-cf87-4eda-adc4-6ce111baf2d3"
BOOT_UUID = "032D-57AD"
LUMABOOT_OFFSET = 1 << 20      # start=256 4096-byte sectors
LUMABOOT_SIZE = 480 << 20
PLACEHOLDERS = {"userdata", "kit", "boot", "dtbo"}
FLASH = re.compile(r"^(flash [a-z0-9_]+ \{(userdata|kit|boot|dtbo)\}|boot \{boot\}|--set-active=[ab]|reboot)$")


class KitError(Exception):
    pass


def load_manifest(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def kit_document(device: dict, packages: list[str], version: str) -> dict:
    """kit.json: what luma-device-layer reads (src/luma-update/luma_update/device_layer.py)."""
    kit, repo = device.get("kit", {}), device.get("device_repo")
    if not repo:
        raise KitError("device.toml has no [device_repo]: nothing to layer")
    wanted = list(repo.get("replace", [])) + list(repo.get("install", []))
    missing = sorted(set(wanted) - set(packages))
    if missing:
        raise KitError(f"the device repository lacks {', '.join(missing)}")
    return {
        "schema": 1,
        "device": f"{device['vendor']}/{device['codename']}",
        "name": device.get("marketing_name", device["name"]),
        "version": version,
        "compatible": list(kit["compatible"]),
        "repo": {"id": repo["id"], "url": repo["url"]},
        "replace": list(repo.get("replace", [])),
        "install": list(repo.get("install", [])),
        "packages": sorted(set(packages)),
    }


def flash_steps(device: dict, files: dict[str, str]) -> list[str]:
    """fastboot commands from [kit] flash, refusing preserved partitions and missing files."""
    preserve = set(device.get("install", {}).get("preserve", []))
    steps = []
    for step in device["kit"]["flash"]:
        if not FLASH.match(step):
            raise KitError(f"[kit] flash step {step!r} is not a supported fastboot command")
        words = step.split()
        if words[0] == "flash" and words[1] in preserve:
            raise KitError(f"[kit] flash writes preserved partition {words[1]}")
        for name in re.findall(r"\{(\w+)\}", step):
            if name not in files:
                raise KitError(f"[kit] flash uses {{{name}}} but this kit has no such file")
        steps.append("fastboot " + step.format(**files))
    if "kit" in files and not any("{kit}" in s for s in device["kit"]["flash"]):
        raise KitError("[kit] flash never writes {kit}")
    return steps


def check_boot_image(data: bytes) -> None:
    for arg in (f"pmos_root_uuid={ROOT_UUID}", f"pmos_boot_uuid={BOOT_UUID}"):
        if arg.encode() not in data:
            raise KitError(f"the boot image's command line lacks {arg} (the generic phone userdata image)")


def run(argv: list[str], **kwargs) -> str:
    result = subprocess.run(argv, text=True, capture_output=True, **kwargs)
    if result.returncode != 0:
        raise KitError(f"{argv[0]} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def keyring(key: Path, work: Path) -> Path:
    path = work / "release.gpg"
    run(["gpg", "--batch", "--yes", "--dearmor", "--output", str(path), str(key)])
    return path


def repo_packages(repo: Path, ring: Path) -> list[str]:
    repomd = repo / "repodata/repomd.xml"
    if not repomd.is_file() or not (repo / "repodata/repomd.xml.asc").is_file():
        raise KitError(f"{repo} is not a signed rpm-md repository")
    run(["gpgv", "--keyring", str(ring), str(repomd) + ".asc", str(repomd)])
    rpms = sorted((repo / "Packages").glob("*.rpm"))
    if not rpms:
        raise KitError(f"{repo}/Packages holds no RPMs")
    names = run(["rpm", "-qp", "--nosignature", "--qf", "%{NAME}\\n", *map(str, rpms)]).split()
    return sorted(set(names))


def make_kit_filesystem(kit_dir: Path, image: Path) -> None:
    image.unlink(missing_ok=True)
    run(["mkfs.vfat", "-C", "-F", "32", "-S", "4096", "-n", "LUMABOOT", "-i", BOOT_UUID.replace("-", ""),
         str(image), str(LUMABOOT_SIZE // 1024)])
    env = dict(os.environ, MTOOLS_SKIP_CHECK="1")
    run(["mcopy", "-s", "-p", "-m", "-i", str(image), str(kit_dir), "::/"], env=env)
    listing = run(["mdir", "-/", "-b", "-i", str(image), "::/luma-device-kit"], env=env)
    if "kit.json.asc" not in listing:
        raise KitError("the kit filesystem lacks kit.json.asc after copying")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build(args: argparse.Namespace) -> None:
    manifest = args.manifest or REPO_ROOT / "devices" / args.device / "device.toml"
    device = load_manifest(manifest)
    if f"{device['vendor']}/{device['codename']}" != args.device:
        raise KitError(f"{manifest} is not {args.device}")
    if "kit" not in device:
        raise KitError(f"{manifest} has no [kit]")
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    prefix = f"luma-{device['codename']}-{args.version}"
    files = {"userdata": args.userdata_name}

    boot = out / f"{prefix}-boot.img"
    data = Path(args.boot_image).read_bytes()
    # A command line built into a compressed kernel (CONFIG_CMDLINE) is not
    # readable in the image; --boot-cmdline names the boot build's record of it.
    check_boot_image(Path(args.boot_cmdline).read_bytes() if args.boot_cmdline else data)
    boot.write_bytes(data)
    files["boot"] = boot.name

    boot_toml = (manifest.parent / device.get("boot", {}).get("config", "boot.toml"))
    dtbo = load_manifest(boot_toml).get("dtbo", {}) if boot_toml.is_file() else {}
    if dtbo.get("policy") == "blank":
        blank = out / "dtbo-blank.img"
        with blank.open("wb") as f:
            f.truncate(dtbo["size"])
        files["dtbo"] = blank.name

    if device.get("device_repo"):
        if not (args.device_repo and args.key and args.sign_homedir and args.sign_key):
            raise KitError("[device_repo] needs --device-repo, --key, --sign-homedir and --sign-key")
        with tempfile.TemporaryDirectory(dir=args.output) as tmp:
            work = Path(tmp)
            ring = keyring(Path(args.key), work)
            repo = Path(args.device_repo)
            document = kit_document(device, repo_packages(repo, ring), args.version)
            kit_dir = work / "luma-device-kit"
            shutil.copytree(repo, kit_dir / "repo", ignore=shutil.ignore_patterns("SHA256SUMS", "*.old"))
            (kit_dir / "kit.json").write_text(json.dumps(document, indent=2) + "\n")
            run(["gpg", "--batch", "--yes", "--homedir", args.sign_homedir, "--local-user", args.sign_key,
                 "--armor", "--detach-sign", "--output", str(kit_dir / "kit.json.asc"), str(kit_dir / "kit.json")])
            run(["gpgv", "--keyring", str(ring), str(kit_dir / "kit.json.asc"), str(kit_dir / "kit.json")])
            used = sum(p.stat().st_size for p in kit_dir.rglob("*") if p.is_file())
            if used > LUMABOOT_SIZE - (16 << 20):
                raise KitError(f"the kit holds {used >> 20} MiB; LumaBoot has {LUMABOOT_SIZE >> 20} MiB")
            fat = work / "lumaboot.vfat"
            make_kit_filesystem(kit_dir, fat)
            sparse = out / f"{prefix}-kit.sparse"
            run([sys.executable, str(REPO_ROOT / "scripts/mobile/img2simg.py"), str(fat), str(sparse),
                 "--at", str(LUMABOOT_OFFSET)])
            shutil.copy2(kit_dir / "kit.json", out / "kit.json")
            files["kit"] = sparse.name

    steps = flash_steps(device, files)
    (out / "FLASH.txt").write_text(
        f"# {device.get('marketing_name', device['name'])}: Luma {args.version}\n"
        f"# {args.userdata_name} is the generic phone userdata image of the same build.\n"
        + "".join(s + "\n" for s in steps))
    sums = [f"{sha256(out / name)}  {name}" for name in sorted(set(files.values()) - {args.userdata_name})]
    (out / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    print((out / "FLASH.txt").read_text(), end="")
    print((out / "SHA256SUMS").read_text(), end="")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--device", required=True, help="VENDOR/CODENAME")
    parser.add_argument("--manifest", type=Path, help="device.toml (default devices/VENDOR/CODENAME/device.toml)")
    parser.add_argument("--boot-image", required=True)
    parser.add_argument("--boot-cmdline", help="file with the boot image's command line, when the image "
                        "does not hold it readably (a kernel built with CONFIG_CMDLINE)")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--device-repo")
    parser.add_argument("--key", help="the Luma OS Release public key, armored")
    parser.add_argument("--sign-homedir", help="GNUPGHOME with the Luma OS Release secret key")
    parser.add_argument("--sign-key", help="fingerprint of the Luma OS Release key")
    parser.add_argument("--userdata-name", default="luma-phone-userdata.sparse")
    args = parser.parse_args(argv)
    try:
        build(args)
    except (KitError, KeyError, OSError) as exc:
        print(f"build-device-kit: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
