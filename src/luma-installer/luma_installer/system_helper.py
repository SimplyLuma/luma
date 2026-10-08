from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


MINIMUM_SPACE_HEADROOM = 64 * 1024 * 1024
SYSTEM_STATE_ROOT = Path("/var/lib/luma-installer")


@contextmanager
def system_transaction_lock() -> Iterator[None]:
    SYSTEM_STATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(
        SYSTEM_STATE_ROOT / "transaction.lock",
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another authenticated system package transaction is active") from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def validate_source(path: Path, expected: str) -> tuple[int, os.stat_result]:
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise ValueError("invalid SHA-256 fingerprint")
    source = path.absolute()
    descriptor = os.open(source, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid == 0:
            raise ValueError("package must be a regular file owned by the requesting user")

        # Atomic Fedora exposes home directories through /home, while their
        # canonical on-disk location is /var/home.  Validate against the file
        # owner's account home instead of hard-coding either spelling.  This
        # also prevents another local user from authorizing a package staged
        # beneath somebody else's home.
        account_home = Path(pwd.getpwuid(metadata.st_uid).pw_dir).resolve(strict=True)
        canonical_source = source.parent.resolve(strict=True) / source.name
        staging_root = account_home / ".local" / "share" / "luma" / "installer" / "staging" / expected
        if canonical_source.parent != staging_root:
            raise ValueError("package is outside its owner's fingerprinted Luma staging directory")
        return descriptor, metadata
    except Exception:
        os.close(descriptor)
        raise


def copy_and_verify(descriptor: int, suffix: str, expected: str, transaction: Path) -> Path:
    destination = transaction / f"application{suffix}"
    digest = hashlib.sha256()
    with os.fdopen(descriptor, "rb", closefd=True) as source, destination.open("xb") as target:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            target.write(chunk)
        target.flush()
        os.fsync(target.fileno())
    destination.chmod(0o600)
    if digest.hexdigest() != expected:
        raise ValueError("package changed after the user reviewed it")
    return destination


def require_transaction_space(root: Path, payload_bytes: int) -> None:
    required = payload_bytes + max(MINIMUM_SPACE_HEADROOM, payload_bytes // 10)
    available = shutil.disk_usage(root).free
    if available < required:
        raise ValueError(
            "not enough free system storage for the authenticated transaction "
            f"({available} bytes available; {required} required)"
        )


def _booted_base_is_pending() -> bool:
    """True when no other release waits for a restart, so a package change
    can also be applied to the running system (ADR-038)."""
    result = subprocess.run(["/usr/bin/rpm-ostree", "status", "--json"], check=False, capture_output=True,
                            text=True, timeout=120)
    if result.returncode != 0:
        return False
    try:
        deployments = json.loads(result.stdout).get("deployments", [])
    except ValueError:
        return False
    booted = next((item for item in deployments if item.get("booted")), None)
    staged = next((item for item in deployments if item.get("staged")), None)
    if booted is None:
        return False
    base = lambda item: item.get("base-checksum") or item.get("checksum")  # noqa: E731
    return staged is None or base(staged) == base(booted)


def apply_live(*, replacement: bool) -> bool:
    """Apply the pending package change to the running system, as ``sudo dnf
    install`` does. False leaves it for the next restart (never an error)."""
    if not _booted_base_is_pending():
        return False
    try:
        from luma_install_commands import live as live_exports  # optional: launchers appear at once
    except ImportError:
        live_exports = None
    before = live_exports.snapshot() if live_exports else None
    command = ["/usr/bin/rpm-ostree", "apply-live"] + (["--allow-replacement"] if replacement else [])
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        return False
    if live_exports is not None:
        try:
            live_exports.refresh(before)
        except Exception:
            pass
    return True


def run_backend(kind: str, package: Path) -> bool:
    """Install ``package``; True when a restart is still needed."""
    if kind == "rpm":
        command = ["/usr/bin/rpm-ostree", "install", "--idempotent", str(package)]
    elif kind == "snap":
        Path("/var/lib/snapd/snap").mkdir(mode=0o755, parents=True, exist_ok=True)
        subprocess.run(
            ["/usr/bin/systemctl", "enable", "--now", "snapd.socket"],
            check=True,
        )
        command = ["/usr/bin/snap", "install", "--dangerous", str(package)]
    else:
        raise ValueError("unsupported privileged backend")
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        message = next((line.strip() for line in reversed((result.stderr + result.stdout).splitlines())
                        if line.strip()), "system installer failed")
        raise RuntimeError(message)
    if kind == "rpm":
        return not apply_live(replacement=False)
    return False


def package_identity(kind: str, package: Path) -> str:
    if kind == "rpm":
        command = ["/usr/bin/rpm", "-qp", "--queryformat", "%{NAME}", str(package)]
        pattern = r"[A-Za-z0-9][A-Za-z0-9+._-]{0,127}"
    elif kind == "snap":
        command = ["/usr/bin/unsquashfs", "-cat", str(package), "meta/snap.yaml"]
        pattern = r"[a-z0-9][a-z0-9+.-]{0,79}"
    else:
        raise ValueError("unsupported privileged backend")
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise ValueError("could not determine the package identity")
    if kind == "snap":
        match = re.search(r"(?m)^name:\s*['\"]?([^'\"\s]+)", result.stdout)
        value = match.group(1) if match else ""
    else:
        value = result.stdout.strip()
    if not re.fullmatch(pattern, value):
        raise ValueError("package identity is invalid")
    return value


def receipt_for(expected: str) -> tuple[Path, dict[str, object]]:
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise ValueError("invalid receipt fingerprint")
    path = Path("/var/lib/luma-installer/receipts") / f"{expected}.json"
    metadata = path.lstat()
    if metadata.st_uid != 0 or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("the system receipt is not trusted")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("sha256") != expected:
        raise ValueError("the system receipt is malformed")
    return path, value


def write_receipt(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_suffix(".json.partial")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o644)
    temporary.replace(path)


def manage_backend(action: str, receipt: dict[str, object]) -> bool:
    kind = receipt.get("format")
    name = receipt.get("package_name")
    if not isinstance(name, str):
        raise ValueError("the system receipt has no package identity")
    restart_required = False
    if action == "remove" and kind == "snap":
        command = ["/usr/bin/snap", "remove", name]
    elif action == "remove" and kind == "rpm":
        if receipt.get('external_layered'):
            raise ValueError('Review this native application again before removing its current package owner.')
        command = ["/usr/bin/rpm-ostree", "uninstall", name]
        restart_required = None
    else:
        raise ValueError(f"{action} is not supported for this system package")
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        message = next((line.strip() for line in reversed((result.stderr + result.stdout).splitlines())
                        if line.strip()), "system package management failed")
        raise RuntimeError(message)
    if restart_required is None:
        restart_required = not apply_live(replacement=True)
    return bool(restart_required)


def reconcile_receipts() -> None:
    root = SYSTEM_STATE_ROOT / "receipts"
    if not root.is_dir():
        return
    for path in root.glob("*.json"):
        try:
            receipt_path, receipt = receipt_for(path.stem)
            if receipt.get("format") != "rpm" or receipt.get("state") != "removal-pending-restart":
                continue
            name = receipt.get("package_name")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._-]{0,127}", name):
                continue
            result = subprocess.run(
                ["/usr/bin/rpm", "-q", "--", name], check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
            )
            if result.returncode == 1:
                receipt_path.unlink()
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
            # An untrusted or malformed receipt is never deleted automatically.
            continue


def cleanup_abandoned_system_transactions(root: Path | None = None, owner_uid: int = 0) -> None:
    transactions = root if root is not None else SYSTEM_STATE_ROOT / "transactions"
    if not transactions.is_dir() or transactions.is_symlink():
        return
    for transaction in transactions.iterdir():
        try:
            metadata = transaction.lstat()
        except OSError:
            continue
        if (transaction.name.startswith("transaction-") and metadata.st_uid == owner_uid and
                stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)):
            shutil.rmtree(transaction, ignore_errors=True)


def _refused(message: str) -> int:
    print(f"refused: {message}", flush=True)
    return 1


def flatpak_revert(app_id: str, commit: str) -> int:
    """Go back to one earlier build of a Depot-managed system Flatpak (Depot's "Go back").

    Only root may name a commit for the system installation. The app must be a
    catalogued application installed from a Luma-managed remote, and the commit
    a full checksum; libflatpak verifies the build's signature as for any update."""
    import re
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+){2,}", app_id) or \
            not re.fullmatch(r"[0-9a-f]{64}", commit):
        return _refused("that is not an app and version Depot can go back to")
    try:
        import gi
        gi.require_version("Flatpak", "1.0")
        from gi.repository import Flatpak, GLib
        from .depot_flatpak import SourceUnavailable, revert_transaction
    except (ImportError, ValueError) as error:
        return fail(str(error))
    try:
        with system_transaction_lock():
            installation = Flatpak.Installation.new_system(None)
            ref = next((r for r in installation.list_installed_refs_by_kind(Flatpak.RefKind.APP, None)
                        if r.get_name() == app_id), None)
            if ref is None:
                return _refused("that app is not installed for everyone on this computer")
            if ref.get_commit() == commit:
                print("result: unchanged", flush=True)
                return 0
            print("progress: 0.10 Getting the previous version", flush=True)
            transaction = revert_transaction(installation, ref, commit)
            transaction.run(None)
            print("progress: 1.00 Done", flush=True)
            print("result: reverted", flush=True)
            return 0
    except SourceUnavailable as error:
        return _refused(str(error))
    except GLib.Error as error:
        text = error.message or str(error)
        lower = text.lower()
        # libflatpak's words for a build the remote does not have, over HTTP ("Server returned
        # HTTP 404", "No such metadata object") and from a file:// repository (seen in the
        # container test: "Error opening file .../objects/ff/ff…ff.commit: No such file").
        if any(words in lower for words in ("no such metadata object", "no such commit", "not found",
                                             "404", "couldn't find")) or \
                (".commit" in lower and "no such file" in lower):
            return fail(f"commit-unavailable: {text.splitlines()[0][:300]}")
        return fail(text.splitlines()[0][:300])
    except (OSError, RuntimeError) as error:
        return fail(str(error))


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if os.geteuid() != 0:
        return fail("this helper must be authorized through polkit")
    if values == ["reconcile"]:
        try:
            with system_transaction_lock():
                cleanup_abandoned_system_transactions()
                reconcile_receipts()
            return 0
        except (OSError, RuntimeError) as error:
            return fail(str(error))
    if len(values) == 3 and values[0] == "flatpak-revert":
        return flatpak_revert(values[1], values[2])
    if len(values) == 3 and values[0] == 'remove-layered':
        from . import layered_apps
        try:
            with system_transaction_lock():
                layered_apps.remove(values[1], values[2])
            return 0
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            return fail(str(error))
    if len(values) == 2 and values[0] in {"override-remove", "override-reset"}:
        # ADR-031: remove or restore an app that Luma's image ships.
        from .system_overrides import main as override_main
        return override_main(values, lock=system_transaction_lock)
    if len(values) == 2 and values[0] in {"snap-install", "snap-refresh", "snap-remove",
                                          "repo-install", "repo-remove"}:
        # An app from its publisher's official channel, named by catalogue id.
        from .depot_channels import main as channels_main
        return channels_main(values, lock=system_transaction_lock)
    if len(values) == 2 and values[0] == "remove":
        try:
            with system_transaction_lock():
                receipt_path, receipt = receipt_for(values[1])
                restart_required = manage_backend(values[0], receipt)
                if values[0] == "remove":
                    if restart_required:
                        receipt["state"] = "removal-pending-restart"
                        write_receipt(receipt_path, receipt)
                    else:
                        receipt_path.unlink()
            return 0
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError, subprocess.SubprocessError) as error:
            return fail(str(error))
    if len(values) != 3 or values[0] not in {"rpm", "snap"}:
        return fail("usage: luma-installer-system rpm|snap STAGED_FILE SHA256 | remove SHA256 | remove-layered DESKTOP_ID PACKAGE | "
                    "override-remove PACKAGE | override-reset PACKAGE | flatpak-revert APP_ID COMMIT | "
                    "snap-install|snap-refresh|snap-remove|repo-install|repo-remove CATALOG_ID | reconcile")
    kind, source_value, expected = values
    source = Path(source_value)
    descriptor = -1
    transaction: Path | None = None
    try:
        with system_transaction_lock():
            descriptor, metadata = validate_source(source, expected)
            if metadata.st_size > 8 * 1024 * 1024 * 1024:
                raise ValueError("package exceeds the 8 GB safety limit")
            root = SYSTEM_STATE_ROOT / "transactions"
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            require_transaction_space(root, metadata.st_size)
            transaction = Path(tempfile.mkdtemp(prefix="transaction-", dir=root))
            transaction.chmod(0o700)
            package = copy_and_verify(descriptor, f".{kind}", expected, transaction)
            descriptor = -1
            name = package_identity(kind, package)
            restart_required = run_backend(kind, package)
            receipt_root = SYSTEM_STATE_ROOT / "receipts"
            receipt_root.mkdir(mode=0o755, parents=True, exist_ok=True)
            receipt = receipt_root / f"{expected}.json"
            write_receipt(receipt, {
                "format": kind, "sha256": expected, "installed_unix": int(time.time()),
                "restart_required": restart_required, "package_name": name, "state": "installed",
                "asserted_origin": False if kind == "snap" else None,
            })
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        return fail(str(error))
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if transaction is not None:
            shutil.rmtree(transaction, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
