#!/usr/bin/python3
"""Exercise the privileged helper's user-staging trust boundary."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from luma_installer.system_helper import validate_source


def main() -> None:
    home = Path.home().resolve()
    digest = hashlib.sha256(b"safe fixture\n").hexdigest()
    test_root = Path(tempfile.mkdtemp(prefix="helper-validation-", dir=home))
    try:
        staging = test_root / ".local" / "share" / "luma" / "installer" / "staging" / digest
        staging.mkdir(parents=True)
        source = staging / "fixture.snap"
        source.write_bytes(b"safe fixture\n")

        # The account-home prefix is part of the contract, not the temporary
        # directory name. Recreate the canonical staging path for the test.
        canonical = home / ".local" / "share" / "luma" / "installer" / "staging" / digest
        canonical.mkdir(parents=True, exist_ok=True)
        package = canonical / f"helper-validation-{os.getpid()}.snap"
        package.write_bytes(source.read_bytes())
        descriptor, metadata = validate_source(package, digest)
        os.close(descriptor)
        assert metadata.st_uid == os.getuid()

        outside = test_root / "outside.snap"
        outside.write_bytes(source.read_bytes())
        try:
            validate_source(outside, digest)
        except ValueError:
            pass
        else:
            raise AssertionError("the helper accepted a package outside the staging directory")
    finally:
        if 'package' in locals():
            package.unlink(missing_ok=True)
        shutil.rmtree(test_root, ignore_errors=True)

    print("Privileged helper staging validation passed.")


if __name__ == "__main__":
    main()
