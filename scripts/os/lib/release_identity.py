#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Luma's release identity: the one place every name and version is rendered.

The identity is config/os/release.env (codename, stage, stage number, major
version) plus two facts of the build: its channel and, for a nightly, the day
it is labelled for. Everything that names a release, from os-release to the
download index, the update graph and the release notes, calls this module.

Display names (owner decision 2026-09-17, ADR-040):

  nightly of a beta stage   Luma (Prairie, Beta 0, Nightly 20260916)
  beta release              Luma (Prairie, Beta 1)   Luma (Prairie, Beta 1.1)
  final release             Luma (Version 1, Prairie)
  nightly after final       Luma (Version 1, Prairie, Nightly 20261102)

Machine versions are unchanged, so every installed client keeps ordering them:
1.0.0-nightly.<build id>, 1.0.0-beta.<stage number>, 1.0.0.

  release_identity.py os-release --channel C --build-id ID --nightly-date YYYY-MM-DD
                                 --build-date YYYY-MM-DD [--contract FILE]
  release_identity.py display-name --channel C [--nightly-date YYYY-MM-DD] [--contract FILE]
  release_identity.py machine-version --channel C --build-id ID [--contract FILE]
  release_identity.py nightly-date --build-id ID --built UTC-TIMESTAMP
"""

import argparse
import datetime
import os
import re
import sys
import zoneinfo
from dataclasses import dataclass

CONTRACT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "config", "os", "release.env")
CENTRAL = "America/Chicago"
CHANNELS = ("nightly", "beta", "stable")


@dataclass(frozen=True)
class Identity:
    name: str
    os_id: str
    product_version: str
    version_id: str
    major: str
    codename: str
    stage: str
    stage_number: str
    fedora_release: str

    @property
    def codename_display(self) -> str:
        return self.codename[:1].upper() + self.codename[1:]


def read_contract(path: str = CONTRACT) -> dict:
    values = {}
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
    return values


def load(path: str = CONTRACT) -> Identity:
    values = read_contract(path)
    try:
        identity = Identity(
            name=values["LUMA_OS_NAME"], os_id=values["LUMA_OS_ID"],
            product_version=values["LUMA_OS_PRODUCT_VERSION"], version_id=values["LUMA_OS_VERSION_ID"],
            major=values["LUMA_OS_MAJOR"], codename=values["LUMA_OS_CODENAME"],
            stage=values["LUMA_OS_STAGE"], stage_number=values.get("LUMA_OS_STAGE_NUMBER", ""),
            fedora_release=values["LUMA_OS_FEDORA_RELEASE"],
        )
    except KeyError as error:
        raise ValueError(f"release contract has no {error.args[0]}") from None
    validate(identity)
    return identity


def validate(identity: Identity) -> None:
    if identity.name != "Luma":
        raise ValueError(f"invalid OS name: {identity.name}")
    if not re.fullmatch(r"[a-z][a-z0-9]*", identity.codename):
        raise ValueError(f"invalid codename: {identity.codename}")
    if not re.fullmatch(r"\d+\.\d+\.\d+", identity.product_version):
        raise ValueError(f"invalid product version: {identity.product_version}")
    if not re.fullmatch(r"\d+", identity.major) or identity.product_version.split(".")[0] != identity.major:
        raise ValueError(f"major version {identity.major} does not match {identity.product_version}")
    # os-release VERSION_ID names the release line and never changes within it
    # (a beta, a nightly and the final release of Luma 1 are all VERSION_ID=1),
    # so tools that key on it (sysext matching, CPE, dnf) see one release.
    if identity.version_id != identity.major:
        raise ValueError(f"VERSION_ID must be the major version {identity.major}, not {identity.version_id}")
    if identity.stage == "beta":
        if not re.fullmatch(r"\d+(\.\d+)?", identity.stage_number):
            raise ValueError(f"invalid beta stage number: {identity.stage_number!r}")
    elif identity.stage == "final":
        if identity.stage_number:
            raise ValueError("a final stage has no stage number")
    else:
        raise ValueError(f"stage must be beta or final, not {identity.stage}")
    if not re.fullmatch(r"\d+", identity.fedora_release):
        raise ValueError(f"invalid Fedora release: {identity.fedora_release}")


def label_day(nightly_date: str) -> str:
    """2026-09-16 or 20260916 -> 20260916."""
    compact = nightly_date.replace("-", "")
    try:
        datetime.date(int(compact[0:4]), int(compact[4:6]), int(compact[6:8]))
    except (ValueError, IndexError):
        raise ValueError(f"invalid nightly date: {nightly_date!r}") from None
    if not re.fullmatch(r"\d{8}", compact):
        raise ValueError(f"invalid nightly date: {nightly_date!r}")
    return compact


def check_channel(identity: Identity, channel: str) -> None:
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel: {channel}")
    if channel == "beta" and identity.stage != "beta":
        raise ValueError("a beta release needs LUMA_OS_STAGE=beta")
    if channel == "beta" and identity.stage_number in ("0", ""):
        raise ValueError("Beta 0 is published only as nightlies; set LUMA_OS_STAGE_NUMBER for a beta release")
    if channel == "stable" and identity.stage != "final":
        raise ValueError("a stable release needs LUMA_OS_STAGE=final")


def version_text(identity: Identity, channel: str, nightly_date: str = "") -> str:
    """os-release VERSION: the name without "Luma" and without parentheses."""
    check_channel(identity, channel)
    if identity.stage == "beta":
        parts = [identity.codename_display, f"Beta {identity.stage_number}"]
    else:
        parts = [f"Version {identity.major}", identity.codename_display]
    if channel == "nightly":
        if not nightly_date:
            raise ValueError("a nightly needs its nightly date")
        parts.append(f"Nightly {label_day(nightly_date)}")
    return ", ".join(parts)


def stage_text(identity: Identity) -> str:
    """VERSION of the release line's current stage, without a channel or build:
    "Prairie, Beta 0", "Version 1, Prairie". luma-release ships it; an image
    build replaces it with the build's own name."""
    if identity.stage == "beta":
        return f"{identity.codename_display}, Beta {identity.stage_number}"
    return f"Version {identity.major}, {identity.codename_display}"


def display_name(identity: Identity, channel: str, nightly_date: str = "") -> str:
    return f"{identity.name} ({version_text(identity, channel, nightly_date)})"


def machine_version(identity: Identity, channel: str, build_id: str) -> str:
    """The ordered version luma-update, the graph and OSTree compare (unchanged)."""
    check_channel(identity, channel)
    if channel == "nightly":
        if not re.fullmatch(r"\d{8}\.\d+", build_id):
            raise ValueError(f"invalid build id: {build_id}")
        return f"{identity.product_version}-nightly.{build_id}"
    if channel == "beta":
        return f"{identity.product_version}-beta.{identity.stage_number}"
    return identity.product_version


def nightly_date(build_id: str, built_utc: str) -> str:
    """The Central day a nightly is labelled for: the build id's day, or the
    Central day it was built on when that is earlier (release-process.md)."""
    if not re.fullmatch(r"\d{8}\.\d+", build_id):
        raise ValueError(f"invalid build id: {build_id}")
    id_day = datetime.date(int(build_id[0:4]), int(build_id[4:6]), int(build_id[6:8]))
    built = datetime.datetime.fromisoformat(built_utc.replace("Z", "+00:00"))
    if built.tzinfo is None:
        raise ValueError(f"build time has no time zone: {built_utc}")
    return min(id_day, built.astimezone(zoneinfo.ZoneInfo(CENTRAL)).date()).isoformat()


def quote(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._:/+-]*", value):
        value = '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'
    return value


def os_release_fields(identity: Identity, channel: str, build_id: str, nightly: str, build_date: str) -> list:
    if not re.fullmatch(r"\d{8}\.\d+", build_id):
        raise ValueError(f"invalid build id: {build_id}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", build_date):
        raise ValueError(f"invalid build date: {build_date}")
    version = version_text(identity, channel, nightly)
    fields = [
        ("NAME", identity.name),
        ("VERSION", version),
        ("ID", identity.os_id),
        ("ID_LIKE", "fedora"),
        ("VERSION_ID", identity.version_id),
        ("VERSION_CODENAME", identity.codename),
        ("PRETTY_NAME", f"{identity.name} ({version})"),
        # systemd's os-release: development for nightlies and betas.
        ("RELEASE_TYPE", "stable" if identity.stage == "final" and channel == "stable" else "development"),
        ("BUILD_ID", build_id),
        ("IMAGE_ID", "luma-desktop"),
        ("IMAGE_VERSION", build_id),
        ("VARIANT", "Desktop"),
        ("VARIANT_ID", "desktop"),
        ("ANSI_COLOR", "0;38;2;60;110;180"),
        # Icon name of the system logo (luma-logos, ADR-040): Settings › About
        # and every other presenter of the system's mark read it from here.
        ("LOGO", "luma-logo"),
        ("CPE_NAME", f"cpe:/o:projectluma:luma:{identity.version_id}"),
        ("DEFAULT_HOSTNAME", "luma"),
        ("VENDOR_NAME", "Project Luma"),
        ("VENDOR_URL", "https://simplyluma.com"),
        ("HOME_URL", "https://simplyluma.com"),
        ("DOCUMENTATION_URL", "https://simplyluma.com/help"),
        ("SUPPORT_URL", "https://simplyluma.com/support"),
        ("BUG_REPORT_URL", "https://simplyluma.com/support"),
        ("PLATFORM_ID", f"platform:f{identity.fedora_release}"),
        ("LUMA_RELEASE_CHANNEL", channel),
        ("LUMA_RELEASE_STAGE", identity.stage),
    ]
    if identity.stage_number:
        fields.append(("LUMA_RELEASE_STAGE_NUMBER", identity.stage_number))
    if channel == "nightly":
        fields.append(("LUMA_NIGHTLY_DATE", label_day(nightly)))
    fields += [
        ("LUMA_BUILD_DATE", build_date),
        ("LUMA_FEDORA_RELEASE", identity.fedora_release),
    ]
    return fields


def package_os_release_fields(identity: Identity) -> list:
    """os-release as the luma-release package ships it: the stage's name and
    everything tools need, but nothing that names a build (no BUILD_ID,
    IMAGE_VERSION, channel or nightly date). Presenters that find no
    IMAGE_VERSION ask luma-update for the booted build's name."""
    version = stage_text(identity)
    fields = [
        ("NAME", identity.name),
        ("VERSION", version),
        ("ID", identity.os_id),
        ("ID_LIKE", "fedora"),
        ("VERSION_ID", identity.version_id),
        ("VERSION_CODENAME", identity.codename),
        ("PRETTY_NAME", f"{identity.name} ({version})"),
        ("RELEASE_TYPE", "stable" if identity.stage == "final" else "development"),
        ("IMAGE_ID", "luma-desktop"),
        ("VARIANT", "Desktop"),
        ("VARIANT_ID", "desktop"),
        ("ANSI_COLOR", "0;38;2;60;110;180"),
        ("LOGO", "luma-logo"),
        ("CPE_NAME", f"cpe:/o:projectluma:luma:{identity.version_id}"),
        ("DEFAULT_HOSTNAME", "luma"),
        ("VENDOR_NAME", "Project Luma"),
        ("VENDOR_URL", "https://simplyluma.com"),
        ("HOME_URL", "https://simplyluma.com"),
        ("DOCUMENTATION_URL", "https://simplyluma.com/help"),
        ("SUPPORT_URL", "https://simplyluma.com/support"),
        ("BUG_REPORT_URL", "https://simplyluma.com/support"),
        ("PLATFORM_ID", f"platform:f{identity.fedora_release}"),
        ("LUMA_RELEASE_STAGE", identity.stage),
    ]
    if identity.stage_number:
        fields.append(("LUMA_RELEASE_STAGE_NUMBER", identity.stage_number))
    fields.append(("LUMA_FEDORA_RELEASE", identity.fedora_release))
    return fields


def render_os_release(identity: Identity, channel: str, build_id: str, nightly: str, build_date: str) -> str:
    return "".join(f"{key}={quote(value)}\n" for key, value in
                   os_release_fields(identity, channel, build_id, nightly, build_date))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("os-release", "display-name", "machine-version"):
        command = sub.add_parser(name)
        command.add_argument("--contract", default=CONTRACT)
        command.add_argument("--channel", required=True)
        if name != "display-name":
            command.add_argument("--build-id", required=True)
        if name != "machine-version":
            command.add_argument("--nightly-date", default="")
        if name == "os-release":
            command.add_argument("--build-date", required=True)
    package = sub.add_parser("package-os-release")
    package.add_argument("--contract", default=CONTRACT)
    package.add_argument("--system-release", action="store_true", help="print /usr/lib/luma-release instead")
    day = sub.add_parser("nightly-date")
    day.add_argument("--build-id", required=True)
    day.add_argument("--built", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "nightly-date":
            print(nightly_date(args.build_id, args.built))
            return 0
        identity = load(args.contract)
        if args.command == "package-os-release":
            if args.system_release:
                print(f"{identity.name} release {identity.version_id} ({stage_text(identity)})")
            else:
                sys.stdout.write("".join(f"{k}={quote(v)}\n" for k, v in package_os_release_fields(identity)))
            return 0
        if args.command == "os-release":
            sys.stdout.write(render_os_release(identity, args.channel, args.build_id, args.nightly_date, args.build_date))
        elif args.command == "display-name":
            print(display_name(identity, args.channel, args.nightly_date))
        else:
            print(machine_version(identity, args.channel, args.build_id))
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
