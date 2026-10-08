#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Preserve the trusted image clock floor at native installer boundaries.

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
    print("Anaconda native RTC initialization and signed pull preserve the installer clock floor")


def verify(root):
    here = Path(__file__).resolve().parent
    references = {"timezone.py": "anaconda-timezone-44.30.py",
                  "modules/payloads/payload/rpm_ostree/installation.py": "anaconda-ostree-installation-44.30.py"}
    for relative, (path, patch) in owners(root).items():
        expected = patch((here / "upstream" / references[relative]).read_bytes())
        if path.read_bytes() != expected:
            raise ValueError("Anaconda clock-floor patch missing from final stage 2: " + relative)
    print("Complete native clock-floor owners verified in final stage 2")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--verify":
        verify(Path(sys.argv[2]))
    elif len(sys.argv) == 2:
        prepare(Path(sys.argv[1]))
    else:
        raise SystemExit("usage: prepare-runtime-clock.py [--verify] INSTALLER_ROOT")
