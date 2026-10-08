# SPDX-License-Identifier: Apache-2.0
"""Isolated entry point for Monitor's additive LumaUI preview launcher."""
import os
from pathlib import Path
import sys

PREVIEW_ID = 'io.luma.Monitor.LumaUIPreview'


def make_application(arguments):
    # Set the hook before either application module resolves its identity.
    os.environ['LUMA_MONITOR_PREVIEW'] = '1'
    if '--this-machine' in arguments and not os.environ.get('LUMA_MONITOR_FIXTURE'):
        os.environ.setdefault('LUMA_MONITOR_STYLE_PATH', str(Path(__file__).resolve().parents[1] / 'data/machine.css'))
        from .machine_application import MonitorApplication
    else:
        from .application import MonitorApplication
    app = MonitorApplication()
    # Fail before registration/activation if an imported module ignored the hook.
    if app.get_application_id() != PREVIEW_ID:
        raise RuntimeError('Monitor preview resolved a non-preview application identity')
    return app


def main():
    arguments = sys.argv[1:]
    app = make_application(arguments)
    return app.run(sys.argv if '--this-machine' in arguments and not os.environ.get('LUMA_MONITOR_FIXTURE') else [])


if __name__ == '__main__':
    raise SystemExit(main())
