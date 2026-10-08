# SPDX-License-Identifier: Apache-2.0
import contextlib
import sys

# Reconfigure stderr to flush on every newline before anything else runs —
# a D-Bus-activated service's stdio is typically not a TTY, so Python
# defaults to full block buffering there. logging.StreamHandler already
# flushes after every record it emits, but this also protects anything
# that writes to stderr directly (an uncaught traceback, a GLib/PyGObject
# critical/warning) against being lost if the process is ever killed
# abruptly (e.g. SIGKILL from a conflicting D-Bus activation) before an
# unflushed block is written out.
with contextlib.suppress(AttributeError, ValueError, OSError):
    sys.stderr.reconfigure(line_buffering=True)

import os

if os.environ.get('LUMA_TIDE_FIXTURE'):
    from .fixture_application import main
else:
    from .application import main

raise SystemExit(main())
