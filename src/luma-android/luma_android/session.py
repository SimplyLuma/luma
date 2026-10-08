from __future__ import annotations

from .config import load_runtime_config
from .engine import WaydroidEngine
from .errors import LumaAndroidError


def main() -> int:
    config = load_runtime_config()
    engine = WaydroidEngine(config.engine)
    try:
        engine.ensure_ready(config.multi_window, timeout=180, detached=False)
    except LumaAndroidError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
