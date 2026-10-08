"""Application icons that are a logo on a disc become a rounded square.

Every application on the dock, in the app grid and in search is drawn to the
same rounded-square silhouette. Artwork published for other platforms is often
a logo on a solid circle -- Discord, Steam, many Electron applications -- which
Luma then placed on its neutral tile: a coloured coin on a grey square, beside
icons that fill their shape.

For each installed application whose icon is such a disc (a broad, opaque
silhouette with one consistent outer colour; the rules are Luma's shared
`luma_appkit.icon_assets.background_color`), a rounded square of that colour is
drawn behind the untouched artwork and published in the user's icon theme under
the application's own icon name, where it takes precedence over the original.
Artwork that is not unambiguously a disc on a solid colour is left alone.

Published plates carry a marker naming their source. They are regenerated when
the source changes and removed when the application that needed one is gone.
Icons the active Luma icon theme draws itself are never touched.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import sys
from pathlib import Path

MARKER = "X-Luma-Icon-Plate"
SIZE = 512
CORNER_RATIO = 0.25  # the radius Prairie icons and the dock mask use
ICON_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,200}")


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))


def data_dirs() -> list[Path]:
    value = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    dirs = [Path(item) for item in value.split(":") if item]
    for extra in (Path.home() / ".local/share/flatpak/exports/share", Path("/var/lib/flatpak/exports/share")):
        if extra not in dirs:
            dirs.insert(0, extra)
    return dirs


def plate_path(name: str) -> Path:
    return data_home() / "icons/hicolor/scalable/apps" / f"{name}.svg"


def is_plate(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return MARKER.encode() in stream.read(512)
    except OSError:
        return False


def themed_by_luma(name: str, theme_roots: list[Path]) -> bool:
    for root in theme_roots:
        if root.is_dir() and (any(root.glob(f"*/*/{name}.*")) or any(root.glob(f"*/{name}.*"))):
            return True
    return False


def find_source(name: str) -> Path | None:
    """The best artwork the icon theme would use for `name`, excluding plates."""
    scalable: Path | None = None
    raster: tuple[int, Path] | None = None
    for base in [data_home(), *data_dirs()]:
        hicolor = base / "icons/hicolor"
        candidate = hicolor / "scalable/apps" / f"{name}.svg"
        if scalable is None and candidate.is_file() and not is_plate(candidate):
            scalable = candidate
        if not hicolor.is_dir():
            continue
        for directory in hicolor.glob("*x*/apps"):
            match = re.fullmatch(r"(\d+)x\d+(?:@(\d+))?", directory.parent.name)
            png = directory / f"{name}.png"
            if match and png.is_file():
                size = int(match.group(1)) * int(match.group(2) or 1)
                if raster is None or size > raster[0]:
                    raster = (size, png)
    if scalable is not None:
        return scalable
    return raster[1] if raster else None


LUMA_ICON_PREFIXES = ("org.projectluma.", "io.luma.")


def gradient_rows(pixels: bytes, width: int, height: int, stride: int, channels: int) -> list[tuple[int, int, int]] | None:
    """Row colours for a disc whose rim is a top-to-bottom gradient (Steam), or None.

    The disc must be a disc: transparent corners and roughly pi/4 of the square
    opaque. Its outer edge is sampled all the way round and must follow one
    linear vertical gradient closely, so a plate of that gradient meets the
    edge without a seam.
    """
    import math
    if channels != 4 or width != height or width < 32:
        return None

    def pixel(x, y):
        offset = y * stride + x * 4
        return pixels[offset:offset + 4]

    corners = [pixel(x, y)[3] for x in (0, 1, width - 2, width - 1) for y in (0, 1, height - 2, height - 1)]
    if max(corners) >= 16:
        return None
    # Antialiased and semi-transparent edges count as covered: a disc's area is
    # about 79% of its square wherever the edge falls.
    covered = sum(pixel(x, y)[3] >= 128 for y in range(height) for x in range(width))
    if not .66 * width * height <= covered <= .88 * width * height:
        return None
    samples = []
    for ray in range(48):
        angle = ray * math.tau / 48
        for step in range(8):
            radius = .49 - step * .02
            x = min(width - 1, max(0, int((.5 + radius * math.cos(angle)) * width)))
            y = min(height - 1, max(0, int((.5 + radius * math.sin(angle)) * height)))
            value = pixel(x, y)
            if value[3] >= 200:
                samples.append((y / (height - 1), tuple(value[:3])))
                break
    if len(samples) < 44:
        return None
    def fit(points):
        count = len(points)
        mean_y = sum(t for t, _ in points) / count
        variance = sum((t - mean_y) ** 2 for t, _ in points) or 1.0
        lines = []
        for channel in range(3):
            mean_c = sum(c[channel] for _, c in points) / count
            slope = sum((t - mean_y) * (c[channel] - mean_c) for t, c in points) / variance
            lines.append((mean_c - slope * mean_y, slope))
        return lines

    def error(lines, point):
        t, c = point
        return max(abs(a + b * t - c[channel]) for channel, (a, b) in enumerate(lines))

    # Artwork that reaches the rim (Steam's arm) is not the disc: fit, set aside
    # the points furthest from the fit, and require the rest -- at least 80% of
    # the rim -- to follow it closely.
    fits = fit(samples)
    kept = sorted(samples, key=lambda point: error(fits, point))[:int(len(samples) * .8)]
    fits = fit(kept)
    if max(error(fits, point) for point in kept) > 20:
        return None
    return [tuple(max(0, min(255, round(a + b * (row / (SIZE - 1))))) for a, b in fits) for row in range(SIZE)]


def _rounded_plate(color: tuple[int, int, int] | list[tuple[int, int, int]]):
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib
    radius = SIZE * CORNER_RATIO
    row = bytearray(SIZE * 4)
    pixels = bytearray()
    for y in range(SIZE):
        row_color = color[y] if isinstance(color, list) else color
        for x in range(SIZE):
            # Distance outside the rounded corner, antialiased over one pixel.
            dx = max(radius - (x + 0.5), (x + 0.5) - (SIZE - radius), 0.0)
            dy = max(radius - (y + 0.5), (y + 0.5) - (SIZE - radius), 0.0)
            coverage = min(1.0, max(0.0, radius + 0.5 - (dx * dx + dy * dy) ** 0.5)) if dx and dy else 1.0
            offset = x * 4
            row[offset:offset + 4] = bytes((*row_color, round(255 * coverage)))
        pixels += row
    return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(pixels)), GdkPixbuf.Colorspace.RGB,
                                           True, 8, SIZE, SIZE, SIZE * 4)


def _feather_rim(artwork):
    """Fade the outermost ring of a gradient disc into the plate behind it.

    A published disc darkens or lightens its own antialiased edge; against a
    plate of the same gradient that edge would still read as a faint circle.
    """
    from gi.repository import GdkPixbuf, GLib
    stride = artwork.get_rowstride()
    pixels = bytearray(artwork.get_pixels())
    centre = SIZE / 2
    inner, outer = SIZE * .455, SIZE * .5
    for y in range(SIZE):
        dy = (y + .5 - centre) ** 2
        for x in range(SIZE):
            distance = (dy + (x + .5 - centre) ** 2) ** .5
            if distance <= inner:
                continue
            offset = y * stride + x * 4 + 3
            factor = 0.0 if distance >= outer else (outer - distance) / (outer - inner)
            pixels[offset] = round(pixels[offset] * factor)
    return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(pixels)), GdkPixbuf.Colorspace.RGB,
                                           True, 8, SIZE, SIZE, stride)


def make_plate(source: Path) -> bytes | None:
    """PNG bytes of the plated icon, or None when the artwork is not a disc."""
    try:
        from luma_appkit.icon_assets import background_color
    except ImportError:
        return None
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib
    try:
        artwork = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(source), SIZE, SIZE, True)
    except GLib.Error:
        return None
    width, height = artwork.get_width(), artwork.get_height()
    if not artwork.get_has_alpha() or abs(width - height) > max(width, height) / 10:
        return None
    sample = artwork.scale_simple(64, 64, GdkPixbuf.InterpType.HYPER)
    color = background_color(sample.get_pixels(), 64, 64, sample.get_rowstride(), sample.get_n_channels())
    if color is None:
        color = gradient_rows(sample.get_pixels(), 64, 64, sample.get_rowstride(), sample.get_n_channels())
    if color is None:
        return None
    plate = _rounded_plate(color)
    if width != SIZE or height != SIZE:
        artwork = artwork.scale_simple(SIZE, SIZE, GdkPixbuf.InterpType.HYPER)
    if isinstance(color, list):
        artwork = _feather_rim(artwork)
    artwork.composite(plate, 0, 0, SIZE, SIZE, 0, 0, 1, 1, GdkPixbuf.InterpType.HYPER, 255)
    ok, data = plate.save_to_bufferv("png", [], [])
    return bytes(data) if ok else None


def plate_svg(name: str, source: Path, png: bytes) -> str:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{SIZE}" height="{SIZE}" viewBox="0 0 {SIZE} {SIZE}">'
            f'<!-- {MARKER} source="{source}" sha256="{digest}" -->'
            f'<image width="{SIZE}" height="{SIZE}" href="data:image/png;base64,{base64.b64encode(png).decode()}"/>'
            f'</svg>\n')


def application_icon_names() -> set[str]:
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    names = set()
    for app in Gio.AppInfo.get_all():
        if not app.should_show() or not hasattr(app, "get_string"):
            continue
        icon = (app.get_string("Icon") or "").strip()
        if ICON_NAME.fullmatch(icon) and not icon.endswith("-symbolic"):
            names.add(icon)
    return names


def sync(theme_roots: list[Path] | None = None) -> list[str]:
    """Publish, refresh and retire plates. Returns the names that changed."""
    theme_roots = theme_roots if theme_roots is not None else [Path("/usr/share/icons/Prairie")]
    changed: list[str] = []
    wanted = set()
    for name in sorted(application_icon_names()):
        # Luma's own applications are drawn to the silhouette already.
        if name.startswith(LUMA_ICON_PREFIXES) or themed_by_luma(name, theme_roots):
            continue
        source = find_source(name)
        if source is None:
            continue
        target = plate_path(name)
        if target.exists() and not is_plate(target):
            continue  # the user's own icon
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if target.exists():
            head = target.read_text(encoding="utf-8", errors="replace")[:1024]
            if f'source="{source}" sha256="{digest}"' in head:
                wanted.add(name)
                continue
        png = make_plate(source)
        if png is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.partial")
        temporary.write_text(plate_svg(name, source, png), encoding="utf-8")
        temporary.replace(target)
        wanted.add(name)
        changed.append(name)
    directory = plate_path("x").parent
    if directory.is_dir():
        for existing in directory.glob("*.svg"):
            if existing.stem not in wanted and is_plate(existing):
                existing.unlink(missing_ok=True)
                changed.append(existing.stem)
    if changed:
        from .desktop import refresh_icon_cache
        refresh_icon_cache()
    return changed


def main(argv: list[str] | None = None) -> int:
    for name in sync():
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
