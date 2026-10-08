#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Prepare a hash-locked, host-mounted APEX runtime for the FP6 container.

Run as root on the Fairphone. All source partition mounts and APEX payload
mounts are read-only. The resulting loop devices are retained by the host and
must never be passed to the Android container together with loop-control.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zipfile


OUTPUT = Path("/var/tmp/luma-stock-qrel1695-apex-runtime2")
EXPECTED_IMAGES = {
    Path("/var/tmp/luma-stock-qrel1695-images/system_a.img"):
        "c817290357023c4831e31aaf26ef2ec9ffc984c43c98ab6902deb19739746122",
    Path("/var/tmp/luma-stock-qrel1695-images/vendor_a.img"):
        "4159a51642c7f6462012891197a237ad86768a1f55853c6ab3b38d6ff2b144b4",
    Path("/var/tmp/luma-stock-qrel1695-images/system_ext_a.img"):
        "71e9c5b743ec957562788340ffae8cdfea758c586da858421950525079bacb09",
    Path("/var/tmp/luma-stock-qrel1695-images/product_a.img"):
        "c5b458671c560dda0da70c8e2c5e9c1ea0e01b11d6f45c3f8009dc5964b15937",
    Path("/var/tmp/luma-stock-qrel1695-images/odm_a.img"):
        "3d31463d172b485f866e25340887e36b96986bb5c2255bff9a6b7777277bb831",
}
APEX_DIRS = (
    ("system", Path("/var/tmp/luma-stock-qrel1695-system/system/apex")),
    ("system_ext", Path("/var/tmp/luma-stock-qrel1695-system_ext/apex")),
    ("product", Path("/var/tmp/luma-stock-qrel1695-product/apex")),
    ("vendor", Path("/var/tmp/luma-stock-qrel1695-vendor/apex")),
    ("odm", Path("/var/tmp/luma-stock-qrel1695-odm/apex")),
)


def die(message: str) -> "None":
    raise SystemExit(f"error: {message}")


def run(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    die("invalid APEX manifest varint")


def parse_manifest(data: bytes) -> tuple[str, int]:
    offset = 0
    name: str | None = None
    version: int | None = None
    while offset < len(data):
        key, offset = read_varint(data, offset)
        field = key >> 3
        wire = key & 7
        if wire == 0:
            value, offset = read_varint(data, offset)
            if field == 2:
                version = value
        elif wire == 1:
            offset += 8
        elif wire == 2:
            length, offset = read_varint(data, offset)
            end = offset + length
            if end > len(data):
                die("truncated APEX manifest")
            if field == 1:
                name = data[offset:end].decode("utf-8")
            offset = end
        elif wire == 5:
            offset += 4
        else:
            die(f"unsupported APEX manifest wire type {wire}")
    if not name or version is None:
        die("APEX manifest lacks name or version")
    if not all(char.isalnum() or char in "._" for char in name):
        die(f"unsafe APEX name {name!r}")
    return name, version


def assert_read_only_mount(path: Path) -> None:
    result = run("findmnt", "-rn", "-T", str(path), "-o", "OPTIONS", capture=True)
    if "ro" not in result.stdout.strip().split(","):
        die(f"source mount is not read-only: {path}")


def cleanup(mounts: list[Path]) -> None:
    for mountpoint in reversed(mounts):
        subprocess.run(("umount", str(mountpoint)), check=False)
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)


def main() -> int:
    if os.geteuid() != 0:
        die("run as root on the Fairphone")
    if OUTPUT.exists():
        die(f"refuse to overwrite {OUTPUT}")
    for image, expected in EXPECTED_IMAGES.items():
        if not image.is_file() or image.is_symlink():
            die(f"partition image missing or symlinked: {image}")
        actual = sha256(image)
        if actual != expected:
            die(f"partition image hash mismatch: {image}")
    for _, apex_dir in APEX_DIRS:
        assert_read_only_mount(apex_dir.parent)

    images_dir = OUTPUT / "images"
    mounts_dir = OUTPUT / "mounts"
    metadata_dir = OUTPUT / "metadata"
    for directory in (images_dir, mounts_dir, metadata_dir):
        directory.mkdir(parents=True, mode=0o700)

    mounted: list[Path] = []
    inventory: list[dict[str, object]] = []
    names: set[str] = set()
    try:
        sources: list[tuple[str, Path]] = []
        for partition, apex_dir in APEX_DIRS:
            if not apex_dir.is_dir():
                continue
            sources.extend(
                (partition, path)
                for path in sorted(apex_dir.iterdir())
                if path.is_file() and path.suffix in {".apex", ".capex"}
            )
        if len(sources) != 41:
            die(f"expected 41 APEX/CAPEX packages, found {len(sources)}")

        for partition, source in sources:
            source_hash = sha256(source)
            with zipfile.ZipFile(source) as outer:
                is_compressed = "original_apex" in outer.namelist()
                if is_compressed:
                    original_bytes = outer.read("original_apex")
                    original_hash = hashlib.sha256(original_bytes).hexdigest()
                else:
                    original_bytes = source.read_bytes()
                    original_hash = source_hash

            with zipfile.ZipFile(io.BytesIO(original_bytes)) as apex:
                required = {"apex_manifest.pb", "apex_payload.img", "apex_pubkey"}
                if not required.issubset(apex.namelist()):
                    die(f"APEX payload incomplete: {source}")
                manifest_bytes = apex.read("apex_manifest.pb")
                public_key = apex.read("apex_pubkey")
                payload_info = apex.getinfo("apex_payload.img")
                if (
                    payload_info.compress_type != zipfile.ZIP_STORED
                    or payload_info.compress_size != payload_info.file_size
                ):
                    die(f"APEX payload is not stored verbatim: {source}")
                (
                    signature,
                    _version,
                    _flags,
                    compression,
                    _mtime,
                    _mdate,
                    _crc,
                    compressed_size,
                    uncompressed_size,
                    filename_length,
                    extra_length,
                ) = struct.unpack_from(
                    "<4s5H3I2H", original_bytes, payload_info.header_offset
                )
                if (
                    signature != b"PK\x03\x04"
                    or compression != 0
                    or compressed_size != payload_info.file_size
                    or uncompressed_size != payload_info.file_size
                ):
                    die(f"APEX local payload header differs: {source}")
                payload_offset = (
                    payload_info.header_offset + 30 + filename_length + extra_length
                )
                name, version = parse_manifest(manifest_bytes)
                if name in names:
                    die(f"duplicate APEX package name: {name}")
                names.add(name)
                package_id = f"{name}@{version}"
                apex_path = images_dir / f"{package_id}.apex"
                apex_path.write_bytes(original_bytes)
                payload_verify_path = OUTPUT / ".payload-verify.img"
                with (
                    apex.open("apex_payload.img") as source_stream,
                    payload_verify_path.open("wb") as out,
                ):
                    shutil.copyfileobj(source_stream, out, 1024 * 1024)

            os.chmod(apex_path, 0o400)
            run("avbtool", "verify_image", "--image", str(payload_verify_path))
            payload_hash = sha256(payload_verify_path)
            payload_verify_path.unlink()
            mountpoint = mounts_dir / package_id
            mountpoint.mkdir(mode=0o700)
            run(
                "mount",
                "-o",
                (
                    f"loop,ro,nosuid,nodev,offset={payload_offset},"
                    f"sizelimit={payload_info.file_size}"
                ),
                str(apex_path),
                str(mountpoint),
            )
            assert_read_only_mount(mountpoint)
            mounted.append(mountpoint)

            entry = {
                "name": name,
                "version": version,
                "partition": partition,
                "source": str(source),
                "compressed": is_compressed,
                "source_sha256": source_hash,
                "original_apex_sha256": original_hash,
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "public_key_sha256": hashlib.sha256(public_key).hexdigest(),
                "payload_sha256": payload_hash,
                "payload_offset": payload_offset,
                "payload_size": payload_info.file_size,
                "runtime_apex": str(apex_path),
                "runtime_apex_sha256": sha256(apex_path),
                "mountpoint": str(mountpoint),
            }
            inventory.append(entry)
            print(f"prepared {package_id} from {partition}", flush=True)

        inventory_path = metadata_dir / "activated-apex-inventory.json"
        inventory_path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
        os.chmod(inventory_path, 0o400)
        image_manifest = metadata_dir / "partition-images.sha256"
        image_manifest.write_text(
            "".join(f"{expected}  {image}\n" for image, expected in EXPECTED_IMAGES.items())
        )
        os.chmod(image_manifest, 0o400)
        seal = metadata_dir / "HASHES.sha256"
        files_to_seal = sorted(images_dir.iterdir()) + [inventory_path, image_manifest]
        seal.write_text("".join(f"{sha256(path)}  {path}\n" for path in files_to_seal))
        os.chmod(seal, 0o400)
        os.chmod(images_dir, 0o500)
        os.chmod(mounts_dir, 0o500)
        os.chmod(metadata_dir, 0o500)
        os.chmod(OUTPUT, 0o500)
    except BaseException:
        cleanup(mounted)
        raise

    print(f"READY_APEX_RUNTIME=true count={len(inventory)} root={OUTPUT}")
    print("LOOP_CONTROL_EXPOSED_TO_CONTAINER=false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
