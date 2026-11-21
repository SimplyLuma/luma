# SPDX-License-Identifier: Apache-2.0
"""Graphics drivers come from the device (ADR-052, Applications 5): prototype.

An application's userland carries the Mesa of its release. That Mesa does not
know hardware released after it, and NVIDIA's libraries must match the loaded
kernel module exactly, so the drivers a tree uses are the device's. They are
kept in the store as layers and laid into each application's checkout as hard
links; the application's own commit is never changed.

Three routes, in the order Luma prefers them:

``mesa``    The default. Current Mesa built for the application's userland, with
            exactly the dependencies that build needs. Nothing else in the tree
            changes: same C library, same C++ runtime. The layer is built by
            the distribution's own tools and applied per package, never
            replacing a package with an older one. Where current Mesa comes
            from is data, one row per userland (``data/graphics-layers.json``):
            the release's backports, a Luma build where the distribution has
            none, or "release" where the release's own updates carry current
            Mesa and no layer is kept at all.
``host``    The device's own driver files and the libraries they need, the way
            Steam Linux Runtime's pressure-vessel imports them: for each
            library the newer of the device's and the tree's wins, and when
            the device's C library is the newer one it replaces the tree's
            whole C library, dynamic loader included, because drivers built
            against it cannot run on an older one. This changes what the
            application itself runs on, so it is the fallback: taken only when
            the data calls for a Mesa layer and the store has none.
``nvidia``  The device's NVIDIA userspace, which is built against an old C
            library and runs in any userland. Taken only when its version is
            the loaded kernel module's.

``route_plan`` says which of these a tree gets and why; ``ensure_layers``
builds or stages what the plan is missing.

Proven: GL through virgl in a virtual machine, on ``mesa`` and ``host``. The
``nvidia`` route has only met a constructed device root. Still to be proven on
real hardware, by three commands run inside a tree
(``luma-app-closure shell APP -- COMMAND``):

    glmark2-wayland        GL_RENDERER is the hardware driver, not llvmpipe
    vulkaninfo --summary   a physical device of the hardware driver, not llvmpipe (lavapipe)
    vainfo --display drm   decode profiles (VAEntrypointVLD) are listed

    Intel    i915, xe          iris; ANV; VA-API there is intel-media-driver (iHD), not
                               Mesa, and no layer carries it yet
    AMD      amdgpu            radeonsi; RADV; VA-API from radeonsi
    NVIDIA   nvidia            "NVIDIA ..." from libGLX_nvidia/libEGL_nvidia; Vulkan from
                               nvidia_icd; vainfo needs nvidia-vaapi-driver in the tree,
                               so decode may have to be shown through NVDEC instead
    ARM      panfrost/panthor (Mali, panvk), freedreno (Adreno, turnip), v3d (Raspberry
             Pi 5, v3dv), asahi (Apple, honeykrisp). Decode on these is V4L2, not
             VA-API: vainfo is expected to find nothing. ``host`` and ``nvidia``
             default to x86_64 (``machine=62``); on ARM they take ``machine=183``.

Nothing in the shipped install path uses this module.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import tempfile
from pathlib import Path

from . import closure
from .errors import InstallerError

# -- The device --------------------------------------------------------------------

def gpu_drivers(sys_root: Path = Path("/sys")) -> list[str]:
    """Kernel drivers bound to the device's display adapters."""
    found = []
    for card in sorted((sys_root / "class/drm").glob("card[0-9]*")):
        if "-" in card.name:
            continue
        try:
            name = os.path.basename(os.readlink(card / "device/driver"))
        except OSError:
            continue
        if name not in found:
            found.append(name)
    return found


def nvidia_module_version(sys_root: Path = Path("/sys")) -> str:
    try:
        return (sys_root / "module/nvidia/version").read_text().strip()
    except OSError:
        return ""


def device_nodes(dev_root: Path = Path("/dev")) -> list[str]:
    """Device nodes a sandbox needs for rendering."""
    nodes = [str(dev_root / "dri")] if (dev_root / "dri").is_dir() else []
    nodes += [str(path) for path in sorted(dev_root.glob("nvidia*")) if not path.is_dir()]
    if (dev_root / "nvidia-caps").is_dir():
        nodes.append(str(dev_root / "nvidia-caps"))
    return nodes


# -- Versions ----------------------------------------------------------------------

def _deb_order(character: str) -> int:
    if character == "~":
        return -1
    if character.isalpha():
        return ord(character)
    return ord(character) + 256 if character else 0


def _deb_compare_part(a: str, b: str) -> int:
    i = j = 0
    while i < len(a) or j < len(b):
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            x = _deb_order(a[i]) if i < len(a) and not a[i].isdigit() else 0
            y = _deb_order(b[j]) if j < len(b) and not b[j].isdigit() else 0
            if x != y:
                return -1 if x < y else 1
            i += i < len(a) and not a[i].isdigit()
            j += j < len(b) and not b[j].isdigit()
        k, m = i, j
        while k < len(a) and a[k].isdigit():
            k += 1
        while m < len(b) and b[m].isdigit():
            m += 1
        x, y = int(a[i:k] or 0), int(b[j:m] or 0)
        if x != y:
            return -1 if x < y else 1
        i, j = k, m
    return 0


def deb_version_compare(a: str, b: str) -> int:
    """dpkg --compare-versions: negative, zero or positive."""
    def split(value: str) -> tuple[int, str, str]:
        epoch, _colon, rest = value.partition(":") if ":" in value else ("0", "", value)
        upstream, _dash, revision = rest.rpartition("-") if "-" in rest else (rest, "", "")
        return int(epoch) if epoch.isdigit() else 0, upstream, revision
    (e1, u1, r1), (e2, u2, r2) = split(a), split(b)
    if e1 != e2:
        return -1 if e1 < e2 else 1
    return _deb_compare_part(u1, u2) or _deb_compare_part(r1, r2)


# -- ELF ---------------------------------------------------------------------------

DT_NEEDED, DT_SONAME, DT_STRTAB = 1, 14, 5
PT_LOAD, PT_DYNAMIC = 1, 2


def elf_dynamic(path: Path) -> dict[str, object] | None:
    """{"bits", "machine", "soname", "needed"} for a dynamic ELF file, else None."""
    try:
        with path.open("rb") as stream:
            head = stream.read(64)
            if len(head) < 52 or head[:4] != b"\x7fELF" or head[5] != 1:
                return None
            wide = head[4] == 2
            machine = struct.unpack_from("<H", head, 18)[0]
            if wide:
                phoff, = struct.unpack_from("<Q", head, 32)
                phentsize, phnum = struct.unpack_from("<HH", head, 54)
            else:
                phoff, = struct.unpack_from("<I", head, 28)
                phentsize, phnum = struct.unpack_from("<HH", head, 42)
            stream.seek(phoff)
            table = stream.read(phentsize * phnum)
            loads, dynamic = [], None
            for index in range(phnum):
                entry = table[index * phentsize:(index + 1) * phentsize]
                if wide:
                    kind, _flags, offset, vaddr, _paddr, filesz = struct.unpack_from("<IIQQQQ", entry)
                else:
                    kind, offset, vaddr, _paddr, filesz = struct.unpack_from("<IIIII", entry)
                if kind == PT_LOAD:
                    loads.append((vaddr, offset, filesz))
                elif kind == PT_DYNAMIC:
                    dynamic = (offset, filesz)
            if dynamic is None:
                return None
            stream.seek(dynamic[0])
            data = stream.read(dynamic[1])
            size, form = (16, "<qQ") if wide else (8, "<iI")
            needed, soname, strtab = [], None, None
            for index in range(0, len(data) - size + 1, size):
                tag, value = struct.unpack_from(form, data, index)
                if tag == 0:
                    break
                if tag == DT_NEEDED:
                    needed.append(value)
                elif tag == DT_SONAME:
                    soname = value
                elif tag == DT_STRTAB:
                    strtab = value
            if strtab is None:
                return None
            base = next((offset + strtab - vaddr for vaddr, offset, filesz in loads if vaddr <= strtab < vaddr + filesz), None)
            if base is None:
                return None

            def text(offset: int) -> str:
                stream.seek(base + offset)
                return stream.read(256).split(b"\0", 1)[0].decode("utf-8", "replace")

            return {"bits": 64 if wide else 32, "machine": machine, "soname": text(soname) if soname is not None else "",
                    "needed": [text(offset) for offset in needed]}
    except (OSError, struct.error):
        return None


SHT_DYNSYM, SHT_GNU_VERDEF = 11, 0x6FFFFFFD


def elf_exports(path: Path) -> tuple[frozenset[str], frozenset[str]]:
    """(symbols a library defines, symbol versions it defines), from its section table."""
    try:
        data = path.read_bytes()
    except OSError:
        return frozenset(), frozenset()
    if len(data) < 64 or data[:4] != b"\x7fELF" or data[5] != 1:
        return frozenset(), frozenset()
    wide = data[4] == 2
    try:
        if wide:
            shoff, = struct.unpack_from("<Q", data, 40)
            shentsize, shnum = struct.unpack_from("<HH", data, 58)
        else:
            shoff, = struct.unpack_from("<I", data, 32)
            shentsize, shnum = struct.unpack_from("<HH", data, 46)
        sections = []
        for index in range(shnum):
            base = shoff + index * shentsize
            if wide:
                _name, kind, _flags, _addr, offset, size, link, _info, _align, entsize = struct.unpack_from("<IIQQQQIIQQ", data, base)
            else:
                _name, kind, _flags, _addr, offset, size, link, _info, _align, entsize = struct.unpack_from("<IIIIIIIIII", data, base)
            sections.append((kind, offset, size, link, entsize))

        def text(table: int, offset: int) -> str:
            start = sections[table][1] + offset
            return data[start:data.index(b"\0", start)].decode("utf-8", "replace")

        symbols: set[str] = set()
        versions: set[str] = set()
        for kind, offset, size, link, entsize in sections:
            if kind == SHT_DYNSYM and entsize:
                for position in range(offset + entsize, offset + size, entsize):
                    if wide:
                        name, info, _other, shndx = struct.unpack_from("<IBBH", data, position)
                    else:
                        name, _value, _size, info, _other, shndx = struct.unpack_from("<IIIBBH", data, position)
                    if shndx != 0 and info >> 4 in (1, 2):
                        symbols.add(text(link, name))
            elif kind == SHT_GNU_VERDEF:
                position = offset
                while position < offset + size:
                    _version, flags, _ndx, _count, _hash, aux, following = struct.unpack_from("<HHHHIII", data, position)
                    name, _next = struct.unpack_from("<II", data, position + aux)
                    if not flags & 1:  # not the entry for the library's own name
                        versions.add(text(link, name))
                    if not following:
                        break
                    position += following
        return frozenset(symbols), frozenset(versions)
    except (struct.error, IndexError, ValueError):
        return frozenset(), frozenset()


def file_version(path: Path, soname: str) -> tuple[int, ...]:
    """libdrm.so.2 -> libdrm.so.2.4.124 -> (2, 4, 124): the version in the real file's name."""
    try:
        real = os.path.basename(os.path.realpath(path))
    except OSError:
        return ()
    if not real.startswith(soname):
        return ()
    return tuple(int(part) for part in re.findall(r"\d+", real[len(soname):]))


def _numbers(names: frozenset[str]) -> tuple[int, ...]:
    return max((tuple(int(part) for part in re.findall(r"\d+", name)) for name in names), default=())


def tree_is_newer(device: Path, tree: Path, soname: str, device_file_version: tuple[int, ...] | None = None) -> bool:
    """Whether the tree's copy of a library is strictly newer than the device's.

    The order pressure-vessel's library comparison uses: the versions a
    library defines for its symbols, then the symbols themselves (a strict
    superset is newer), then the version in the file name. Anything short of
    strictly newer goes to the device, whose drivers were built against its
    own copy."""
    device_symbols, device_versions = elf_exports(device)
    tree_symbols, tree_versions = elf_exports(tree)
    # Names with a leading underscore are the linker's and the library's private ones.
    device_symbols = frozenset(name for name in device_symbols if not name.startswith("_"))
    tree_symbols = frozenset(name for name in tree_symbols if not name.startswith("_"))
    if device_versions != tree_versions:
        if tree_versions > device_versions:
            return True
        if device_versions > tree_versions:
            return False
        return _numbers(tree_versions) > _numbers(device_versions)
    if device_symbols != tree_symbols:
        if tree_symbols > device_symbols:
            return True
        if device_symbols > tree_symbols:
            return False
    ours = file_version(device, soname) if device_file_version is None else tuple(device_file_version)
    return file_version(tree, soname) > ours


def glibc_version(path: Path) -> tuple[int, ...]:
    """The newest GLIBC_x.y a C library defines."""
    versions = elf_exports(path)[1]
    return _numbers(frozenset(name for name in versions if name.startswith("GLIBC_2")))


# -- Where libraries live in a root ------------------------------------------------

GLIBC_FAMILY = re.compile(r"^(ld-linux.*\.so\.\d+|ld\.so\.1|lib(c|m|dl|rt|pthread|util|resolv|anl|mvec|nsl|BrokenLocale|thread_db|c_malloc_debug|"
                          r"nss_(files|dns|compat|hesiod|db|myhostname|systemd|resolve|mymachines))\.so\.\d+)$")

MULTIARCH = {62: "x86_64-linux-gnu", 183: "aarch64-linux-gnu", 3: "i386-linux-gnu"}


def library_candidates(bits: int = 64, machine: int = 62) -> list[str]:
    """Where libraries of one word size may live, in Debian's layout and Fedora's."""
    candidates = [f"usr/lib/{MULTIARCH[machine]}"] if machine in MULTIARCH else []
    return candidates + ["usr/lib64" if bits == 64 else "usr/lib32", "usr/lib"]


def library_dirs(root: Path, bits: int = 64, machine: int = 62) -> list[Path]:
    """The folders a root's loader searches (relative to the root), its C library's first."""
    present = [Path(entry) for entry in library_candidates(bits, machine) if (root / entry).is_dir()]
    return sorted(present, key=lambda entry: not (root / entry / "libc.so.6").exists())


def find_library(root: Path, soname: str, bits: int = 64, machine: int = 62) -> Path | None:
    for folder in library_dirs(root, bits, machine):
        candidate = root / folder / soname
        if candidate.exists():
            return candidate
    return None


def _resolve_in(root: Path, path: Path) -> Path | None:
    """Follow links without leaving the root."""
    current = path
    for _hop in range(16):
        if not current.is_symlink():
            return current if current.exists() else None
        target = os.readlink(current)
        current = root / target.lstrip("/") if target.startswith("/") else current.parent / target
        current = Path(os.path.normpath(current))
        if root not in current.parents and current != root:
            return None
    return None


# -- Route "host": the device's own drivers ----------------------------------------

LOADERS = ("libGL.so.1", "libEGL.so.1", "libGLESv2.so.2", "libGLESv1_CM.so.1", "libOpenGL.so.0", "libGLX.so.0",
           "libGLdispatch.so.0", "libgbm.so.1", "libvulkan.so.1", "libva.so.2", "libva-drm.so.2", "libva-wayland.so.2",
           "libva-x11.so.2", "libvdpau.so.1", "libdrm.so.2", "libGLX_mesa.so.0", "libEGL_mesa.so.0", "libglapi.so.0")

# Mesa's own libraries share private interfaces: they move as one set.
MESA_SET = re.compile(r"^lib(gbm\.so\.1|EGL_mesa\.so|GLX_mesa\.so|glapi\.so|gallium[-.\w]*\.so|dril_dri\.so|xatracker\.so|"
                      r"vulkan_\w+\.so|VkLayer_MESA\w*\.so)")
DRIVER_FOLDERS = ("dri", "gbm", "vdpau")
REPLACED_MANIFESTS = ("usr/share/glvnd/egl_vendor.d", "usr/share/vulkan/icd.d")
MANIFEST_FOLDERS = ("usr/share/glvnd/egl_vendor.d", "usr/share/vulkan/icd.d", "usr/share/vulkan/implicit_layer.d",
                    "usr/share/vulkan/explicit_layer.d", "usr/share/egl/egl_external_platform.d", "usr/share/drirc.d",
                    "usr/share/libdrm")


def _manifest_libraries(device: Path) -> list[str]:
    names = []
    for folder in MANIFEST_FOLDERS[:3]:
        for manifest in sorted((device / folder).glob("*.json")):
            try:
                described = json.loads(manifest.read_text())
            except (OSError, ValueError):
                continue
            for section in ("ICD", "layer"):
                value = (described.get(section) or {}).get("library_path", "") if isinstance(described.get(section), dict) else ""
                if value:
                    names.append(value)
    return names


def host_driver_files(device: Path = Path("/"), bits: int = 64, machine: int = 62) -> dict[str, object]:
    """Everything the device's graphics stack is made of, as paths under ``device``.

    {"libraries": {relative path: soname}, "glibc": {...}, "data": [relative paths], "glibc_version": (2, 43, 0)}
    """
    folders = library_dirs(device, bits, machine)
    if not folders:
        raise InstallerError("This device has no library folder to take drivers from.")
    queue: list[Path] = []
    for soname in LOADERS:
        found = find_library(device, soname, bits, machine)
        if found is not None:
            queue.append(found)
    for name in _manifest_libraries(device):
        found = device / name.lstrip("/") if name.startswith("/") else find_library(device, name, bits, machine)
        if found is not None and found.exists():
            queue.append(found)
    data: list[str] = []
    for folder in folders:
        for kind in DRIVER_FOLDERS:
            for driver in sorted((device / folder / kind).glob("*.so*")):
                queue.append(driver)
    libraries: dict[str, str] = {}
    glibc: dict[str, str] = {}
    file_versions: dict[str, list[int]] = {}
    seen: set[Path] = set()
    while queue:
        path = queue.pop()
        real = _resolve_in(device, path)
        if real is None or path in seen:
            continue
        seen.add(path)
        described = elf_dynamic(real)
        if described is None or described["bits"] != bits or described["machine"] != machine:
            continue
        relative = str(path.relative_to(device))
        name = str(described["soname"]) or path.name
        (glibc if GLIBC_FAMILY.match(path.name) else libraries)[relative] = name
        file_versions[relative] = list(file_version(path, name))
        for needed in described["needed"]:  # type: ignore[union-attr]
            found = find_library(device, str(needed), bits, machine)
            if found is not None and found not in seen:
                queue.append(found)
    libc = find_library(device, "libc.so.6", bits, machine)
    if libc is not None:
        # The whole C library moves together or not at all.
        for member in sorted(libc.parent.iterdir()):
            if GLIBC_FAMILY.match(member.name) and member.exists():
                glibc[str(member.relative_to(device))] = member.name
        for extra in ("gconv",):
            if (libc.parent / extra).is_dir():
                data.append(str((libc.parent / extra).relative_to(device)))
        for tool in ("usr/sbin/ldconfig", "usr/bin/ldconfig"):
            if (device / tool).is_file():
                data.append(tool)
                break
    for folder in MANIFEST_FOLDERS:
        if (device / folder).is_dir():
            data.append(folder)
    return {"libraries": libraries, "glibc": glibc, "data": data, "file_versions": file_versions,
            "glibc_version": list(glibc_version(libc)) if libc is not None else []}


def stage_host_layer(device: Path, destination: Path, bits: int = 64, machine: int = 62) -> dict[str, object]:
    """Copy the device's driver files into ``destination`` at their own paths.

    Links are followed: each library is stored as one file under the name the
    loader asks for."""
    files = host_driver_files(device, bits, machine)
    for group in ("libraries", "glibc"):
        for relative in files[group]:  # type: ignore[union-attr]
            source = _resolve_in(device, device / relative)
            if source is None:
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o755)
    for relative in files["data"]:  # type: ignore[union-attr]
        source = device / relative
        target = destination / relative
        if source.is_dir():
            shutil.copytree(source, target, symlinks=False, dirs_exist_ok=True,
                            ignore_dangling_symlinks=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o755)
    return {"schema": closure.SCHEMA, "kind": "graphics", "route": "host", "id": "host", **files}


def _place(source: Path, target: Path) -> None:
    """Put a store file into a checkout: a new directory entry, never a write."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    os.link(source, target)


def _place_tree(source: Path, target: Path) -> int:
    count = 0
    for folder, _dirs, names in os.walk(source):
        for name in names:
            path = Path(folder) / name
            _place(path, target / path.relative_to(source))
            count += 1
    return count


def apply_host_layer(layer: Path, record: dict[str, object], root: Path, bits: int = 64, machine: int = 62) -> dict[str, object]:
    """Lay the device's drivers into a checkout. Returns what was decided."""
    tree_libc = find_library(root, "libc.so.6", bits, machine)
    if tree_libc is None:
        raise InstallerError("The application tree has no C library.")
    device_glibc = tuple(record.get("glibc_version") or ())
    tree_glibc = glibc_version(tree_libc)
    swap = device_glibc > tree_glibc
    taken, kept = [], []
    versions = dict(record.get("file_versions") or {})  # type: ignore[arg-type]
    for relative, soname in dict(record["libraries"]).items():  # type: ignore[arg-type]
        source = layer / relative
        if not source.is_file():
            continue
        folder = Path(relative).parent.name
        if folder in DRIVER_FOLDERS:
            # A driver module belongs to the loader that came with it.
            _place(source, root / relative)
            taken.append(relative)
            continue
        present = find_library(root, soname, bits, machine)
        if present is not None and not MESA_SET.match(soname) and tree_is_newer(
                source, present, soname, tuple(versions.get(relative) or ())):
            kept.append(soname)
            continue
        _place(source, root / relative)
        if present is not None and present != root / relative:
            # One copy of a library per process: the tree's own gives way.
            _place(source, present.parent / soname)
        taken.append(relative)
    if swap:
        tree_dir = tree_libc.parent
        for relative, soname in dict(record["glibc"]).items():  # type: ignore[arg-type]
            source = layer / relative
            if not source.is_file():
                continue
            _place(source, root / relative)
            if soname.startswith("ld-"):
                for interpreter in (root / "usr/lib64" / soname, root / "usr/lib" / soname, tree_dir / soname):
                    if interpreter.is_symlink() or interpreter.exists():
                        _place(source, interpreter)
            elif (tree_dir / soname).exists() or (tree_dir / soname).is_symlink():
                _place(source, tree_dir / soname)
    for relative in REPLACED_MANIFESTS:
        # The tree's own driver manifests name the drivers being replaced.
        if (layer / relative).is_dir() and (root / relative).is_dir():
            for stale in (root / relative).iterdir():
                stale.unlink()
    for relative in record["data"]:  # type: ignore[union-attr]
        source = layer / relative
        if relative.endswith("gconv"):
            if swap and source.is_dir():
                _place_tree(source, root / relative)
        elif relative.endswith("ldconfig"):
            if swap and source.is_file():
                _place(source, root / "usr/sbin/ldconfig.luma-device")
        elif source.is_dir():
            _place_tree(source, root / relative)
    device_dirs = sorted({str(Path(relative).parent) for relative in taken if Path(relative).parent.name not in DRIVER_FOLDERS})
    own = str(tree_libc.parent.relative_to(root))
    return {"route": "host", "glibc_replaced": swap, "device_glibc": list(device_glibc), "tree_glibc": list(tree_glibc),
            "taken": len(taken), "kept": sorted(set(kept)),
            "library_dirs": ["/" + entry for entry in device_dirs if entry != own],
            "ldconfig": "/usr/sbin/ldconfig.luma-device" if swap else ""}


# -- Route "mesa": current Mesa for the userland ----------------------------------

# Where current Mesa for each userland comes from is data, defined per release.
LAYERS_VERSION = 1
LAYERS_ENVIRONMENT = "LUMA_GRAPHICS_LAYERS"
LAYERS_PATHS = (Path("/usr/share/luma/installer/graphics-layers.json"),
                Path(__file__).resolve().parent.parent / "data/graphics-layers.json")
# "backports"  the distribution's own backports suite: a layer is built from it.
# "luma"       a suite Luma builds and publishes: a layer is built from it.
# "updates"    only what the release's updates carry, which trees already install: nothing to build.
# "release"    the release's updates carry current Mesa: no layer is wanted.
LAYER_SOURCES = ("backports", "luma", "updates", "release")
BUILT_SOURCES = ("backports", "luma")
LUMA_BUILD_KEYS = ("source_package", "build_depends", "architectures", "cadence")

# The copy used when no file is found. Kept equal to data/graphics-layers.json (a test compares them).
BUILTIN_LAYERS: dict[str, object] = {
    "version": 1,
    "userlands": {
        "debian-13": {
            "source": "backports",
            "suite": "trixie-backports",
            "apt": "deb http://deb.debian.org/debian trixie-backports main",
            "luma_build": False,
            "note": ("Debian's own backports follow upstream Mesa while trixie is the stable release (26.1.6-1~bpo13+1 "
                 "when the prototype ran). Once Debian 14 is out the suite stops moving and this row becomes a Luma "
                 "build."),
        },
        "ubuntu-24.04": {
            "source": "updates",
            "suite": "noble-updates",
            "luma_build": True,
            "note": ("Ubuntu has no backports of Mesa. The hardware-enablement Mesa reaches noble-updates with each point "
                 "release, taken from the interim release before it, so it trails upstream by six months or more and "
                 "stops at 24.04.5. Trees already install it; a layer of it would add nothing. Current Mesa for noble "
                 "is Luma's to build."),
            "build": {
                "source_package": "mesa",
                "build_depends": ("The closure of mesa's Build-Depends that noble lacks at the needed version: libdrm, "
                     "wayland-protocols, directx-headers, spirv-tools and spirv-llvm-translator with the LLVM they are "
                     "built for, libva headers, and rustc with bindgen and cbindgen for the Rust drivers. Each is built "
                     "for noble and shipped in the same suite."),
                "architectures": ["x86_64", "aarch64"],
                "cadence": ("Rebuilt whenever the system image moves to a new Mesa feature release, and for each point release "
                     "the image takes, so a tree's Mesa is the device's."),
            },
        },
        "debian-12": {
            "source": "backports",
            "suite": "bookworm-backports",
            "apt": "deb http://deb.debian.org/debian bookworm-backports main",
            "luma_build": False,
            "note": ("bookworm-backports carries Mesa 25.0.7-2~bpo12+1 and no longer moves: backports for a superseded "
                 "release close about a year after the next one. Hardware that needs a newer Mesa than that needs a "
                 "Luma build for this row (same shape as ubuntu-24.04's)."),
        },
        "ubuntu-22.04": {
            "source": "updates",
            "suite": "jammy-updates",
            "luma_build": True,
            "note": ("Ubuntu has no backports of Mesa. jammy-updates stopped at the hardware-enablement Mesa 23.2 and will"
                 " not move again. Trees already install it. Current Mesa for jammy is Luma's to build, and it is the "
                 "widest build of the set."),
            "build": {
                "source_package": "mesa",
                "build_depends": ("As for ubuntu-24.04, and further back: meson, a current LLVM and clang, libdrm, wayland and "
                     "wayland-protocols, libglvnd, directx-headers, the SPIR-V tools, libva, and a Rust toolchain. jammy's"
                     " glibc 2.35 and GCC 11 are kept; the layer never replaces the C library or libstdc++."),
                "architectures": ["x86_64", "aarch64"],
                "cadence": ("Rebuilt whenever the system image moves to a new Mesa feature release, and for each point release "
                     "the image takes."),
            },
        },
        "fedora-45": {
            "source": "release",
            "luma_build": False,
            "note": ("Fedora rebases Mesa inside a release: the updates repository carries upstream's latest feature "
                 "series within weeks of its release. Current here means that series. A tree gets it when its packages"
                 " are resolved again, so no layer is kept."),
        },
        "fedora-44": {
            "source": "release",
            "luma_build": False,
            "note": ("The previous Fedora release takes the same Mesa rebases through its updates repository until shortly"
                 " before its end of life, about a month after the release two after it. Current here means at most "
                 "one feature series behind fedora-45; the userland is retired when the release is."),
        },
    },
    "not_yet_userlands": {
        "el-10": {
            "source": "release",
            "luma_build": True,
            "note": ("Not a userland yet. Enterprise Linux rebases Mesa only at minor releases, months behind upstream and"
                 " with a reduced driver set. Luma would build mesa from Fedora's packaging against EL 10's toolchain,"
                 " with the LLVM and Rust from AppStream, for x86_64 and aarch64, on the system image's cadence."),
        },
        "el-9": {
            "source": "release",
            "luma_build": True,
            "note": ("Not a userland yet. As el-10, with more to carry: EL 9's meson, libdrm, wayland-protocols and SPIR-V"
                 " tools are older than current Mesa builds against, so they join the build closure."),
        },
    },
}


def check_graphics_layers(data: object) -> list[str]:
    """What is wrong with a graphics-layers document; empty when it can be used."""
    if not isinstance(data, dict) or data.get("version") != LAYERS_VERSION or not isinstance(data.get("userlands"), dict):
        return [f"not a version {LAYERS_VERSION} graphics-layers document"]
    problems = []
    for group in ("userlands", "not_yet_userlands"):
        rows = data.get(group, {})
        if not isinstance(rows, dict):
            problems.append(f"{group} is not a table")
            continue
        for name, row in rows.items():
            if not isinstance(row, dict) or row.get("source") not in LAYER_SOURCES:
                problems.append(f"{name}: source is one of {', '.join(LAYER_SOURCES)}")
                continue
            if not isinstance(row.get("luma_build"), bool) or not isinstance(row.get("note"), str) or not row["note"]:
                problems.append(f"{name}: luma_build and note are required")
            if row["source"] in BUILT_SOURCES and not (row.get("suite") and str(row.get("apt", "")).startswith("deb ")):
                problems.append(f"{name}: a {row['source']} source names its suite and its apt line")
            build = row.get("build")
            if group == "userlands" and row.get("luma_build") is True and not (
                    isinstance(build, dict) and all(build.get(key) for key in LUMA_BUILD_KEYS)):
                problems.append(f"{name}: a Luma build says what is built ({', '.join(LUMA_BUILD_KEYS)})")
    return problems


def load_graphics_layers(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InstallerError(f"{path} cannot be read as graphics layer sources: {error}") from error
    problems = check_graphics_layers(data)
    if problems:
        raise InstallerError(f"{path}: {'; '.join(problems)}")
    return data


def graphics_layers() -> dict[str, object]:
    """The per-release table: $LUMA_GRAPHICS_LAYERS, the installed file, the source tree's, else the built-in copy."""
    override = os.environ.get(LAYERS_ENVIRONMENT, "")
    for path in ([Path(override)] if override else []) + list(LAYERS_PATHS):
        if path.is_file():
            try:
                return load_graphics_layers(path)
            except InstallerError:
                continue
    return BUILTIN_LAYERS


def mesa_source(userland: str) -> dict[str, object]:
    """A userland's row. One without a row wants a layer nothing can build yet."""
    row = graphics_layers()["userlands"].get(userland)  # type: ignore[union-attr]
    return dict(row) if row else {"source": "", "luma_build": True, "note": f"No Mesa source is defined for {userland}."}


def wants_mesa_layer(row: dict[str, object]) -> bool:
    """Whether current Mesa for the userland has to come from a layer."""
    return row.get("source") in BUILT_SOURCES or bool(row.get("luma_build"))


MESA_PACKAGES = ("libgl1-mesa-dri", "libglx-mesa0", "libegl-mesa0", "libgbm1", "mesa-vulkan-drivers", "mesa-va-drivers",
                 "libgl1", "libegl1", "libgles2", "libvulkan1", "libva2", "libva-drm2", "libva-wayland2", "libva-x11-2")

MESA_SCRIPT = r"""
set -e
printf '%s\n' "$LUMA_MESA_SOURCE" > /etc/apt/sources.list.d/luma-mesa.list
apt-get -q update
apt-get -q -y --no-install-recommends -t "$LUMA_MESA_SUITE" install "$@"
"""

PACKAGE_FILES_SCRIPT = r"""
for package in "$@"; do
  dpkg-query -L "$package" | while IFS= read -r path; do
    [ -f "$path" ] || [ -L "$path" ] || continue
    printf '%s\t%s\n' "$package" "$path"
  done
done
"""


def build_mesa_layer(userland_name: str, store, work: Path, cache: Path, log_path: Path | None = None) -> str:
    """Build the Mesa layer for a userland and commit it; returns the ref.

    The layer holds, per package, the files of every package the Mesa install
    added to or changed in the release's minimal root. The suite it installs
    from is the userland's row in the graphics-layers data."""
    source = mesa_source(userland_name)
    userland = closure.USERLANDS.get(userland_name)
    if userland is None or not source["source"]:
        raise InstallerError(f"No Mesa source is defined for {userland_name}.")
    if not wants_mesa_layer(source):
        raise InstallerError(f"{userland_name} needs no Mesa layer: its own updates carry current Mesa.")
    if source["source"] not in BUILT_SOURCES or not source.get("apt"):
        raise InstallerError(f"There is no current Mesa for {userland_name} to build a layer from: Luma has to build it first.")
    if userland.family != "deb":
        raise InstallerError(f"Mesa layers are built with apt; {userland_name} is not a Debian or Ubuntu release.")
    log = closure.BuildLog(log_path)
    architecture = closure.host_architecture()
    base_ref = closure.ensure_userland(userland_name, architecture, work, cache, store, log)
    base = {(row[0], row[2]): row[1] for row in store.read_record(base_ref)["packages"]}  # type: ignore[union-attr]
    target = work / f"mesa-{userland_name}"
    closure.remove_tree(target)
    target.mkdir(parents=True)
    root = target / "root"
    try:
        store.export(base_ref, root)
        common = [closure._lists_bind(cache, userland, ""), closure._archives_bind(cache, userland)]
        closure._write_in_tree(root, closure.DPKG_BUILD_PATH, closure.DPKG_BUILD)
        closure._write_in_tree(root, "usr/sbin/policy-rc.d", closure.POLICY_RC, 0o755)
        log.run("update userland", closure.sandbox_in_tree(root, [
            "env", "LUMA_SOURCES=" + "\n".join(userland.sources), "LUMA_FOREIGN=", "sh", "-c", closure.DEB_BASE_SCRIPT],
            binds=common))
        log.run("install mesa", closure.sandbox_in_tree(root, [
            "env", f"LUMA_MESA_SOURCE={source['apt']}", f"LUMA_MESA_SUITE={source['suite']}",
            "sh", "-c", MESA_SCRIPT, "sh", *MESA_PACKAGES], binds=common))
        lock = closure.parse_lock(log.run("lock", closure.sandbox_in_tree(root, closure.DEB_LOCK, network=False)).stdout)
        fresh = [row for row in lock if base.get((row[0], row[2])) != row[1]]
        listing = log.run("list files", closure.sandbox_in_tree(
            root, ["sh", "-c", PACKAGE_FILES_SCRIPT, "sh", *[f"{row[0]}:{row[2]}" if row[2] != "all" else row[0] for row in fresh]],
            network=False)).stdout
        layer = target / "layer"
        owned: dict[str, list[str]] = {}
        for line in listing.splitlines():
            package, _tab, path = line.partition("\t")
            name = package.split(":", 1)[0]
            relative = path.lstrip("/")
            if not relative.startswith(("usr/lib/", "usr/share/vulkan/", "usr/share/glvnd/", "usr/share/drirc.d/",
                                        "usr/share/libdrm/", "usr/share/egl/", "etc/")):
                continue
            owned.setdefault(name, []).append(relative)
        code = ("import os, shutil, sys, json\n"
                "root, layer, owned = sys.argv[1], sys.argv[2], json.load(open(sys.argv[3]))\n"
                "for name, paths in owned.items():\n"
                "    for relative in paths:\n"
                "        source, target = os.path.join(root, relative), os.path.join(layer, 'pkgs', name, relative)\n"
                "        os.makedirs(os.path.dirname(target), exist_ok=True)\n"
                "        if os.path.islink(source):\n"
                "            os.symlink(os.readlink(source), target)\n"
                "        else:\n"
                "            shutil.copyfile(source, target); os.chmod(target, os.stat(source).st_mode & 0o777)\n")
        (target / "owned.json").write_text(json.dumps(owned))
        closure._run([*closure.userns_prefix(), "python3", "-c", code, str(root), str(layer), str(target / "owned.json")],
                     timeout=1800)
        record = {"schema": closure.SCHEMA, "kind": "graphics", "route": "mesa", "id": f"mesa-{userland_name}",
                  "userland": userland_name, "architecture": architecture, "source": source["apt"], "suite": source["suite"],
                  "packages": fresh, "mesa": next((row[1] for row in fresh if row[0] == "libgl1-mesa-dri"), "")}
        ref = store.graphics_ref(f"mesa-{userland_name}", architecture)
        store.commit_tree(layer, ref, record, subject=f"Mesa {record['mesa']} for {userland_name}")
        return ref
    finally:
        closure.remove_tree(target)


def apply_mesa_layer(layer: Path, record: dict[str, object], tree_record: dict[str, object], root: Path) -> dict[str, object]:
    """Lay the userland's Mesa into a checkout, package by package.

    A package the tree already has at the same or a newer version is left
    alone, so the layer can never take a tree backwards."""
    have = {(row[0], row[2]): row[1] for row in tree_record.get("packages") or []}  # type: ignore[union-attr]
    taken, kept, files = [], [], 0
    for name, version, architecture in record["packages"]:  # type: ignore[misc]
        present = have.get((name, architecture))
        if present is not None and deb_version_compare(present, version) >= 0:
            kept.append(name)
            continue
        source = layer / "pkgs" / name
        if source.is_dir():
            files += _place_links(source, root)
            taken.append(name)
    return {"route": "mesa", "mesa": record.get("mesa", ""), "taken": taken, "kept": kept, "files": files,
            "library_dirs": [], "ldconfig": "", "glibc_replaced": False}


def _place_links(source: Path, root: Path) -> int:
    count = 0
    for folder, _dirs, names in os.walk(source):
        for name in names:
            path = Path(folder) / name
            target = root / path.relative_to(source)
            if path.is_symlink():
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_symlink() or target.exists():
                    target.unlink()
                os.symlink(os.readlink(path), target)
            else:
                _place(path, target)
            count += 1
    return count


# -- Route "nvidia" ----------------------------------------------------------------

NVIDIA_LIBRARIES = re.compile(r"^(lib(nvidia-[A-Za-z0-9_-]+|GLX_nvidia|EGL_nvidia|GLESv2_nvidia|GLESv1_CM_nvidia|nvcuvid|cuda|"
                              r"cudadebugger|nvoptix|vdpau_nvidia|nvidia_egl_[a-z0-9-]+)\.so[.\d]*|nvidia-drm_gbm\.so)$")
NVIDIA_DATA = ("usr/share/glvnd/egl_vendor.d/10_nvidia.json", "usr/share/vulkan/icd.d/nvidia_icd.json",
               "usr/share/vulkan/icd.d/nvidia_icd.x86_64.json", "usr/share/vulkan/implicit_layer.d/nvidia_layers.json",
               "usr/share/egl/egl_external_platform.d", "usr/share/nvidia", "etc/OpenCL/vendors/nvidia.icd")


def nvidia_userspace_version(device: Path = Path("/"), bits: int = 64, machine: int = 62) -> str:
    for folder in library_dirs(device, bits, machine):
        for path in sorted((device / folder).glob("libnvidia-glcore.so.*")) + sorted((device / folder).glob("libGLX_nvidia.so.*.*")):
            version = path.name.split(".so.", 1)[1]
            if re.fullmatch(r"\d+(\.\d+){1,2}", version):
                return version
    return ""


def _nvidia_libraries(device: Path, bits: int, machine: int) -> dict[str, str]:
    libraries: dict[str, str] = {}
    for folder in library_dirs(device, bits, machine):
        for path in sorted((device / folder).rglob("*.so*")):
            if not NVIDIA_LIBRARIES.match(path.name) or path.is_dir():
                continue
            real = _resolve_in(device, path)
            described = elf_dynamic(real) if real is not None else None
            if described is None or described["bits"] != bits or described["machine"] != machine:
                continue
            libraries[str(path.relative_to(device))] = str(described["soname"]) or path.name
    return libraries


def nvidia_files(device: Path = Path("/"), module_version: str | None = None, bits: int = 64, machine: int = 62) -> dict[str, object]:
    """The device's NVIDIA userspace, only when it is the loaded module's.

    A library from one driver release against a module from another fails at
    the first call, so a mismatch is refused here rather than at launch. On
    x86_64 the 32-bit libraries are listed apart, for trees that have 32-bit
    programs."""
    module = nvidia_module_version() if module_version is None else module_version
    userspace = nvidia_userspace_version(device, bits, machine)
    if not module:
        raise InstallerError("The NVIDIA kernel module is not loaded on this device.")
    if userspace != module:
        raise InstallerError(f"The NVIDIA libraries on this device are {userspace or 'missing'}; the loaded module is {module}.")
    data = [relative for relative in NVIDIA_DATA if (device / relative).exists()]
    return {"version": module, "libraries": _nvidia_libraries(device, bits, machine),
            "libraries32": _nvidia_libraries(device, 32, 3) if machine == 62 else {}, "data": data}


def stage_nvidia_layer(device: Path, destination: Path, module_version: str | None = None) -> dict[str, object]:
    files = nvidia_files(device, module_version)
    for relative in [*files["libraries"], *files["libraries32"]]:  # type: ignore[misc]
        source = _resolve_in(device, device / relative)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)  # type: ignore[arg-type]
        target.chmod(0o755)
    for relative in files["data"]:  # type: ignore[union-attr]
        source, target = device / relative, destination / relative
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    return {"schema": closure.SCHEMA, "kind": "graphics", "route": "nvidia", "id": f"nvidia-{files['version']}", **files}


def _in_tree(relative: str, device_dirs: list[str], tree_dir: Path | None) -> Path:
    """A device library's place in a tree: the same path below the tree's own library folder."""
    prefix = max((entry for entry in device_dirs if relative.startswith(entry + "/")), key=len, default="")
    return tree_dir / relative[len(prefix) + 1:] if prefix and tree_dir is not None else Path(relative)


def apply_nvidia_layer(layer: Path, record: dict[str, object], root: Path, bits: int = 64, machine: int = 62) -> dict[str, object]:
    """Lay NVIDIA's userspace into a checkout.

    Each library goes into the tree's own library folder for its word size,
    at the path it had below the device's (``gbm/``, ``vdpau/``), so the
    tree's loader, libgbm and libvdpau find it where they already look,
    whichever layout the device and the tree have. The 32-bit set is laid in
    only when the tree has a 32-bit C library. Manifests name libraries by
    soname and keep their paths."""
    own = library_dirs(root, bits, machine)[:1]
    groups = [("libraries", library_candidates(bits, machine), own)]
    libc32 = find_library(root, "libc.so.6", 32, 3)
    if libc32 is not None and (elf_dynamic(libc32) or {}).get("bits") == 32:
        groups.append(("libraries32", library_candidates(32, 3), [libc32.parent.relative_to(root)]))
    count, placed32, folders = 0, 0, set()
    for group, device_dirs, tree_dirs in groups:
        for relative in dict(record.get(group) or {}):  # type: ignore[arg-type]
            source = layer / relative
            if not source.is_file():
                continue
            target = _in_tree(relative, device_dirs, tree_dirs[0] if tree_dirs else None)
            _place(source, root / target)
            count += 1
            placed32 += group == "libraries32"
            if target.parent.name not in DRIVER_FOLDERS and target.parent not in tree_dirs:
                folders.add("/" + str(target.parent))
    for relative in record["data"]:  # type: ignore[union-attr]
        source = layer / relative
        if source.is_dir():
            count += _place_tree(source, root / relative)
        elif source.is_file():
            _place(source, root / relative)
            count += 1
    return {"route": "nvidia", "version": record.get("version", ""), "files": count, "libraries32": placed32,
            "library_dirs": sorted(folders), "ldconfig": "", "glibc_replaced": False}


# -- Choosing ----------------------------------------------------------------------

def route_plan(userland: str, store, drivers: list[str] | None = None, architecture: str = "", *,
               sys_root: Path = Path("/sys")) -> dict[str, object]:
    """Which graphics layers a tree on ``userland`` gets on this device, and why.

    {"refs": layers in the order they are applied, "route": "mesa" | "release" | "host" | "none",
     "fallback": the data calls for a Mesa layer and the tree is not getting one,
     "replaces_glibc": the tree's C library gives way to the device's,
     "nvidia": the driver release whose layer is included, "reason": in words}

    Only the store and the device are read; nothing is built. ``ensure_layers``
    is the one that fills what is missing."""
    drivers = gpu_drivers(sys_root) if drivers is None else drivers
    architecture = architecture or closure.host_architecture()
    row = mesa_source(userland)
    mesa, host = store.graphics_ref(f"mesa-{userland}", architecture), store.graphics_ref("host", architecture)
    plan: dict[str, object] = {"refs": [], "route": "none", "fallback": False, "replaces_glibc": False, "nvidia": ""}
    refs: list[str] = plan["refs"]  # type: ignore[assignment]
    if not wants_mesa_layer(row):
        plan["route"] = "release"
        reasons = [f"{userland}'s own updates carry current Mesa; no layer is kept for it."]
    elif store.has_ref(mesa):
        refs.append(mesa)
        plan["route"] = "mesa"
        reasons = [f"Mesa built for {userland} ({row.get('suite') or row['source']}) is in the store."]
    else:
        missing = (f"The Mesa layer for {userland} has not been built." if row.get("source") in BUILT_SOURCES and row.get("apt")
                   else f"There is no current Mesa for {userland} yet: Luma has to build it." if row.get("source")
                   else str(row["note"]))
        plan["fallback"] = True
        if store.has_ref(host):
            device_glibc = tuple(store.read_record(host).get("glibc_version") or ())
            tree_glibc = closure.USERLANDS[userland].glibc if userland in closure.USERLANDS else ()
            refs.append(host)
            plan["route"] = "host"
            # Without both versions, assume the usual case: the device is the newer one.
            plan["replaces_glibc"] = device_glibc > tuple(tree_glibc) if device_glibc and tree_glibc else True
            reasons = [missing, "The device's own drivers are used instead" + (
                ", and the tree's C library is replaced by the device's." if plan["replaces_glibc"] else ".")]
        else:
            reasons = [missing, "No layer of the device's drivers is in the store either: the tree runs on its own Mesa."]
    if "nvidia" in drivers:
        module = nvidia_module_version(sys_root)
        wanted = store.graphics_ref(f"nvidia-{module}", architecture)
        if not module:
            reasons.append("An adapter uses the nvidia driver, but the module's version cannot be read: no NVIDIA layer.")
        elif store.has_ref(wanted):
            refs.append(wanted)
            plan["nvidia"] = module
            reasons.append(f"NVIDIA {module} matches the loaded module.")
        else:
            others = [ref.split("/")[1].removeprefix("nvidia-") for ref in store.refs("graphics/nvidia-")
                      if ref.endswith("/" + architecture)]
            reasons.append(f"The loaded NVIDIA module is {module} and the store has no layer for it"
                           + (f" (it has {', '.join(others)})" if others else "") + ": no NVIDIA layer.")
    plan["reason"] = " ".join(reasons)
    return plan


def routes_for(userland: str, store, drivers: list[str] | None = None, architecture: str = "") -> list[str]:
    """Refs of the graphics layers a tree on ``userland`` gets on this device, in order."""
    return route_plan(userland, store, drivers, architecture)["refs"]  # type: ignore[return-value]


def ensure_layers(userland: str, store, work: Path, cache: Path, log_path: Path | None = None, *,
                  drivers: list[str] | None = None, architecture: str = "", device: Path = Path("/"),
                  sys_root: Path = Path("/sys")) -> dict[str, object]:
    """Build or stage the layers the plan for ``userland`` lacks; returns the plan as it then stands.

    Nothing here stops an application from starting: a layer that cannot be
    made is named in the plan's reason and the tree runs on what it has."""
    drivers = gpu_drivers(sys_root) if drivers is None else drivers
    architecture = architecture or closure.host_architecture()
    problems = []
    row = mesa_source(userland)
    if (wants_mesa_layer(row) and row.get("source") in BUILT_SOURCES
            and not store.has_ref(store.graphics_ref(f"mesa-{userland}", architecture))):
        try:
            if architecture != closure.host_architecture():
                raise InstallerError(f"a layer for {architecture} is built on a device of that architecture")
            build_mesa_layer(userland, store, work, cache, log_path)
        except Exception as error:  # a failed build of any kind leaves the tree on its own Mesa
            problems.append(f"Building the Mesa layer failed: {str(error).strip()[-400:] or type(error).__name__}.")
    module = nvidia_module_version(sys_root) if "nvidia" in drivers else ""
    if module and not store.has_ref(store.graphics_ref(f"nvidia-{module}", architecture)):
        try:
            work.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="graphics-nvidia-", dir=work) as staging:
                record = stage_nvidia_layer(device, Path(staging), module)
                store.commit_tree(Path(staging), store.graphics_ref(f"nvidia-{module}", architecture), record,
                                  subject=str(record["id"]))
        except Exception as error:  # the same: no NVIDIA layer, and the reason
            problems.append(f"Staging NVIDIA's libraries failed: {str(error).strip()[-400:] or type(error).__name__}")
    plan = route_plan(userland, store, drivers, architecture, sys_root=sys_root)
    plan["reason"] = " ".join([str(plan["reason"]), *problems])
    return plan
