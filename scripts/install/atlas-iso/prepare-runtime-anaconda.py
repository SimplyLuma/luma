#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Prepare pinned native installer clock and initial EFI deployment boundaries.

Only Lorax's disposable installer runtime is patched. The exact Fedora 44.30
source is required; unrelated initialization and the installed OS stay upstream.
"""
import ast
import hashlib
import sys
from pathlib import Path

UPSTREAM_SHA256 = "680c0688d7185db06bc97dd31913e8bcb7acce2bb113e8e3d40d64ca86c5f030"
PULL_SHA256 = "614ac3f3061d959144e0a2e80919012c7254d44b1fab04daea35b2bef52f3f86"
RESTORE = '''def _luma_restore_clock_floor():
    # PID 1 already used this trusted image timestamp during boot. A later
    # successful RTC read or manual date must not invalidate the release key
    # during the native signed payload pull.
    try:
        floor = os.stat("/usr/lib/clock-epoch").st_mtime
    except FileNotFoundError:
        return
    if time.time() < floor:
        time.clock_settime(time.CLOCK_REALTIME, floor)
        if time.time() < floor:
            raise RuntimeError("System clock remains below the installer clock floor")
        log.info("System time advanced to installer clock floor: %s UTC",
                 time.asctime(time.gmtime(floor)))


'''


INITIAL_EFI_DEPLOY = '''def _luma_initial_efi_deploy(physroot, argv):
    # During initial deployment Anaconda has not bound /dev into the checkout
    # or copied Luma's own bootloader files yet. Auto-detection can mistake an
    # existing EFI/ubuntu/grub.cfg for ours, invoke grub2-probe too early and,
    # if it succeeds, overwrite the other system's configuration. Defer only
    # this first deployment's bootloader write; Anaconda configures our loader
    # and kernel arguments after preparing the deployment's mount targets.
    if not STORAGE.get_proxy(BOOTLOADER).IsEFI():
        return safe_exec_program("ostree", argv)

    config_args = ["config", "--repo=" + physroot + "/ostree/repo"]
    key = "sysroot.bootloader"
    rc, previous = execProgram("ostree", [*config_args, "get", key])
    if rc not in (0, 1):
        raise PayloadInstallationError("Could not read the initial OSTree bootloader policy")
    if rc == 0 and previous.strip() == "none":
        return safe_exec_program("ostree", argv)

    safe_exec_program("ostree", [*config_args, "set", key, "none"])
    try:
        safe_exec_program("ostree", argv)
    finally:
        # Restore even when deployment fails. A permanent 'none' would disable
        # bootloader updates on the installed system.
        restore = ["set", key, previous.strip()] if rc == 0 else ["unset", key]
        safe_exec_program("ostree", [*config_args, *restore])


'''


INSTALL_BOOTUPD = '''def _luma_firmware_order():
    rc, output = execProgram("efibootmgr", [])
    if rc:
        raise BootloaderInstallationError("Could not read the current EFI startup order")
    order = next((line.split(":", 1)[1].strip().split(",")
                  for line in output.splitlines() if line.startswith("BootOrder:")), None)
    if not order or any(len(item) != 4 or any(c not in "0123456789abcdefABCDEF" for c in item)
                        for item in order):
        raise BootloaderInstallationError("The current EFI startup order is unavailable")
    return [item.upper() for item in order]


def _luma_card_firmware():
    # Raspberry Pi 5: U-Boot on the SD card is the EFI firmware and saves EFI
    # variables to the card (ubootefi.var). A persisted Luma entry would start
    # the installed shim directly, skipping the card's menu, which is what
    # starts the installed system on this board.
    try:
        with open("/proc/device-tree/compatible", "rb") as handle:
            compatible = handle.read().split(b"\\0")
    except OSError:
        return False
    return any(name in (b"raspberrypi,5-model-b", b"raspberrypi,5-model-b-d0") for name in compatible)


def _luma_install_bootupd(sysroot, argv, keep_boot_order):
    if _luma_card_firmware():
        log.info("Card-booted firmware: leaving EFI boot entries unchanged")
        return execWithRedirect("bootupctl", [a for a in argv if a != "--update-firmware"], root=sysroot)
    # A named Luma entry avoids shim's generic fallback, which can rebuild and
    # reorder every vendor's entries on first boot. Register our entry normally,
    # then retain all existing priorities and append any newly created entry.
    preserve = keep_boot_order and STORAGE.get_proxy(BOOTLOADER).IsEFI()
    previous = _luma_firmware_order() if preserve else None
    if preserve and "--update-firmware" not in argv:
        argv = [*argv, "--update-firmware"]
    try:
        return execWithRedirect("bootupctl", argv, root=sysroot)
    finally:
        if previous is not None:
            current = _luma_firmware_order()
            order = [entry for entry in previous if entry in current]
            order.extend(entry for entry in current if entry not in order)
            safe_exec_program("efibootmgr", ["--bootorder", ",".join(order)])


'''


def patched_source(data):
    if hashlib.sha256(data).hexdigest() != UPSTREAM_SHA256:
        raise ValueError("unsupported Anaconda RTC initialization source")
    text = data.decode()
    text = text.replace("import datetime\n", "import datetime\nimport os\n", 1)
    text = text.replace("def time_initialize(timezone_proxy):", RESTORE + "def time_initialize(timezone_proxy):", 1)
    before = "    execWithRedirect(cmd, args)\n"
    if text.count(before) != 1:
        raise ValueError("unsupported Anaconda hardware-clock call")
    text = text.replace(before, "    try:\n        execWithRedirect(cmd, args)\n    finally:\n        _luma_restore_clock_floor()\n", 1)
    ast.parse(text)
    return text.encode()


def patched_pull_source(data):
    if hashlib.sha256(data).hexdigest() != PULL_SHA256:
        raise ValueError("unsupported Anaconda signed-pull source")
    text = data.decode()
    before = "from pyanaconda.modules.common.task import Task, sync_run_task\n"
    if text.count(before) != 1:
        raise ValueError("unsupported Anaconda signed-pull imports")
    text = text.replace(before, before + "from pyanaconda.timezone import _luma_restore_clock_floor\n", 1)
    before = "        try:\n            repo.pull_with_options(self._data.remote,\n"
    if text.count(before) != 1:
        raise ValueError("unsupported Anaconda signed-pull boundary")
    text = text.replace(before, "        try:\n            _luma_restore_clock_floor()\n            repo.pull_with_options(self._data.remote,\n", 1)
    before = "class DeployOSTreeTask(Task):\n"
    if text.count(before) != 1:
        raise ValueError("unsupported Anaconda initial deployment owner")
    text = text.replace(before, INITIAL_EFI_DEPLOY + INSTALL_BOOTUPD + before, 1)
    before = '''            log.info("ostree admin deploy starting")
            safe_exec_program(
                "ostree",
'''
    if text.count(before) != 1:
        raise ValueError("unsupported Anaconda initial deployment boundary")
    text = text.replace(before, '''            log.info("ostree admin deploy starting")
            _luma_initial_efi_deploy(
                self._physroot,
''', 1)
    before = '''        rc = execWithRedirect(
            "bootupctl",
'''
    if text.count(before) != 1:
        raise ValueError("unsupported Anaconda bootupd installation boundary")
    text = text.replace(before, '''        rc = _luma_install_bootupd(
            self._sysroot,
''', 1)
    before = '''            root=self._sysroot
        )

        if rc:
            raise BootloaderInstallationError('''
    if text.count(before) != 1:
        raise ValueError("unsupported Anaconda bootupd result boundary")
    text = text.replace(before, '''            bootloader.KeepBootOrder
        )

        if rc:
            raise BootloaderInstallationError(''', 1)
    ast.parse(text)
    return text.encode()


def owners(root):
    root = root.resolve()
    found = {}
    for relative, patch in (("timezone.py", patched_source),
                            ("modules/payloads/payload/rpm_ostree/installation.py", patched_pull_source)):
        matches = list(root.glob("usr/lib*/python*/site-packages/pyanaconda/" + relative))
        if len(matches) != 1 or not matches[0].resolve().is_relative_to(root):
            raise ValueError("missing or unrooted Anaconda clock owner: " + relative)
        found[relative] = (matches[0], patch)
    return found


def prepare(root):
    # Validate both pinned upstream owners before making either modification.
    patched = [(path, patch(path.read_bytes())) for path, patch in owners(root).values()]
    for path, data in patched:
        path.write_bytes(data)
    print("Anaconda native clock floor and initial EFI bootloader ownership prepared")


def verify(root):
    here = Path(__file__).resolve().parent
    references = {"timezone.py": "anaconda-timezone-44.30.py",
                  "modules/payloads/payload/rpm_ostree/installation.py": "anaconda-ostree-installation-44.30.py"}
    for relative, (path, patch) in owners(root).items():
        expected = patch((here / "upstream" / references[relative]).read_bytes())
        if path.read_bytes() != expected:
            raise ValueError("Anaconda clock-floor patch missing from final stage 2: " + relative)
    print("Complete native clock and EFI deployment owners verified in final stage 2")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--verify":
        verify(Path(sys.argv[2]))
    elif len(sys.argv) == 2:
        prepare(Path(sys.argv[1]))
    else:
        raise SystemExit("usage: prepare-runtime-anaconda.py [--verify] INSTALLER_ROOT")
