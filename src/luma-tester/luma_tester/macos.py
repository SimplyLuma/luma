# SPDX-License-Identifier: Apache-2.0
"""Finder entry point for the self-contained macOS bundle."""
import os
import json
from pathlib import Path
import sys
from .__main__ import main


def launch():
    resources=Path(__file__).resolve().parents[2]
    contents=resources.parent
    os.environ['PATH']=str(contents/'Helpers')+os.pathsep+os.environ.get('PATH','/usr/bin:/bin')
    os.environ['LUMA_TESTER_FIRMWARE_DIR']=str(resources/'firmware')
    os.environ['LUMA_TESTER_QEMU_DATA_DIR']=str(resources/'share/qemu')
    gpu=resources/'mac-gpu'
    if (gpu/'runtime.json').is_file():
        runtime=json.loads((gpu/'runtime.json').read_text())
        if runtime.get('backend') in ('angle-metal','apple-opengl') and (gpu/'Helpers/qemu-system-aarch64').is_file():
            os.environ['PATH']=str(gpu/'Helpers')+os.pathsep+os.environ['PATH']
            os.environ['LUMA_TESTER_MAC_GPU']=runtime['backend']
            if runtime['backend']=='angle-metal':os.environ['ANGLE_DEFAULT_PLATFORM']='metal'
    if not sys.argv[1:]:sys.argv.append('gui')
    return main()


if __name__=='__main__':raise SystemExit(launch())
