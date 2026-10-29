#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compile the production clipboard helper with Android's actual Bionic/libc++.

Both supported CPU targets are checked. This is not a link, Android runtime,
HwBinder IPC or hardware qualification. Use the independently verified source
tree and its pinned Clang; no host libc/C++ headers may enter this check.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--output", type=Path, default=os.environ.get("DEV_ARTIFACT_DIR"))
    args = parser.parse_args()
    if args.output is None:
        parser.error("--output or DEV_ARTIFACT_DIR is required")
    source = args.source_root.resolve()
    repo = Path(__file__).resolve().parents[2]
    compiler = source / "prebuilts/clang/host/linux-x86/clang-r450784d/bin/clang++"
    resource = Path(subprocess.check_output([str(compiler), "-print-resource-dir"], text=True).strip()) / "include"
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    unit = args.output / "clipboard.cpp"
    unit.write_text('''#include "luma-clipboard-transfer.h"
void compile_clipboard() {
    luma::clipboard::Transfers transfers;
    transfers.read(-1, [](bool, std::string) {});
    transfers.write(-1, std::make_shared<const std::string>("synthetic"));
    transfers.cancelRead();
    transfers.shutdown();
    luma::clipboard::Cache cache;
    auto generation = cache.beginRead();
    cache.complete(generation, true, "synthetic");
    cache.set(cache.get());
}
''')
    results = []
    for target, kernel_arch, machine in (("aarch64-linux-android33", "arm64", 183),
                                         ("x86_64-linux-android33", "x86", 62)):
        headers = [source / "external/libcxx/include",
                   source / "bionic/libc/include",
                   source / ("bionic/libc/kernel/uapi/asm-" + kernel_arch),
                   source / "bionic/libc/kernel/uapi",
                   source / "bionic/libc/kernel/android/uapi", resource]
        if not all(path.is_dir() for path in headers):
            raise RuntimeError("Pinned Android header tree is incomplete")
        obj = args.output / (target + ".o")
        command = [str(compiler), "--target=" + target, "-std=c++17", "-O1",
                   "-Wall", "-Wextra", "-Werror", "-fPIC", "-pthread",
                   "-fno-exceptions", "-fno-rtti", "-nostdinc", "-nostdinc++"]
        for header in headers:
            command += ["-isystem", str(header)]
        command += ["-I" + str(repo / "patches/android-hardware-waydroid/native"),
                    "-c", str(unit), "-o", str(obj)]
        subprocess.run(command, check=True, timeout=60)
        body = obj.read_bytes()
        if body[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", body, 18)[0] != machine:
            raise RuntimeError("Compiler did not produce the requested 64-bit ELF target")
        results.append({"target": target, "elf_machine": machine,
                        "object_sha256": hashlib.sha256(body).hexdigest(),
                        "command": command})
    report = {"qualification": "compile-only production helper; no link/runtime/IPC/hardware claim",
              "compiler": subprocess.check_output([str(compiler), "--version"], text=True),
              "helper_sha256": hashlib.sha256((repo / "patches/android-hardware-waydroid/native/luma-clipboard-transfer.h").read_bytes()).hexdigest(),
              "results": results}
    (args.output / "bionic-clipboard-compile.json").write_text(json.dumps(report, indent=2) + "\n")
    print("PASS: production clipboard helper compiled with pinned Android Bionic/libc++ for ARM64 and x86_64; no execution claim")


if __name__ == "__main__":
    main()
