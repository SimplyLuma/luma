# SPDX-License-Identifier: Apache-2.0
"""Device admission for the shared updater; no hardware mutation."""

FP6 = "fairphone,fp6"
TITAN2E = "unihertz,titan2e"

# Hardware targets with their own update stream: the board compatible string
# the kernel's device tree must list, and the only architecture it runs.
DEVICES = {
    FP6: ("fairphone,fp6", "aarch64", "fairphone-fp6"),
    TITAN2E: ("unihertz,titan2-elite", "aarch64", "unihertz-titan2e"),
}


def graph_slug(configured: str, arch: str) -> str:
    """The configured device's exact publication bucket; never infer a board."""
    if not configured:
        return ""
    device = DEVICES.get(configured)
    if device is None or device[1] != arch:
        raise ValueError("configured update hardware target does not match this architecture")
    return device[2]


def require_device_target(paths, configured: str, arch: str) -> None:
    """A known phone can never silently follow a generic desktop graph.

    The package configuration selects a stream; the kernel's device tree must
    independently corroborate a hardware-specific stream. An unreadable device
    tree is not an absent device tree.
    """
    try:
        with paths.device_tree_compatible.open("rb") as stream:
            raw = stream.read(65537)
            if not raw:
                raise ValueError("device hardware identity is empty")
    except FileNotFoundError:
        raw = b""
    except OSError as error:
        raise ValueError("cannot verify this device's hardware identity") from error
    if len(raw) > 65536:
        raise ValueError("device hardware identity is oversized")
    if raw and (not raw.endswith(b"\0") or b"\0\0" in raw or raw.startswith(b"\0")):
        raise ValueError("device hardware identity is not a valid string list")
    try:
        compatible = raw.decode("ascii").split("\0")
    except UnicodeDecodeError as error:
        raise ValueError("device hardware identity is malformed") from error
    if configured:
        board, board_arch, _slug = DEVICES.get(configured, (None, None, None))
        if board_arch != arch or board not in compatible:
            raise ValueError("configured update hardware target does not match this device")
    elif any(board in compatible for board, _arch, _slug in DEVICES.values()):
        raise ValueError("this device requires a hardware-targeted update stream")


def require_commit_target(metadata: dict, configured: str, arch: str) -> None:
    """A graph label cannot authorize a commit built for another target.

    Every commit that names an architecture must name this one. Generic
    commits published before the key existed carry none; the graph's own
    architecture still bounds them.
    """
    if not isinstance(metadata, dict):
        raise ValueError("update commit hardware metadata is unavailable")
    if not configured:
        if "org.projectluma.architecture" in metadata and (
                type(metadata["org.projectluma.architecture"]) is not str
                or metadata["org.projectluma.architecture"] != arch):
            raise ValueError("update commit is built for another architecture")
        target = metadata.get("org.projectluma.hardware-target", "")
        if type(target) is not str or target:
            raise ValueError("hardware-targeted update commit cannot update a generic desktop")
        return
    expected = {"org.projectluma.hardware-target": configured,
                "org.projectluma.architecture": arch}
    for key, value in expected.items():
        if type(metadata.get(key)) is not str or metadata[key] != value:
            raise ValueError("update commit does not match this device's hardware target")
