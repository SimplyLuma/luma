#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Apply the effective HWC series to the pinned upstream codeload archive.

No download or build-tree reset occurs. This verifies patch applicability and
vendored production header identity; it does not replace an Android image build.
"""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
REVISION = "85f31a9103b84faba24eb7cbca47750d12e64e4b"
SERIES = ["0001-prairie-native-android-window-chrome.patch",
          "0002-prairie-host-pointer-policy.patch",
          "0003-luma-appkit-content-island.patch",
          "0004-luma-surface-treatment-frame.patch",
          "0007-lumaui-current-inset-frame.patch",
          "0008-luma-desktop-window-behavior-recovery.patch",
          "0009-luma-frame-buffer-thread-ownership.patch",
          "0010-luma-authoritative-task-removal.patch",
          "0011-luma-mandatory-initial-task-resize.patch",
          "0012-luma-shared-composer-rpc-threadpool.patch",
          "0013-luma-software-buffer-allocation-bounds.patch"]


def check_linkage(work):
    # Compile the production wrapper's exact declaration/body in two separate
    # translation units. Preserve its actual anonymous/global placement.
    source = (work / "hwcomposer/wayland-hwc.cpp").read_text()
    header = (work / "hwcomposer/wayland-hwc.h").read_text()
    declaration = "void cancel_task_resize(uint32_t taskID);"
    assert declaration in header
    start = source.index("void cancel_task_resize(uint32_t taskID) {")
    end = source.index("\n}", start) + 2
    body = source[start:end]
    namespace_end = source.index("}  // namespace", source.index("namespace {"))
    global_scope = start > namespace_end
    caller = work / "task-removal-caller.cpp"
    caller.write_text("#include <cstdint>\n" + declaration +
                      "\nint main() { cancel_task_resize(32); }\n")
    queue = '#include "hwcomposer/luma-task-resize.h"\nnamespace {\n' +         'luma::TaskResizeQueue& task_resize_queue() {\n' +         ' static luma::TaskResizeQueue queue([](const luma::TaskResize&) {});\n' +         ' return queue;\n}\n'
    def link(name, external):
        provider = work / f"{name}.cpp"
        provider.write_text(queue + ("}\n" + body if external else body + "\n}\n"))
        result = subprocess.run(["g++", "-std=c++17", "-pthread", str(caller),
                                 str(provider), "-o", str(work / name)],
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        print(f"{name}: exit={result.returncode}\n{result.stdout}")
        return result
    positive = link("production-task-removal-link", global_scope)
    if positive.returncode:
        raise ValueError("Production HAL caller cannot link its cancellation wrapper")
    subprocess.run([str(work / "production-task-removal-link")], check=True)
    negative = link("anonymous-task-removal-link-negative", False)
    if negative.returncode == 0 or "undefined reference" not in negative.stdout:
        raise ValueError("Anonymous-scope linkage negative did not reject the broken HAL")
    print("Actual two-translation-unit task-removal linkage PASS; anonymous-scope control RED")


def check(archive, link_check=False):
    with tempfile.TemporaryDirectory(prefix="luma-windowing-series-") as temporary:
        work = Path(temporary)
        prefix = f"android_hardware_waydroid-{REVISION}/"
        with tarfile.open(archive) as source:
            files = [member for member in source if member.isfile()]
            if not files or not all(member.name.startswith(prefix) for member in files):
                raise ValueError("Expected codeload archive for the pinned hardware revision")
            for member in files:
                relative = Path(member.name[len(prefix):])
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Unsafe upstream archive path")
                target = work / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.extractfile(member).read())
        # Isolate git apply from any parent worktree/configuration.
        subprocess.run(["git", "init", "-q", str(work)], check=True)
        for name in SERIES:
            patch = ROOT / "patches/android-hardware-waydroid" / name
            subprocess.run(["git", "-C", str(work), "apply", "--binary", "--check", str(patch)], check=True)
            subprocess.run(["git", "-C", str(work), "apply", "--binary", str(patch)], check=True)
            print(f"applied {name}: {hashlib.sha256(patch.read_bytes()).hexdigest()}")
        for header in ("luma-window-frame.h", "luma-frame-tokens.h", "luma-frame-glyphs.h"):
            if (work / "hwcomposer" / header).read_bytes() != (ROOT / "src/luma-platform/compat" / header).read_bytes():
                raise ValueError(f"Effective HWC header differs from shared platform: {header}")
        if (work / "hwcomposer/luma-task-resize.h").read_bytes() != (ROOT / "patches/android-hardware-waydroid/native/luma-task-resize.h").read_bytes():
            raise ValueError("Effective HWC resize worker differs from tested production source")
        if (work / "hwcomposer/fonts/OFL.txt").read_bytes() != (ROOT / "patches/android-hardware-waydroid/native/Figtree-OFL.txt").read_bytes():
            raise ValueError("Effective Figtree copyright/OFL differs from pinned source")
        if (work / "hwcomposer/luma-window-callbacks.h").read_bytes() != (ROOT / "patches/android-hardware-waydroid/native/luma-window-callbacks.h").read_bytes():
            raise ValueError("Effective callback lifetime registry differs from native tested source")
        if (work / "hwcomposer/luma-task-lifetime.h").read_bytes() != (ROOT / "patches/android-hardware-waydroid/native/luma-task-lifetime.h").read_bytes():
            raise ValueError("Effective authoritative task removal differs from native tested source")
        if (work / "hwcomposer/luma-rgb-copy-bounds.h").read_bytes() != (ROOT / "patches/android-hardware-waydroid/native/luma-rgb-copy-bounds.h").read_bytes():
            raise ValueError("Effective software copy bounds differ from native tested production source")
        if link_check:
            check_linkage(work)
        print("Pinned HWC patch series, production headers and font license identity: PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--link-check", action="store_true", help="Compile actual cross-translation-unit cancellation linkage and its broken-scope control")
    args = parser.parse_args()
    print(f"upstream archive SHA256: {hashlib.sha256(args.archive.read_bytes()).hexdigest()}")
    check(args.archive, args.link_check)
