#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Compose, but never start, the hardware-free QREL Android C1 container.

The exact stock partition and APEX mounts remain immutable lower layers.
Generated overlays add only audited C1 service blockers, while disposable
state supplies `/data`, `/metadata`, and `/linkerconfig`. The resulting LXC
configuration exposes no modem, audio, GPU, input, eUICC, loop, block, or
device-mapper node.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile


NAME = "luma-stock-android-c1"
STATE = Path("/var/lib/luma/stock-android-container1")
LXC_DIR = Path("/var/lib/lxc") / NAME
HOOK_INPUT = Path("/var/tmp/luma-stock-android-container-build-inputs/fp6-stock-android-binderfs-lxc-hook.py")
HOOK_SHA256 = "0b4b7ba7517a71b5894407e61ca5eaf9385a56b310f37b48c4e721e4fa2ffdca"
CONTAINER_INIT = Path("/var/tmp/luma-stock-android-container-build-inputs/init.container3")
CONTAINER_INIT_SHA256 = "270e1cda28deeac8b0619614a900f5f21f3eb7745e28d138d7f7a5835021243d"
STOCK_INIT_SHA256 = "11111bc72131e1e613ce0504aa0166e13207ff0f25b5767a959eb9baddd99e60"
APEX_ROOT = Path("/var/tmp/luma-stock-qrel1695-apex-runtime1")
APEX_INVENTORY = APEX_ROOT / "metadata/activated-apex-inventory.json"
APEX_INVENTORY_SHA256 = "4c0812666bb23251111a0f23b10db404a489c078e1ac7fdaa8975c1448a74869"
MANAGER_ROOT = Path("/var/tmp/luma-stock-qrel1695-manager-boundary4")
MANAGERS = {
    "servicemanager": "831e8c6ba29dc06fac2adfed1ac7544594d89951113ba25429268f990202f217",
    "hwservicemanager": "48dda02a0cad50107bb7737aee5b44b5228d789a5b3520fcb499249ac3ecc600",
    "vndservicemanager": "7d6bed816b8206dbf5fee4f269ca1066e328195199677746fdbbebdbdc1bca75",
    "manifest.json": "becc09c4f94c27307ec04beceb254915cb5f4f268fbff65a21f405b5354d8218",
}
BINDER_BOUNDARY_ROOT = Path("/var/tmp/luma-stock-qrel1695-binder-boundary3")
BINDER_BOUNDARY = {
    "libbinder.so": "38ac7e535e386f9e560089ce4ffe4e63e991c8b75f3b3ca441d47ecb8f46d254",
    "libhidlbase.so": "13a0a6eeac07b48b19cd32d2b3a6eecc0714538ac85e8fd391e7e392d513c259",
    "libbinder-vndk34.so": "ff47f142ea3065e5326279584f7a59591c02dd93085c8120466486814e4caea8",
    "manifest.json": "7167bc03eeb7dcef6346b094937b4b04ad2f6a8a4bc98d0a8e0664630bfb7dfa",
}
PARTITIONS = {
    "system": Path("/var/tmp/luma-stock-qrel1695-system"),
    "vendor": Path("/var/tmp/luma-stock-qrel1695-vendor"),
    "system_ext": Path("/var/tmp/luma-stock-qrel1695-system_ext"),
    "product": Path("/var/tmp/luma-stock-qrel1695-product"),
    "odm": Path("/var/tmp/luma-stock-qrel1695-odm"),
}
PARTITION_IMAGE_HASHES = {
    "system": "c817290357023c4831e31aaf26ef2ec9ffc984c43c98ab6902deb19739746122",
    "vendor": "4159a51642c7f6462012891197a237ad86768a1f55853c6ab3b38d6ff2b144b4",
    "system_ext": "71e9c5b743ec957562788340ffae8cdfea758c586da858421950525079bacb09",
    "product": "c5b458671c560dda0da70c8e2c5e9c1ea0e01b11d6f45c3f8009dc5964b15937",
    "odm": "3d31463d172b485f866e25340887e36b96986bb5c2255bff9a6b7777277bb831",
}
BLOCKED_EXECUTABLES = {
    "/vendor/bin/hw/qcrilNrd",
    "/vendor/bin/imsdaemon",
    "/vendor/bin/ims-dataservice-daemon",
    "/vendor/bin/ims_rtp_daemon",
    "/system/bin/audioserver",
    "/vendor/bin/audioadsprpcd",
    "/vendor/bin/hw/android.hardware.audio.service_64",
    "/system/bin/surfaceflinger",
    "/system/bin/bootanimation",
    # These services either require physical storage/slot state or can reach
    # Qualcomm DSP remoteprocs over kernel socket families even without a
    # character device in /dev. They belong only in later, explicitly gated
    # hardware phases and must remain absent from hardware-free C1.
    "/vendor/bin/hw/android.hardware.boot-service.qti",
    "/vendor/bin/hw/android.hardware.sensors-service.multihal",
    "/vendor/bin/sscrpcd",
    "/vendor/bin/adsprpcd",
    "/vendor/bin/cdsprpcd",
    "/system/vendor/bin/ipacm",
    "/system/vendor/bin/ipacm-diag",
    # The legacy daemon expects Android's old /dev/qseecom ABI.  The bounded
    # secure follow-on gate uses the modern TEE client node solely through the
    # exact QSEECom AIDL HAL; restarting qseecomd adds no KeyMint coverage and
    # creates an avoidable root process at the secure-device boundary.
    "/vendor/bin/qseecomd",
}
BLOCKED_BASENAME_PATTERNS = (
    re.compile(r"^android\.hardware\.audio\.service"),
    re.compile(r"^android\.hardware\.graphics\.composer"),
    re.compile(r"^vendor\..*(?:display|composer).*(?:service|hal)"),
)
REQUIRED_BLOCKS = {
    "/vendor/bin/hw/qcrilNrd",
    "/vendor/bin/imsdaemon",
    "/vendor/bin/ims-dataservice-daemon",
    "/vendor/bin/ims_rtp_daemon",
    "/system/bin/audioserver",
    "/vendor/bin/hw/android.hardware.audio.service_64",
    "/system/bin/surfaceflinger",
    "/vendor/bin/hw/android.hardware.boot-service.qti",
    "/vendor/bin/hw/android.hardware.sensors-service.multihal",
    "/vendor/bin/sscrpcd",
    "/vendor/bin/adsprpcd",
    "/vendor/bin/cdsprpcd",
    "/system/vendor/bin/ipacm",
    "/system/vendor/bin/ipacm-diag",
    "/vendor/bin/qseecomd",
}


def die(message: str) -> "None":
    raise RuntimeError(message)


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


def require_file(path: Path, expected: str) -> None:
    if not path.is_file() or path.is_symlink():
        die(f"missing or linked file: {path}")
    actual = sha256(path)
    if actual != expected:
        die(f"hash mismatch for {path}: {actual}")


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


def parse_apex_manifest(data: bytes) -> dict[str, object]:
    """Read the stock fields required by Android's ApexInfoList schema."""
    offset = 0
    values: dict[int, object] = {}
    while offset < len(data):
        key, offset = read_varint(data, offset)
        field = key >> 3
        wire = key & 7
        if wire == 0:
            value, offset = read_varint(data, offset)
            if field in (2, 11):
                values[field] = value
        elif wire == 1:
            offset += 8
        elif wire == 2:
            length, offset = read_varint(data, offset)
            end = offset + length
            if end > len(data):
                die("truncated APEX manifest")
            if field in (1, 5):
                values[field] = data[offset:end].decode("utf-8")
            offset = end
        elif wire == 5:
            offset += 4
        else:
            die(f"unsupported APEX manifest wire type {wire}")
    name = values.get(1)
    version = values.get(2)
    if not isinstance(name, str) or not name or not isinstance(version, int):
        die("APEX manifest lacks name or version")
    return {
        "name": name,
        "version": version,
        "version_name": values.get(5, ""),
        "provide_shared_apex_libs": bool(values.get(11, 0)),
    }


def read_apex_manifest(source: Path) -> tuple[dict[str, object], str]:
    with zipfile.ZipFile(source) as outer:
        if "original_apex" in outer.namelist():
            original = outer.read("original_apex")
            with zipfile.ZipFile(io.BytesIO(original)) as apex:
                data = apex.read("apex_manifest.pb")
        else:
            data = outer.read("apex_manifest.pb")
    return parse_apex_manifest(data), hashlib.sha256(data).hexdigest()


def write_apex_info_list(path: Path, inventory: list[dict[str, object]]) -> str:
    """Emit the stock apexd metadata boundary for the pre-mounted APEX set.

    The loop-free C1 adapter replaces apexd's mount operation, so it must also
    provide the metadata apexd normally derives from the same verified package
    manifests. Linkerconfig uses the partition field to construct the VNDK and
    other cross-partition namespaces; omitting this file produces an invalid
    graph even when every exact APEX payload is already visible.
    """
    root = ET.Element("apex-info-list")
    for entry in sorted(inventory, key=lambda item: str(item["name"])):
        source = Path(str(entry["source"]))
        if sha256(source) != entry["source_sha256"]:
            die(f"APEX source hash differs while generating metadata: {source}")
        apex, manifest_hash = read_apex_manifest(source)
        if manifest_hash != entry["manifest_sha256"]:
            die(f"APEX manifest hash differs while generating metadata: {source}")
        if apex["name"] != entry["name"] or apex["version"] != entry["version"]:
            die(f"APEX identity differs while generating metadata: {source}")
        partition = str(entry["partition"]).upper()
        if partition not in {"SYSTEM", "SYSTEM_EXT", "PRODUCT", "VENDOR", "ODM"}:
            die(f"invalid APEX source partition: {partition}")
        module_path = f"/{str(entry['partition'])}/apex/{source.name}"
        ET.SubElement(
            root,
            "apex-info",
            {
                "moduleName": str(apex["name"]),
                "modulePath": module_path,
                "preinstalledModulePath": module_path,
                "versionCode": str(apex["version"]),
                "versionName": str(apex["version_name"]),
                "isFactory": "true",
                "isActive": "true",
                "lastUpdateMillis": "0",
                "provideSharedApexLibs": (
                    "true" if apex["provide_shared_apex_libs"] else "false"
                ),
                "partition": partition,
            },
        )
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    path.chmod(0o444)
    return sha256(path)


def assert_read_only_mount(path: Path) -> None:
    result = run("findmnt", "-rn", "-T", str(path), "-o", "TARGET,OPTIONS", capture=True)
    fields = result.stdout.strip().split(maxsplit=1)
    if not fields or Path(fields[0]) != path:
        die(f"not an exact mountpoint: {path}")
    options = fields[1].split(",") if len(fields) == 2 else []
    if "ro" not in options:
        die(f"mount is writable: {path}")


def init_directory(partition: str, mounted: Path) -> Path:
    if partition == "system":
        return mounted / "system/etc/init"
    return mounted / "etc/init"


def blocked_service_definitions(lower: Path) -> list[tuple[str, str, Path]]:
    found: list[tuple[str, str, Path]] = []
    for rc in sorted(lower.rglob("*.rc")):
        if "/etc/init/" not in str(rc):
            continue
        try:
            lines = rc.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            match = re.match(r"^service\s+(\S+)\s+(\S+)", line)
            if not match:
                continue
            service, executable = match.groups()
            basename = Path(executable).name
            if executable in BLOCKED_EXECUTABLES or any(
                pattern.search(basename) for pattern in BLOCKED_BASENAME_PATTERNS
            ):
                found.append((service, executable, rc))
    return found


def write_override(path: Path, services: list[tuple[str, str, Path]]) -> None:
    lines = [
        "# Generated hardware-free C1 boundary. Exact lower images remain unchanged.",
        "",
    ]
    for service, executable, source in sorted(services):
        lines.extend(
            (
                f"# blocked executable={executable} source={source}",
                f"service {service} /system/bin/false",
                "    override",
                "    disabled",
                "    oneshot",
                "    user root",
                "    group root",
                "",
            )
        )
    path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    path.write_text("\n".join(lines))
    path.chmod(0o644)


def append_apex_adapter(path: Path) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            """
# Exact APEX payloads are already AVB-verified and mounted read-only by the
# host. These lifecycle adapters prevent stock apexd from requesting forbidden
# loop and device-mapper control while retaining Android's normal boot gates.
service apexd-bootstrap /system/bin/luma-apexd-container --bootstrap
    override
    disabled
    oneshot
    user root
    group system

service apexd /system/bin/luma-apexd-container --daemon
    override
    disabled
    user root
    group system

service apexd-snapshotde /system/bin/luma-apexd-container --snapshotde
    override
    disabled
    oneshot
    user root
    group system
"""
        )


def append_vold_diagnostic(path: Path) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            """
# C1 diagnostic only: keep the container alive if exact vold exits so its
# own log can be collected. All stock arguments, UID/GID, and profiles remain
# unchanged; only reboot_on_failure is omitted. This is not a boot-success
# adaptation and must be removed after the causal storage error is resolved.
service vold /system/bin/vold \\
        --blkid_context=u:r:blkid:s0 --blkid_untrusted_context=u:r:blkid_untrusted:s0 \\
        --fsck_context=u:r:fsck:s0 --fsck_untrusted_context=u:r:fsck_untrusted:s0
    override
    class core
    ioprio be 2
    task_profiles ProcessCapacityHigh
    shutdown critical
    group root reserved_disk
    user root
"""
        )


def binder_major() -> int:
    for line in Path("/proc/devices").read_text().splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1] == "binder":
            return int(fields[0])
    die("Binder character-device major is absent")


def mount_entry(source: str, target: str, kind: str = "none", options: str = "bind,ro,create=dir 0 0") -> str:
    return f"lxc.mount.entry = {source} {target} {kind} {options}"


def read_integer(path: Path) -> int:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError) as error:
        die(f"cannot read integer from {path}: {error}")


def main() -> int:
    if os.geteuid() != 0:
        die("run as root on the Fairphone")
    model = Path("/sys/firmware/devicetree/base/model").read_bytes().rstrip(b"\0").decode()
    if model != "The Fairphone (Gen. 6)":
        die("device identity mismatch")
    if os.uname().release != "7.1.2-luma-fp-ims1":
        die("the accepted ims1-compatible kernel release is not running")
    if "androidboot.slot_suffix=_b" not in Path("/proc/cmdline").read_text().split():
        die("device is not running slot b")
    config = subprocess.run(
        ("zcat", "/proc/config.gz"), check=True, text=True, stdout=subprocess.PIPE
    ).stdout
    if "CONFIG_ANDROID_BINDERFS=y\n" not in config:
        die("the accepted BinderFS compatibility candidate is not running")
    host_mmap_rnd_bits = read_integer(Path("/proc/sys/vm/mmap_rnd_bits"))
    if host_mmap_rnd_bits < 1:
        die(f"invalid host mmap_rnd_bits value: {host_mmap_rnd_bits}")
    if STATE.exists() or LXC_DIR.exists():
        die("refuse to overwrite existing container state")
    if subprocess.run(("lxc-info", "-P", "/var/lib/lxc", "-n", NAME), check=False).returncode == 0:
        die("container name is already registered")
    for service in ("ModemManager", "luma-fp6-cellular-link", "luma-fp6-imsd"):
        if subprocess.run(("systemctl", "is-active", "--quiet", service), check=False).returncode:
            die(f"Luma owner is unexpectedly inactive: {service}")
    for partition, lower in PARTITIONS.items():
        assert_read_only_mount(lower)
        image = Path(f"/var/tmp/luma-stock-qrel1695-images/{partition}_a.img")
        require_file(image, PARTITION_IMAGE_HASHES[partition])
    require_file(APEX_INVENTORY, APEX_INVENTORY_SHA256)
    for name, expected in MANAGERS.items():
        require_file(MANAGER_ROOT / name, expected)
    for name, expected in BINDER_BOUNDARY.items():
        require_file(BINDER_BOUNDARY_ROOT / name, expected)
    require_file(HOOK_INPUT, HOOK_SHA256)
    require_file(CONTAINER_INIT, CONTAINER_INIT_SHA256)

    with APEX_INVENTORY.open(encoding="utf-8") as stream:
        apex_inventory = json.load(stream)
    if len(apex_inventory) != 41:
        die(f"APEX count differs: {len(apex_inventory)}")
    for entry in apex_inventory:
        source = Path(entry["mountpoint"])
        expected = APEX_ROOT / "mounts" / f'{entry["name"]}@{entry["version"]}'
        if source != expected:
            die(f"APEX mountpoint differs: {entry['name']}")
        assert_read_only_mount(source)

    (STATE / "overlays").mkdir(parents=True, mode=0o700)
    (STATE / "mounts").mkdir(mode=0o700)
    (STATE / "state").mkdir(mode=0o700)
    (STATE / "host").mkdir(mode=0o700)
    LXC_DIR.mkdir(parents=True, mode=0o700)
    added_mounts: list[Path] = []
    manifest: dict[str, object] = {
        "version": 1,
        "name": NAME,
        "kernel_release": os.uname().release,
        "partition_image_sha256": PARTITION_IMAGE_HASHES,
        "apex_inventory_sha256": APEX_INVENTORY_SHA256,
        "manager_sha256": MANAGERS,
        "binder_boundary_sha256": BINDER_BOUNDARY,
        "binder_transaction_sid_requested": False,
        "binder_boundary_scope": "hardware-free C1 container only",
        "hook_sha256": HOOK_SHA256,
        "stock_init_sha256": STOCK_INIT_SHA256,
        "container_init_sha256": CONTAINER_INIT_SHA256,
        "host_mmap_rnd_bits_observed": host_mmap_rnd_bits,
        "host_mmap_rnd_bits_modified": False,
        "container_private_mmap_rnd_bits": True,
        "started": False,
        "modem_exposed": False,
        "audio_exposed": False,
        "gpu_exposed": False,
        "input_exposed": False,
        "loop_or_block_exposed": False,
    }
    try:
        overlay_mounts: dict[str, Path] = {}
        blocked: list[dict[str, str]] = []
        discovered_execs: set[str] = set()
        for partition, lower in PARTITIONS.items():
            overlay = STATE / "overlays" / partition
            upper = overlay / "upper"
            work = overlay / "work"
            target = STATE / "mounts" / partition
            # The upper directory supplies the overlay root inode. Android
            # services run under their stock non-root UIDs, so each partition
            # root must retain the stock world-traversable directory contract.
            upper.mkdir(parents=True, mode=0o755)
            work.mkdir(mode=0o700)
            target.mkdir(mode=0o755)
            run(
                "mount",
                "-t",
                "overlay",
                "overlay",
                "-o",
                f"lowerdir={lower},upperdir={upper},workdir={work},index=off,metacopy=off",
                str(target),
            )
            added_mounts.append(target)
            overlay_mounts[partition] = target
            services = blocked_service_definitions(lower)
            if services:
                override = init_directory(partition, target) / "zz-luma-container-c1-disable.rc"
                write_override(override, services)
                for service, executable, source in services:
                    discovered_execs.add(executable)
                    blocked.append(
                        {
                            "partition": partition,
                            "service": service,
                            "executable": executable,
                            "source_rc": str(source),
                        }
                    )
        missing = REQUIRED_BLOCKS - discovered_execs
        if missing:
            die(f"required C1 service blocks were not discovered: {sorted(missing)}")

        # The container root, /data, and /metadata are already supplied by the
        # host as isolated filesystem trees. Letting stock vold consume the
        # phone's physical fstab would make it open device-mapper and resolve
        # logical partitions, violating C1's no-block-device boundary. An
        # intentionally empty container fstab makes ReadDefaultFstab fail
        # non-fatally; stock vold then publishes its Binder service without
        # probing or managing any phone storage.
        stock_fstab = PARTITIONS["vendor"] / "etc/fstab.default"
        if not stock_fstab.is_file() or stock_fstab.is_symlink():
            die("stock vendor fstab.default is absent or linked")
        container_fstab = overlay_mounts["vendor"] / "etc/fstab.default"
        container_fstab.write_text(
            "# Generated hardware-free C1 fstab. Filesystems are host-provided.\n"
        )
        container_fstab.chmod(0o644)
        manifest["stock_vendor_fstab_sha256"] = sha256(stock_fstab)
        manifest["container_vendor_fstab_sha256"] = sha256(container_fstab)
        manifest["container_fstab_block_entries"] = 0

        rootfs = overlay_mounts["system"]
        stock_init = rootfs / "system/bin/init"
        if not stock_init.is_file() or stock_init.is_symlink() or not os.access(stock_init, os.X_OK):
            die("exact stock /system/bin/init is absent or not executable")
        require_file(stock_init, STOCK_INIT_SHA256)
        apex_adapter = rootfs / "system/bin/luma-apexd-container"
        apex_adapter.write_text(
            """#!/system/bin/sh
case "$1" in
  --bootstrap)
    exit 0
    ;;
  --daemon)
    setprop apexd.status activated
    while true; do sleep 3600; done
    ;;
  --snapshotde)
    setprop apexd.status ready
    exit 0
    ;;
  *)
    exit 64
    ;;
esac
"""
        )
        apex_adapter.chmod(0o755)
        system_override = init_directory("system", rootfs) / "zz-luma-container-c1-disable.rc"
        append_apex_adapter(system_override)
        append_vold_diagnostic(system_override)
        manifest["apex_lifecycle_adapter_sha256"] = sha256(apex_adapter)
        manifest["vold_reboot_on_failure_suppressed_for_diagnostics"] = True
        for relative in (
            "apex",
            "bootstrap-apex",
            "data",
            "dev",
            "luma-apex-host",
            "linkerconfig",
            "metadata",
            "mnt",
            "run",
            "storage",
            "tmp",
            "var",
        ):
            (rootfs / relative).mkdir(parents=True, mode=0o755, exist_ok=True)
        cache_link = rootfs / "cache"
        if not cache_link.is_symlink() or os.readlink(cache_link) != "/data/cache":
            die("stock /cache symlink differs")
        for name in ("data", "metadata", "linkerconfig", "logs"):
            directory = STATE / "state" / name
            directory.mkdir(mode=0o700)
        linkerconfig_shared = STATE / "state/linkerconfig-shared"
        linkerconfig_shared.mkdir(mode=0o700)
        for name in ("bootstrap", "default"):
            (STATE / "state/linkerconfig" / name).mkdir(mode=0o755)
        apex_links = STATE / "state/apex-links"
        apex_links.mkdir(mode=0o755)
        hidden_apex_root = rootfs / "luma-apex-host"
        for entry in apex_inventory:
            name = entry["name"]
            version = entry["version"]
            versioned = f"{name}@{version}"
            (hidden_apex_root / versioned).mkdir(mode=0o755)
            # libapexutil deliberately accepts only DT_DIR entries in /apex;
            # symlink aliases make every package invisible to linkerconfig.
            # LXC bind-mounts the exact read-only payload onto both stock path
            # forms after mounting this stable directory at /apex.
            (apex_links / versioned).mkdir(mode=0o755)
            (apex_links / name).mkdir(mode=0o755)
        apex_info_list = apex_links / "apex-info-list.xml"
        manifest["apex_info_list_sha256"] = write_apex_info_list(
            apex_info_list, apex_inventory
        )
        manifest["apex_info_list_source"] = (
            "exact hash-locked APEX manifests and source partitions"
        )
        manifest["apex_container_topology"] = (
            "read-only-direct-package-binds-over-stable-directory-tree"
        )
        manifest["linkerconfig_container_topology"] = "shared-bootstrap-default-for-single-apex-set"
        (STATE / "state/data/cache").mkdir(mode=0o700)
        kptr_restrict = STATE / "state/proc-kptr-restrict"
        kptr_restrict.write_text("2\n")
        kptr_restrict.chmod(0o600)
        # Android init treats inability to raise this global sysctl as fatal.
        # The FP6 host currently exposes a lower kernel-specific ceiling and
        # must remain untouched during C1. A private regular file lets init
        # complete its write/read contract without changing or misreporting
        # the Fedora host setting; both facts are recorded in the manifest.
        mmap_rnd_bits = STATE / "state/proc-mmap-rnd-bits"
        mmap_rnd_bits.write_text(f"{host_mmap_rnd_bits}\n")
        mmap_rnd_bits.chmod(0o600)
        manifest["container_private_mmap_rnd_bits_initial_sha256"] = sha256(mmap_rnd_bits)
        hook = STATE / "host/binderfs-hook.py"
        shutil.copyfile(HOOK_INPUT, hook)
        hook.chmod(0o700)
        require_file(hook, HOOK_SHA256)

        lines = [
            "# Generated FP6 stock-QREL hardware-free C1 container.",
            f"lxc.rootfs.path = dir:{rootfs}",
            "lxc.rootfs.options = ro",
            "lxc.arch = aarch64",
            f"lxc.uts.name = {NAME}",
            "lxc.autodev = 0",
            "lxc.console.path = none",
            "lxc.tty.max = 0",
            "lxc.pty.max = 8",
            "lxc.no_new_privs = 1",
            "lxc.hook.version = 1",
            f"lxc.hook.mount = {hook}",
            # QREL's system-as-root image carries the exact stock init at this
            # path. `/init` belongs to the Android boot ramdisk and is not part
            # of the immutable system partition used as this rootfs.
            "lxc.init.cmd = /system/bin/init second_stage",
            # Android creates its own uid/pid cgroups below the LXC payload
            # cgroup. The cgroup namespace keeps that writable view rooted at
            # the container subtree rather than the host hierarchy.
            "lxc.mount.auto = proc:mixed sys:ro cgroup:rw",
            "lxc.net.0.type = empty",
            "lxc.cap.keep = audit_control sys_nice wake_alarm setpcap setgid setuid sys_ptrace sys_admin block_suspend net_admin net_raw net_bind_service kill dac_override dac_read_search fsetid chown sys_resource fowner ipc_lock sys_chroot syslog",
            "lxc.environment = ANDROID_ROOT=/system",
            "lxc.environment = ANDROID_DATA=/data",
            "lxc.environment = ANDROID_STORAGE=/storage",
            "lxc.environment = ANDROID_RUNTIME_ROOT=/apex/com.android.runtime",
            "lxc.environment = ANDROID_ART_ROOT=/apex/com.android.art",
            "lxc.environment = ANDROID_I18N_ROOT=/apex/com.android.i18n",
            "lxc.environment = ANDROID_TZDATA_ROOT=/apex/com.android.tzdata",
            "lxc.environment = INIT_SECOND_STAGE=true",
            "lxc.cgroup2.devices.deny = a",
            "lxc.cgroup2.devices.allow = c 1:3 rwm",
            "lxc.cgroup2.devices.allow = c 1:5 rwm",
            "lxc.cgroup2.devices.allow = c 1:7 rwm",
            "lxc.cgroup2.devices.allow = c 1:8 rwm",
            "lxc.cgroup2.devices.allow = c 1:9 rwm",
            "lxc.cgroup2.devices.allow = c 1:11 rwm",
            "lxc.cgroup2.devices.allow = c 5:0 rwm",
            "lxc.cgroup2.devices.allow = c 5:2 rwm",
            "lxc.cgroup2.devices.allow = c 136:* rwm",
            f"lxc.cgroup2.devices.allow = c {binder_major()}:* rwm",
            mount_entry("tmpfs", "dev", "tmpfs", "nosuid,nodev,mode=755,size=16777216,create=dir 0 0"),
            mount_entry("tmpfs", "dev/socket", "tmpfs", "nosuid,nodev,mode=755,size=4194304,create=dir 0 0"),
            mount_entry("/dev/null", "dev/null", options="bind,create=file 0 0"),
            mount_entry("/dev/zero", "dev/zero", options="bind,create=file 0 0"),
            mount_entry("/dev/full", "dev/full", options="bind,create=file 0 0"),
            mount_entry("/dev/random", "dev/random", options="bind,create=file 0 0"),
            mount_entry("/dev/urandom", "dev/urandom", options="bind,create=file 0 0"),
            mount_entry("/dev/tty", "dev/tty", options="bind,create=file 0 0"),
            # Preserve Android init's normal kernel logger. This is a write-only
            # diagnostic surface for the hardware-free construction gate, not
            # a physical device class, and lets fatal second-stage errors land
            # in the run-bounded host dmesg capture.
            mount_entry("/dev/kmsg", "dev/kmsg", options="bind,create=file 0 0"),
            mount_entry("/dev/null", "dev/console", options="bind,create=file 0 0"),
            mount_entry("none", "dev/pts", "devpts", "newinstance,ptmxmode=0666,mode=0620,gid=5,create=dir 0 0"),
            mount_entry("binder", "dev/binderfs", "binder", "max=3,create=dir 0 0"),
            # Android init creates a second mount namespace during boot. Nested
            # LXC bind mounts directly below /apex remain in mountinfo but lose
            # pathname reachability across that transition. Bind a stable link
            # directory at /apex and place the exact payload mounts at a private
            # top-level path instead.
            mount_entry(str(apex_links), "apex", options="bind,ro,create=dir 0 0"),
            # Stock SetupMountNamespaces() binds /bootstrap-apex over /apex in
            # its bootstrap namespace. Give both names the same immutable,
            # AVB-verified set so Android can retain its authentic namespace
            # bookkeeping without hiding the container's APEX inputs.
            mount_entry(str(apex_links), "bootstrap-apex", options="bind,ro,create=dir 0 0"),
            mount_entry("tmpfs", "mnt", "tmpfs", "nosuid,nodev,mode=755,size=16777216,create=dir 0 0"),
            mount_entry("tmpfs", "storage", "tmpfs", "nosuid,nodev,mode=755,size=4194304,create=dir 0 0"),
            mount_entry("tmpfs", "tmp", "tmpfs", "nosuid,nodev,mode=1777,size=67108864,create=dir 0 0"),
            mount_entry("tmpfs", "run", "tmpfs", "nosuid,nodev,mode=755,size=16777216,create=dir 0 0"),
            mount_entry("tmpfs", "var", "tmpfs", "nosuid,nodev,mode=755,size=16777216,create=dir 0 0"),
            # Stock init treats this hardening write as fatal. Give it a
            # container-private regular file rather than permission to mutate
            # the host kernel's global sysctl.
            mount_entry(str(kptr_restrict), "proc/sys/kernel/kptr_restrict", options="bind,create=file 0 0"),
            mount_entry(str(mmap_rnd_bits), "proc/sys/vm/mmap_rnd_bits", options="bind,create=file 0 0"),
        ]
        for partition in ("vendor", "system_ext", "product", "odm"):
            lines.append(mount_entry(str(overlay_mounts[partition]), partition))
        for name in ("data", "metadata", "linkerconfig"):
            lines.append(mount_entry(str(STATE / "state" / name), name, options="bind,create=dir 0 0"))
        # SetupMountNamespaces records one authentic namespace for C1's one
        # immutable APEX set. Linkerconfig still names its generated views
        # `bootstrap` and `default`; bind both names to the same writable
        # directory so enter_default_mount_ns mounts the graph it just built.
        lines.append(mount_entry(str(linkerconfig_shared), "linkerconfig/bootstrap", options="bind,create=dir 0 0"))
        lines.append(mount_entry(str(linkerconfig_shared), "linkerconfig/default", options="bind,create=dir 0 0"))
        lines.extend(
            (
                mount_entry(str(CONTAINER_INIT), "system/bin/init", options="bind,ro,create=file 0 0"),
                mount_entry(str(MANAGER_ROOT / "servicemanager"), "system/bin/servicemanager", options="bind,ro,create=file 0 0"),
                mount_entry(str(MANAGER_ROOT / "hwservicemanager"), "system_ext/bin/hwservicemanager", options="bind,ro,create=file 0 0"),
                mount_entry(str(MANAGER_ROOT / "vndservicemanager"), "vendor/bin/vndservicemanager", options="bind,ro,create=file 0 0"),
                # QREL's context managers and local Binder objects request
                # transaction security SIDs. Fedora has no Android SELinux
                # policy, so the kernel returns EOPNOTSUPP before transactions
                # can reach the otherwise isolated registries. This exact
                # derived library suppresses those two requests and is visible
                # solely inside this private BinderFS C1.
                mount_entry(str(BINDER_BOUNDARY_ROOT / "libbinder.so"), "system/lib64/libbinder.so", options="bind,ro,create=file 0 0"),
                mount_entry(str(BINDER_BOUNDARY_ROOT / "libhidlbase.so"), "system/lib64/libhidlbase.so", options="bind,ro,create=file 0 0"),
            )
        )
        for entry in sorted(apex_inventory, key=lambda item: item["name"]):
            source = entry["mountpoint"]
            name = entry["name"]
            version = entry["version"]
            lines.append(mount_entry(source, f"luma-apex-host/{name}@{version}"))
            lines.append(mount_entry(source, f"apex/{name}@{version}"))
            lines.append(mount_entry(source, f"apex/{name}"))
            if name == "com.android.vndk.v34":
                derived = str(BINDER_BOUNDARY_ROOT / "libbinder-vndk34.so")
                lines.append(
                    mount_entry(
                        derived,
                        f"apex/{name}@{version}/lib64/libbinder.so",
                        options="bind,ro,create=file 0 0",
                    )
                )
                lines.append(
                    mount_entry(
                        derived,
                        f"apex/{name}/lib64/libbinder.so",
                        options="bind,ro,create=file 0 0",
                    )
                )
        config_path = LXC_DIR / "config"
        config_path.write_text("\n".join(lines) + "\n")
        config_path.chmod(0o600)

        manifest["blocked_services"] = blocked
        manifest["config_sha256"] = sha256(config_path)
        manifest["private_binder_devices"] = ["binder", "hwbinder", "vndbinder"]
        manifest["host_character_devices"] = [
            "/dev/null",
            "/dev/zero",
            "/dev/full",
            "/dev/random",
            "/dev/urandom",
            "/dev/tty",
            "/dev/kmsg",
        ]
        manifest_path = STATE / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        manifest_path.chmod(0o600)
    except BaseException:
        for target in reversed(added_mounts):
            subprocess.run(("umount", str(target)), check=False)
        shutil.rmtree(LXC_DIR, ignore_errors=True)
        shutil.rmtree(STATE, ignore_errors=True)
        raise

    print("STOCK_ANDROID_C1_COMPOSED=true")
    print("CONTAINER_STARTED=false")
    print(f"BLOCKED_HARDWARE_SERVICES={len(manifest['blocked_services'])}")
    print("PRIVATE_BINDER_DEVICE_COUNT=3")
    print("PHYSICAL_DEVICE_CLASSES_EXPOSED=0")
    print(f"MANIFEST_SHA256={sha256(STATE / 'manifest.json')}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
