# SPDX-License-Identifier: Apache-2.0
"""Public GUI entry point for Ari's single responsive LumaUI window."""
from .lumaui_app import AriApplication, AriWindow, main

__all__ = ["AriApplication", "AriWindow", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
