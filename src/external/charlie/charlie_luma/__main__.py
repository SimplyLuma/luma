# SPDX-License-Identifier: Apache-2.0
import sys

if sys.argv[1:2] == ["--agent"]:
    # The windowless background agent must never load GTK or the application.
    from .mail_agent import main
else:
    from .application import main

raise SystemExit(main())
