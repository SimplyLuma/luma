#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Android GPU admission of the Fairphone 6 kernel.

luma-android-runtime admits a running FP6 kernel for GPU-rendered Android only
when the sha256 of /sys/kernel/notes is listed in
/usr/share/luma/android/hardware/fairphone-fp6-kernel-notes.d/. That list is
not edited by hand: kernel-modules-luma-fp6 ships one entry, written here at
kernel build time, for the kernel it belongs to.

devices/fairphone/fp6/device.toml [kernel.android_gpu] is the record:

  required_patches      GPU patches every admitted kernel must carry
  rejected_patches      patches no admitted kernel may carry
  qualified_gpu_inputs  GPU inputs (tag + every patch touching drivers/gpu/)
                        that passed the physical qualification; "" = none
  qualified_notes_sha256  the kernel that qualification ran on (provenance)
  kernel_inputs         digest of every [kernel] input, stamped here
  admitted              whether a build of these inputs ships an entry

A rebuild that leaves the GPU inputs alone (new sensor patch, config
fragment) stays admitted; one that changes them falls out until it is
qualified again. CI (check) fails whenever the kernel inputs change and the
record was not restamped, so the outcome is always a reviewed line in the diff.

  fp6-android-gpu-admission.py check
  fp6-android-gpu-admission.py stamp
  fp6-android-gpu-admission.py qualify --manifest KERNEL_OUT/manifest.env
  fp6-android-gpu-admission.py emit --source DIR --vmlinux FILE --output FILE
  fp6-android-gpu-admission.py notes VMLINUX
  fp6-android-gpu-admission.py inputs
"""
from __future__ import annotations

import argparse
import hashlib
import re
import struct
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEVICE_TOML = ROOT / "devices" / "fairphone" / "fp6" / "device.toml"
SHA256 = re.compile(r"[0-9a-f]{64}")
GPU_PATH = re.compile(r"^(?:\+\+\+ b|--- a)/(drivers/gpu/\S+)", re.MULTILINE)


class AdmissionError(Exception):
    pass


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def touches_gpu(patch: Path) -> bool:
    return bool(GPU_PATH.search(patch.read_text(encoding="utf-8", errors="replace")))


def gpu_inputs(kernel: dict, root: Path = ROOT) -> str:
    lines = [f"tag {kernel['tag']}"]
    for patch in kernel.get("patches", []):
        if touches_gpu(root / patch):
            lines.append(f"patch {patch} {file_sha256(root / patch)}")
    return hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()


def kernel_inputs(kernel: dict, root: Path = ROOT) -> str:
    lines = [f"{key} {kernel.get(key, '')}" for key in ("tag", "release", "config", "dtb", "dtb_overlays")]
    for fragment in kernel.get("config_fragments", []):
        lines.append(f"fragment {fragment} {file_sha256(root / fragment)}")
    for patch in kernel.get("patches", []):
        lines.append(f"patch {patch} {file_sha256(root / patch)}")
    return hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()


def load(device_toml: Path = DEVICE_TOML) -> tuple[dict, dict]:
    kernel = tomllib.loads(device_toml.read_text())["kernel"]
    record = kernel.get("android_gpu")
    if not isinstance(record, dict):
        raise AdmissionError(f"{device_toml}: [kernel.android_gpu] is missing")
    return kernel, record


def expected(kernel: dict, record: dict, root: Path = ROOT) -> tuple[str, bool, list[str]]:
    """Return (kernel_inputs, admitted, reasons it is not admitted)."""
    patches = kernel.get("patches", [])
    reasons = []
    for patch in record.get("required_patches", []):
        if not (root / patch).is_file():
            raise AdmissionError(f"required patch {patch} is not a repository file")
        if not touches_gpu(root / patch):
            raise AdmissionError(f"required patch {patch} does not touch drivers/gpu/")
        if patch not in patches:
            reasons.append(f"lacks {Path(patch).name}")
    for patch in record.get("rejected_patches", []):
        if not (root / patch).is_file():
            raise AdmissionError(f"rejected patch {patch} is not a repository file")
        if patch in patches:
            reasons.append(f"carries rejected {Path(patch).name}")
    qualified = record.get("qualified_gpu_inputs", "")
    if qualified and not SHA256.fullmatch(qualified):
        raise AdmissionError("qualified_gpu_inputs must be empty or a sha256")
    if not qualified:
        reasons.append("no kernel has passed the physical qualification")
    elif qualified != gpu_inputs(kernel, root):
        reasons.append("GPU inputs changed since the physical qualification")
    return kernel_inputs(kernel, root), not reasons, reasons


def check(device_toml: Path = DEVICE_TOML, root: Path = ROOT) -> list[str]:
    kernel, record = load(device_toml)
    inputs, admitted, _ = expected(kernel, record, root)
    errors = []
    if record.get("kernel_inputs") != inputs:
        errors.append(
            "the FP6 kernel inputs changed without a matching Android GPU admission record; "
            "run scripts/kernel/fp6-android-gpu-admission.py stamp and commit the result"
        )
    elif record.get("admitted") is not admitted:
        errors.append(f"[kernel.android_gpu] admitted must be {str(admitted).lower()}; run stamp")
    return errors


def replace_value(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{key} = .*$", re.MULTILINE)
    if len(pattern.findall(text)) != 1:
        raise AdmissionError(f"device.toml must hold exactly one '{key} =' line")
    return pattern.sub(lambda _: f"{key} = {value}", text)


def stamp(device_toml: Path = DEVICE_TOML, root: Path = ROOT) -> tuple[bool, list[str]]:
    kernel, record = load(device_toml)
    inputs, admitted, reasons = expected(kernel, record, root)
    text = device_toml.read_text()
    text = replace_value(text, "kernel_inputs", f'"{inputs}"')
    text = replace_value(text, "admitted", str(admitted).lower())
    device_toml.write_text(text)
    return admitted, reasons


def read_manifest(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def qualify(manifest: Path, device_toml: Path = DEVICE_TOML, root: Path = ROOT) -> tuple[bool, list[str]]:
    kernel, _ = load(device_toml)
    values = read_manifest(manifest)
    if values.get("KERNEL_INPUTS") != kernel_inputs(kernel, root):
        raise AdmissionError(f"{manifest} was not built from this tree's [kernel] inputs")
    notes = values.get("KERNEL_NOTES_SHA256", "")
    if not SHA256.fullmatch(notes) or values.get("GPU_INPUTS") != gpu_inputs(kernel, root):
        raise AdmissionError(f"{manifest} lacks KERNEL_NOTES_SHA256/GPU_INPUTS of this tree")
    text = device_toml.read_text()
    text = replace_value(text, "qualified_gpu_inputs", f'"{values["GPU_INPUTS"]}"')
    text = replace_value(text, "qualified_notes_sha256", f'"{notes}"')
    device_toml.write_text(text)
    return stamp(device_toml, root)


def elf_section(path: Path, name: bytes) -> bytes:
    """Contents of one section of an ELF64 little-endian file (vmlinux)."""
    with path.open("rb") as stream:
        header = stream.read(64)
        if header[:4] != b"\x7fELF" or header[4] != 2 or header[5] != 1:
            raise AdmissionError(f"{path} is not a little-endian ELF64 file")
        shoff, = struct.unpack_from("<Q", header, 0x28)
        shentsize, shnum, shstrndx = struct.unpack_from("<HHH", header, 0x3A)

        def section(index: int) -> tuple[int, int, int]:
            stream.seek(shoff + index * shentsize)
            entry = stream.read(64)
            name_offset, = struct.unpack_from("<I", entry, 0)
            offset, size = struct.unpack_from("<QQ", entry, 0x18)
            return name_offset, offset, size

        _, strtab_offset, strtab_size = section(shstrndx)
        stream.seek(strtab_offset)
        strtab = stream.read(strtab_size)
        for index in range(shnum):
            name_offset, offset, size = section(index)
            if strtab[name_offset:strtab.index(b"\0", name_offset)] == name:
                stream.seek(offset)
                return stream.read(size)
    raise AdmissionError(f"{path} has no {name.decode()} section")


def kernel_notes_sha256(vmlinux: Path) -> str:
    """sha256 of /sys/kernel/notes: the kernel's .notes section."""
    return hashlib.sha256(elf_section(vmlinux, b".notes")).hexdigest()


def carries(source: Path, patch: Path) -> bool:
    return subprocess.run(
        ["patch", "-d", str(source), "-p1", "-R", "-f", "-s", "--dry-run", "-i", str(patch)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0


def emit(source: Path, vmlinux: Path, output: Path,
         device_toml: Path = DEVICE_TOML, root: Path = ROOT) -> tuple[bool, str]:
    errors = check(device_toml, root)
    if errors:
        raise AdmissionError("; ".join(errors))
    kernel, record = load(device_toml)
    _, admitted, reasons = expected(kernel, record, root)
    notes = kernel_notes_sha256(vmlinux)
    lines = [
        "# Fairphone 6 kernel admitted for GPU-rendered Android (luma-android-runtime).",
        f"# Written by scripts/kernel/fp6-android-gpu-admission.py for {kernel['release']},",
        f"# kernel inputs {record['kernel_inputs']}.",
    ]
    if admitted:
        for patch in record.get("required_patches", []):
            if not carries(source, root / patch):
                raise AdmissionError(f"{source} does not carry required {patch}")
        for patch in record.get("rejected_patches", []):
            if carries(source, root / patch):
                raise AdmissionError(f"{source} carries rejected {patch}")
        lines.append(f"{notes}  {kernel['release']}")
    else:
        lines.append(f"# Not admitted ({'; '.join(reasons)}); kernel notes {notes}.")
    output.write_text("\n".join(lines) + "\n")
    return admitted, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="fail when the record does not match the kernel inputs")
    commands.add_parser("stamp", help="record the current kernel inputs and the admission outcome")
    qualify_parser = commands.add_parser("qualify", help="record a physically qualified kernel build")
    qualify_parser.add_argument("--manifest", type=Path, required=True)
    emit_parser = commands.add_parser("emit", help="write the admission entry of a built kernel")
    emit_parser.add_argument("--source", type=Path, required=True)
    emit_parser.add_argument("--vmlinux", type=Path, required=True)
    emit_parser.add_argument("--output", type=Path, required=True)
    notes_parser = commands.add_parser("notes", help="print the /sys/kernel/notes sha256 of a vmlinux")
    notes_parser.add_argument("vmlinux", type=Path)
    commands.add_parser("inputs", help="print KERNEL_INPUTS and GPU_INPUTS of this tree")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            errors = check()
            for error in errors:
                print(f"fp6-android-gpu-admission: {error}", file=sys.stderr)
            return 1 if errors else 0
        if args.command == "inputs":
            kernel, _ = load()
            print(f"KERNEL_INPUTS={kernel_inputs(kernel)}\nGPU_INPUTS={gpu_inputs(kernel)}")
            return 0
        if args.command == "notes":
            print(kernel_notes_sha256(args.vmlinux))
            return 0
        if args.command == "emit":
            admitted, notes = emit(args.source, args.vmlinux, args.output)
            print(f"ANDROID_GPU_ADMITTED={str(admitted).lower()}\nKERNEL_NOTES_SHA256={notes}")
            return 0
        if args.command == "stamp":
            admitted, reasons = stamp()
        else:
            admitted, reasons = qualify(args.manifest)
        print("Android GPU: " + ("admitted" if admitted else "not admitted (" + "; ".join(reasons) + ")"))
        return 0
    except (AdmissionError, OSError, KeyError, tomllib.TOMLDecodeError) as error:
        print(f"fp6-android-gpu-admission: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
