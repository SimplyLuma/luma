"""Typed errors for Mod inspection and resolution."""


class LumaModsError(Exception):
    """Base error suitable for a bounded user-facing explanation."""


class ManifestValidationError(LumaModsError):
    """The supplied Mod manifest is malformed or outside the contract."""


class CatalogError(LumaModsError):
    """The local catalog is ambiguous or malformed."""


class ResolutionError(LumaModsError):
    """A Mod cannot be resolved for the requested host state."""


class TrustError(LumaModsError):
    """A trust policy or cryptographic verification result is invalid."""


class StateError(LumaModsError):
    """Persistent Mod state is unsafe, malformed, stale, or inconsistent."""


class TransactionError(LumaModsError):
    """A bounded Mod lifecycle transaction cannot safely continue."""


class InventoryError(LumaModsError):
    """Read-only host inventory could not be collected safely."""


class CatalogUpdateError(LumaModsError):
    """Trusted catalog metadata or artifacts could not be refreshed safely."""
