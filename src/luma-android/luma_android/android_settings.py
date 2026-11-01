# SPDX-License-Identifier: Apache-2.0
"""Write one Android global setting through Waydroid's platform service.

The host user already reaches IPlatform for `waydroid prop set`; its settings
calls need no root shell, so touch profiles keep NoNewPrivileges. Waydroid's
property action supplies the session and Binder setup; only its final write is
replaced. Usage: python3 -m luma_android.android_settings KEY VALUE
"""
from __future__ import annotations

import sys

SETTINGS_GLOBAL = 2  # lineageos.waydroid.Platform.SETTINGS_GLOBAL
ALLOWED = {"force_resizable_activities": {"0", "1"}}


def is_set(platform, key: str) -> bool:
    """Return whether Android holds a value for the global setting.

    An unset setting replies with a null String16, and python3-gbinder 1.3
    crashes reading one, so probe the length before settingsGetString does.
    """
    from tools.interfaces.IPlatform import TRANSACTION_settingsGetString

    request = platform.client.new_request()
    request.append_int32(SETTINGS_GLOBAL)
    request.append_string16(key)
    reply, status = platform.client.transact_sync_reply(TRANSACTION_settingsGetString, request)
    if status:
        raise RuntimeError("Android settings service did not reply")
    reader = reply.init_reader()
    _, exception = reader.read_int32()
    _, length = reader.read_int32()
    return exception == 0 and length >= 0


def put(platform, key: str, value: str) -> bool:
    """Return whether Android changed; raise if it did not accept the value."""
    # Waydroid 1.6.3 settingsGetInt sends the string transaction; read strings.
    if is_set(platform, key) and platform.settingsGetString(SETTINGS_GLOBAL, key) == value:
        return False
    platform.settingsPutString(SETTINGS_GLOBAL, key, value)
    if platform.settingsGetString(SETTINGS_GLOBAL, key) != value:
        raise RuntimeError(f"Android rejected the {key} setting")
    return True


def main(arguments: list[str] | None = None) -> int:
    key, value = arguments if arguments is not None else sys.argv[1:]
    if value not in ALLOWED.get(key, ()):
        raise SystemExit("unsupported Android setting")
    sys.path.insert(0, "/usr/lib/waydroid")
    import tools
    import tools.helpers.props
    from tools.interfaces import IPlatform

    outcome = {}

    def write(args, _key, _value):
        platform = IPlatform.get_service(args)
        if platform is None:
            raise SystemExit("Android platform service is unavailable")
        outcome["changed"] = put(platform, key, value)

    tools.helpers.props.set = write
    sys.argv = ["waydroid", "prop", "set", key, value]
    tools.main()
    # Waydroid logs, rather than raises, a stopped session.
    if "changed" not in outcome:
        raise SystemExit("Android session is not running")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
