class LumaAndroidError(Exception):
    """Expected error suitable for a user-facing diagnostic."""


class ApkValidationError(LumaAndroidError):
    """The supplied APK failed the bounded pre-install checks."""


class RuntimeUnavailableError(LumaAndroidError):
    """The configured Android runtime is unavailable or unhealthy."""
