# SPDX-License-Identifier: Apache-2.0
"""Native preview entry point; pass the identity to the actual application."""
import os
from .identity import PREVIEW_APP_ID


def main(argv=None):
    if os.environ.get('LUMA_TIDE_FIXTURE'):
        from .fixture_application import main as fixture_main
        return fixture_main(argv)
    from .application import main as application_main
    return application_main(argv, application_id=PREVIEW_APP_ID)


if __name__ == '__main__':
    raise SystemExit(main())
