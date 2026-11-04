# SPDX-License-Identifier: Apache-2.0
import sys

if "--first-session" in sys.argv:
    # Before the Shell starts: no GTK to import (session.py).
    import logging
    logging.basicConfig(level=logging.INFO, format="luma-setup: %(message)s")
    from .session import first_session
    at = sys.argv.index("--first-session")
    raise SystemExit(first_session(sys.argv[at + 1] if at + 1 < len(sys.argv) else "settings"))

from .app import main  # noqa: E402

raise SystemExit(main())
