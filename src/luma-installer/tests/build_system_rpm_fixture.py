#!/usr/bin/python3
"""Build the harmless system-RPM fixture used by the physical installer gate.

The package deliberately exports no desktop file, services, or scriptlets.  Its
single immutable marker makes activation and removal observable across an
rpm-ostree reboot without changing host behavior.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


SPEC = """Name: luma-system-installer-fixture
Version: 1.0
Release: 1
Summary: Harmless Luma system installer acceptance fixture
License: Apache-2.0
BuildArch: noarch
Source0: luma-system-installer-fixture.marker

%description
Harmless marker-only package for Luma's immutable system installer gate.

%install
install -D -m 0644 %{SOURCE0} %{buildroot}%{_datadir}/luma-installer-fixture/system-marker

%files
%{_datadir}/luma-installer-fixture/system-marker
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    values = parser.parse_args()
    output = values.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="luma-system-rpm-fixture-") as directory:
        top = Path(directory) / "rpmbuild"
        for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
            (top / name).mkdir(parents=True)
        (top / "SOURCES/luma-system-installer-fixture.marker").write_text(
            "Luma immutable system installer acceptance fixture\n", encoding="utf-8"
        )
        spec = top / "SPECS/luma-system-installer-fixture.spec"
        spec.write_text(SPEC, encoding="utf-8")
        subprocess.run(
            ["rpmbuild", "-bb", "--define", f"_topdir {top}", str(spec)],
            check=True,
        )
        built = next((top / "RPMS/noarch").glob("luma-system-installer-fixture-*.rpm"))
        destination = output / built.name
        shutil.copy2(built, destination)
        print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
