"""Explicit local pairing bootstrap. Never invoked by a remote request."""
from pathlib import Path
import hmac
import os
import re
import ssl
import subprocess
from .transport import fingerprint
from .policy import Journal


def create_identity(directory: Path, *, days: int = 30):
    if type(days) is not int or not 1 <= days <= 398:
        raise ValueError("identity validity must be 1-398 days")
    # Refuse reuse/overwrite; recovery requires an explicit new pairing.
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    key, cert = directory / "device.key", directory / "device.pem"
    try:
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt",
            "ec_paramgen_curve:P-256", "-nodes", "-days", str(days), "-subj", "/CN=Luma Connect device",
            "-addext", "basicConstraints=critical,CA:FALSE",
            "-addext", "keyUsage=critical,digitalSignature",
            "-addext", "extendedKeyUsage=serverAuth,clientAuth",
            "-keyout", str(key), "-out", str(cert)], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        key.chmod(0o600); cert.chmod(0o600)
    except BaseException:
        # Leave the private directory for explicit recovery; never reuse partial keys.
        raise RuntimeError("identity creation failed; use a fresh private directory") from None
    return fingerprint(cert.read_text())


def approve_peer(directory: Path, peer_certificate: Path, expected_fingerprint: str,
                 epoch: str, grants, *, account=None, outgoing_grants=None):
    if directory.stat().st_mode & 0o077:
        raise PermissionError("identity directory must be private")
    pem = peer_certificate.read_text()
    if len(pem) > 16384 or pem.count("-----BEGIN CERTIFICATE-----") != 1:
        raise ValueError("one device certificate required")
    actual = fingerprint(pem)
    if not hmac.compare_digest(actual, expected_fingerprint):
        raise PermissionError("fingerprint mismatch")
    peer_dir = directory / "peers"
    peer_dir.mkdir(mode=0o700, exist_ok=True)
    if peer_dir.stat().st_mode & 0o077:
        raise PermissionError("peer directory must be private")
    target = peer_dir / (actual + ".pem")
    # Existing same-key pairing retains identical certificate; never follow symlinks.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(pem); file.flush(); os.fsync(file.fileno())
    journal = Journal(directory / "continuity.db")
    try: journal.approve(actual, epoch, grants, account=account, outgoing_grants=outgoing_grants)
    finally: journal.close()
    return actual
