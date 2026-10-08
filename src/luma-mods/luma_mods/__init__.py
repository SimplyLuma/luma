"""Project Luma's non-mutating Mod inspection and planning library."""

from .errors import (
    CatalogError,
    ManifestValidationError,
    ResolutionError,
    TrustError,
    StateError,
    TransactionError,
    InventoryError,
    CatalogUpdateError,
)
from .manifest import ManifestLimits, inspect_manifest
from .resolver import Catalog, HostContext, Plan, resolve_mod
from .trust import TrustPolicy, VerificationResult, verify_inspection
from .image_composition import (
    ImageCompositionLock,
    build_image_composition_lock,
    require_install_authorized,
)

__all__ = [
    "Catalog",
    "CatalogError",
    "HostContext",
    "ManifestLimits",
    "ManifestValidationError",
    "Plan",
    "ResolutionError",
    "TrustError",
    "StateError",
    "TransactionError",
    "InventoryError",
    "CatalogUpdateError",
    "TrustPolicy",
    "VerificationResult",
    "ImageCompositionLock",
    "build_image_composition_lock",
    "require_install_authorized",
    "inspect_manifest",
    "resolve_mod",
    "verify_inspection",
]
