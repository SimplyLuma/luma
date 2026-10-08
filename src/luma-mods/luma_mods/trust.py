"""Read-only publisher policy and Sigstore bundle verification for Luma Mods."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .errors import TrustError
from .manifest import ID_PATTERN
from .model import Inspection

TRUST_POLICY_SCHEMA = "org.luma.mod-trust-policy/v0.1"
TRUST_LEVELS = {"luma-core", "luma-verified", "community"}
MAX_POLICY_BYTES = 256 * 1024
MAX_BUNDLE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class PublisherPolicy:
    id: str
    name: str
    level: str
    identity: str
    issuer: str


@dataclass(frozen=True)
class TrustPolicy:
    path: Path
    publishers: Mapping[str, PublisherPolicy]

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> "TrustPolicy":
        source = Path(path)
        payload = _read_regular_file(source, MAX_POLICY_BYTES, "trust policy")
        try:
            data = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_numeric_constant,
            )
        except UnicodeDecodeError as error:
            raise TrustError("trust policy must be UTF-8 JSON") from error
        except json.JSONDecodeError as error:
            raise TrustError(f"trust policy is not valid JSON: {error}") from error
        root = _mapping(data, "trust policy")
        _keys(root, {"schema", "publishers"}, "trust policy")
        if root.get("schema") != TRUST_POLICY_SCHEMA:
            raise TrustError(f"unsupported trust policy; expected {TRUST_POLICY_SCHEMA}")
        raw_publishers = root.get("publishers")
        if not isinstance(raw_publishers, list):
            raise TrustError("trust policy.publishers must be a list")
        if len(raw_publishers) > 4096:
            raise TrustError("trust policy contains too many publishers")
        publishers: dict[str, PublisherPolicy] = {}
        for index, raw in enumerate(raw_publishers):
            where = f"trust policy.publishers[{index}]"
            item = _mapping(raw, where)
            _keys(item, {"id", "name", "level", "identity", "issuer"}, where)
            values = {
                key: _text(item.get(key), f"{where}.{key}")
                for key in ("id", "name", "level", "identity", "issuer")
            }
            if values["level"] not in TRUST_LEVELS:
                raise TrustError(
                    f"{where}.level must be one of: {', '.join(sorted(TRUST_LEVELS))}"
                )
            if not ID_PATTERN.fullmatch(values["id"]):
                raise TrustError(f"{where}.id is not a valid reverse-domain identifier")
            if values["id"] in publishers:
                raise TrustError(f"trust policy repeats publisher {values['id']}")
            publishers[values["id"]] = PublisherPolicy(**values)
        return cls(path=source, publishers=publishers)


@dataclass(frozen=True)
class VerificationResult:
    level: str
    label: str
    verified: bool
    reason: str
    publisher_id: str
    signer_identity: str | None = None
    issuer: str | None = None

    def as_dict(self) -> dict[str, str | bool | None]:
        return {
            "level": self.level,
            "label": self.label,
            "verified": self.verified,
            "reason": self.reason,
            "publisher_id": self.publisher_id,
            "signer_identity": self.signer_identity,
            "issuer": self.issuer,
        }


Runner = Callable[..., Any]


def unverified(inspection: Inspection, reason: str | None = None) -> VerificationResult:
    return VerificationResult(
        level="local-unverified",
        label="Local / Unverified",
        verified=False,
        reason=reason or "No trusted publisher signature was supplied.",
        publisher_id=inspection.mod.identity.publisher.id,
    )


def verify_inspection(
    inspection: Inspection,
    policy: TrustPolicy | None,
    *,
    sigstore_executable: str = "sigstore",
    runner: Runner = subprocess.run,
) -> VerificationResult:
    """Verify the exact signing digest without downloading or changing the host."""

    publisher_id = inspection.mod.identity.publisher.id
    signatures = inspection.mod.signatures
    if not signatures:
        return unverified(inspection)
    if policy is None:
        return unverified(
            inspection,
            "A signature is present, but no protected Luma publisher policy was supplied.",
        )
    publisher = policy.publishers.get(publisher_id)
    if publisher is None:
        return unverified(
            inspection,
            f"Publisher {publisher_id} is not present in the protected Luma policy.",
        )
    if publisher.name != inspection.mod.identity.publisher.name:
        raise TrustError(
            f"publisher display name does not match protected policy for {publisher_id}"
        )
    candidates = [signature for signature in signatures if signature.signer == publisher_id]
    foreign = sorted({signature.signer for signature in signatures if signature.signer != publisher_id})
    if foreign:
        raise TrustError(
            "v0.1 signatures may only identify the manifest publisher; found "
            + ", ".join(foreign)
        )
    if len(candidates) != 1:
        raise TrustError("v0.1 requires exactly one publisher signature")
    signature = candidates[0]
    expected_manifest_digest = f"sha256:{inspection.signing_sha256}"
    if signature.manifest_digest != expected_manifest_digest:
        raise TrustError(
            "publisher signature names a different manifest signing digest"
        )
    bundle_path = inspection.path.parent / signature.bundle
    bundle = _read_regular_file(bundle_path, MAX_BUNDLE_BYTES, "Sigstore bundle")
    actual_bundle_digest = f"sha256:{hashlib.sha256(bundle).hexdigest()}"
    if actual_bundle_digest != signature.bundle_digest:
        raise TrustError("Sigstore bundle digest does not match the manifest")

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix="luma-mod-bundle-", suffix=".sigstore.json", delete=False
        ) as temporary:
            temporary.write(bundle)
            temporary.flush()
            os.fchmod(temporary.fileno(), 0o600)
            temporary_name = temporary.name
        command: Sequence[str] = (
            sigstore_executable,
            "verify",
            "identity",
            "--offline",
            "--bundle",
            temporary_name,
            "--cert-identity",
            publisher.identity,
            "--cert-oidc-issuer",
            publisher.issuer,
            expected_manifest_digest,
        )
        try:
            clean_environment = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LC_ALL": "C",
            }
            if "HOME" in os.environ:
                clean_environment["HOME"] = os.environ["HOME"]
            completed = runner(
                command,
                check=False,
                capture_output=True,
                env=clean_environment,
                text=True,
                timeout=30,
            )
        except FileNotFoundError as error:
            raise TrustError(
                "Sigstore verifier is unavailable; this Mod remains unverified"
            ) from error
        except subprocess.TimeoutExpired as error:
            raise TrustError("Sigstore verification exceeded the 30-second limit") from error
        if completed.returncode != 0:
            detail = _bounded_detail(completed.stderr or completed.stdout)
            raise TrustError(
                "Sigstore rejected the publisher bundle"
                + (f": {detail}" if detail else "")
            )
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    labels = {
        "luma-core": "Luma Core",
        "luma-verified": "Luma Verified",
        "community": "Community",
    }
    return VerificationResult(
        level=publisher.level,
        label=labels[publisher.level],
        verified=True,
        reason=(
            "The exact manifest signing digest matches a Sigstore bundle from the "
            "publisher identity allowed by Luma's protected policy."
        ),
        publisher_id=publisher_id,
        signer_identity=publisher.identity,
        issuer=publisher.issuer,
    )


def _read_regular_file(path: Path, maximum: int, description: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise TrustError(f"cannot open {description} safely: {error.strerror}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise TrustError(f"{description} must be a regular file")
        if metadata.st_size > maximum:
            raise TrustError(f"{description} is larger than the {maximum}-byte limit")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > maximum:
            raise TrustError(f"{description} is larger than the {maximum}-byte limit")
        return payload
    finally:
        os.close(descriptor)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TrustError(f"trust policy contains duplicate field: {key}")
        result[key] = value
    return result


def _reject_numeric_constant(value: str) -> None:
    raise TrustError(f"trust policy contains unsupported numeric constant: {value}")


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TrustError(f"{where} must be an object")
    return value


def _keys(value: Mapping[str, Any], expected: set[str], where: str) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise TrustError(f"{where} is missing required field(s): {', '.join(missing)}")
    if unknown:
        raise TrustError(f"{where} contains unknown field(s): {', '.join(unknown)}")


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrustError(f"{where} must be non-empty text")
    if len(value.encode("utf-8")) > 4096:
        raise TrustError(f"{where} is too long")
    return value


def _bounded_detail(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:240]
