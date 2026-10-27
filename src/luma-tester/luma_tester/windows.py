# SPDX-License-Identifier: Apache-2.0
"""Entry point for the self-contained native Windows folder."""
import os
from pathlib import Path
import sys
from .__main__ import main


def launch():
    root = Path(__file__).resolve().parents[2]
    paths = (root/'helpers/openssh', root/'helpers/qemu', root/'bin')
    os.environ['PATH'] = os.pathsep.join(map(str, paths))+os.pathsep+os.environ.get('PATH','')
    os.environ['LUMA_TESTER_FIRMWARE_DIR'] = str(root/'firmware')
    os.environ['LUMA_TESTER_QEMU_DATA_DIR'] = str(root/'share/qemu')
    if not sys.argv[1:]:sys.argv.append('gui')
    return main()


if __name__ == '__main__':raise SystemExit(launch())
