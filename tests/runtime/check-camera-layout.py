#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Camera layout regression on a private headless display, using the v70 fixture.

The composition check covers the actual window, dial geometry and menu anchors,
shutter/session sizes, and phone breakpoints. Reference screenshots are produced
by lumaui-conform on the server. The former output-directory/--light arguments
remain harmless for older callers; four-theme paint is checked by the gate.
"""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).with_name("check-camera-v70-fixture.py")), run_name="__main__")
