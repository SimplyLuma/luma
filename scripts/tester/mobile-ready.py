#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Publish VM readiness after native Phosh presents Home on a real output."""
import json
import os
from pathlib import Path
import subprocess
import time


runtime = Path(os.environ['XDG_RUNTIME_DIR']) / 'luma-tester'
runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
ready = runtime / 'ready.json'
ready.unlink(missing_ok=True)
boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
deadline = time.monotonic() + 180
while time.monotonic() < deadline:
    try:
        if not Path('/run/luma-display/home-ready').is_file():
            raise RuntimeError('Native Home has not presented yet.')
        subprocess.run(['pgrep', '-u', str(os.getuid()), '-f',
                        '^/opt/luma/phosh/libexec/phosh( |$)'],
                       check=True, stdout=subprocess.DEVNULL, timeout=5)
        outputs = json.loads(subprocess.check_output(['wlr-randr', '--json'], timeout=5))
        enabled = [output for output in outputs if output.get('enabled')]
        if len(enabled) != 1:
            raise RuntimeError('The simulator needs one active output.')
        output = enabled[0]
        mode = next(mode for mode in output['modes'] if mode.get('current'))
        width, height = mode['width'], mode['height']
        if output.get('transform') in ('90', '270', 'flipped-90', 'flipped-270'):
            width, height = height, width
        scale = output.get('scale', 1)
        width, height = round(width / scale), round(height / scale)
        if not 0 < width < height <= 4096:
            raise RuntimeError('Native phone output is not in portrait.')
        temporary = ready.with_suffix('.new')
        temporary.write_text(json.dumps(dict(target='mobile', renderer='luma-phosh',
            boot_id=boot, width=width, height=height, output=output['name'], scale=scale)) + '\n')
        temporary.replace(ready)
        break
    except (OSError, ValueError, KeyError, StopIteration, RuntimeError, subprocess.SubprocessError):
        time.sleep(1)
else:
    raise SystemExit('Native Phosh did not present a portrait Home output within 180 seconds.')
