# SPDX-License-Identifier: Apache-2.0
"""Real raster preview and export pipeline for Darkroom.

LibRaw and NumPy preserve linear camera data through RAW development; Pillow
provides raster decoding, spatial finishing and delivery encoding. The recipe
remains independent of this renderer so a tiled backend can replace it without
changing saved documents.
"""

from __future__ import annotations

import io
import math
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .model import Adjustment, Document, ExportPreset, Layer, Mask, file_path
from .raw import RAW_SUFFIXES, RawDecodeError, decode_raw
from .develop import LinearImage, LIGHT_ADJUSTMENTS, RAW_ADJUSTMENTS, develop, np


# Highlight protection for white balance: from this fraction of full scale in
# every channel a pixel is treated as clipped white, fully so at full scale.
_CLIPPED_WHITE_FROM = 0.92


def _white_balance(image, temperature: float, tint: float):
    """Relative colour balance for already-rendered RGB, protecting near-white.

    This is a display-referred adjustment for raster sources, not camera WB.
    RAW white balance is applied separately in develop.py to linear camera
    channels before the colour matrix and display transfer function.
    """
    from PIL import Image, ImageMath

    red_gain = 1.0 + temperature / 200.0
    green_gain = 1.0 - tint / 250.0
    blue_gain = 1.0 - temperature / 200.0
    red, green, blue, alpha = image.split()
    channels = {"r": red.convert("F"), "g": green.convert("F"), "b": blue.convert("F")}

    def balanced(own: str, gain: float):
        def expression(x):
            low = x["min"](x["min"](x["r"], x["g"]), x["b"]) / 255.0
            hold = x["max"](x["min"]((low - _CLIPPED_WHITE_FROM) / (1.0 - _CLIPPED_WHITE_FROM), 1.0), 0.0)
            hold = hold * hold * (3.0 - 2.0 * hold)
            peak = x["max"](x["max"](x["r"] * red_gain, x["g"] * green_gain), x["b"] * blue_gain) / 255.0
            over = x["max"](peak, 1.0)
            clipped = x["min"](x[own] * gain / 255.0, 1.0)
            rolled = clipped + (1.0 - clipped) * (1.0 - 1.0 / over)
            result = rolled * (1.0 - hold) + (x[own] / 255.0) * hold
            return x["max"](x["min"](result * 255.0 + 0.5, 255.0), 0.0)
        return ImageMath.lambda_eval(expression, **channels).convert("L")

    return Image.merge("RGBA", (balanced("r", red_gain), balanced("g", green_gain), balanced("b", blue_gain), alpha))


class ImagingUnavailable(RuntimeError):
    pass


class RenderError(RuntimeError):
    pass


def load_pillow():
    try:
        from PIL import Image, ImageChops, ImageCms, ImageDraw, ImageEnhance, ImageFilter, ImageOps
    except ImportError as error:  # pragma: no cover - depends on host image
        raise ImagingUnavailable("Darkroom requires the Pillow imaging stack") from error
    return Image, ImageChops, ImageCms, ImageDraw, ImageEnhance, ImageFilter, ImageOps


@dataclass(frozen=True, slots=True)
class RenderResult:
    image: object
    histogram: tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]
    source_profile: str
    output_profile: str
    warnings: tuple[str, ...] = ()


def _value(adjustments: Iterable[Adjustment], kind: str, default=0.0):
    adjustment = next((item for item in adjustments if item.kind == kind and item.enabled), None)
    return default if adjustment is None else adjustment.value


def _curve_lut(points) -> list[int]:
    pairs = sorted((max(0.0, min(1.0, float(x))), max(0.0, min(1.0, float(y)))) for x, y in points)
    if not pairs:
        pairs = [(0.0, 0.0), (1.0, 1.0)]
    if pairs[0][0] > 0.0:
        pairs.insert(0, (0.0, pairs[0][1]))
    if pairs[-1][0] < 1.0:
        pairs.append((1.0, pairs[-1][1]))
    result: list[int] = []
    index = 0
    for sample in range(256):
        x = sample / 255.0
        while index + 1 < len(pairs) - 1 and x > pairs[index + 1][0]:
            index += 1
        x0, y0 = pairs[index]
        x1, y1 = pairs[index + 1]
        amount = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
        result.append(round(max(0.0, min(1.0, y0 + (y1 - y0) * amount)) * 255))
    return result


class RasterEngine:
    def __init__(self, document: Document) -> None:
        self.document = document

    def open_source(self, max_dimension: int | None = None):
        Image, _, ImageCms, _, _, _, ImageOps = load_pillow()
        path = file_path(self.document.source.uri)
        if path is None:
            raise RenderError("The current renderer can open local and portal-backed files only")
        if not path.exists():
            raise RenderError(f"The original image is missing: {path.name}")
        try:
            if self.document.source.raw or path.suffix.lower() in RAW_SUFFIXES:
                frame = decode_raw(path)
                self.document.source.width, self.document.source.height = frame.size
                self.document.source.bit_depth = 16
                return frame.preview(max_dimension), "Camera RAW (linear)"
            else:
                image = Image.open(path)
                image.load()
                image = ImageOps.exif_transpose(image)
            self.document.source.width, self.document.source.height = image.size
        except Exception as error:
            if isinstance(error, RawDecodeError):
                raise RenderError(str(error)) from error
            raise RenderError(f"Could not decode {path.name}: {error}") from error
        source_profile = self.document.source.embedded_profile or "Unspecified"
        icc = image.info.get("icc_profile")
        if icc:
            try:
                input_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                output_profile = ImageCms.createProfile("sRGB")
                image = ImageCms.profileToProfile(image, input_profile, output_profile, outputMode="RGBA")
                source_profile = ImageCms.getProfileName(input_profile).strip() or source_profile
            except Exception:
                image = image.convert("RGBA")
        else:
            image = image.convert("RGBA")
        if max_dimension and max(image.size) > max_dimension:
            scale = max_dimension / max(image.size)
            image.thumbnail((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
        return image, source_profile

    def render(self, max_dimension: int | None = 2048, *, original: bool = False, crop: bool = True, prepared=None) -> RenderResult:
        """The edited image; crop=False keeps the whole frame, for framing a crop."""
        image, source_profile = prepared if prepared is not None else self.open_source(max_dimension)
        image = image.copy() if prepared is not None and not isinstance(image, LinearImage) else image
        warnings: list[str] = []
        if original and isinstance(image, LinearImage): image = develop(image, [])
        if not original:
            image = self._apply_adjustments(image, self.document.raw_development)
            if crop:
                image = self._apply_crop(image)
            image = self._composite_layers(image, self.document.layers[1:])
            image = self._apply_retouch(image)
        histogram = self.histogram(image)
        if self.document.working_space != "sRGB":
            warnings.append(f"Preview is display-converted from {self.document.working_space} through sRGB")
        return RenderResult(image, histogram, source_profile, self.document.working_space, tuple(warnings))

    def _apply_adjustments(self, image, adjustments: Iterable[Adjustment]):
        Image, ImageChops, _, _, ImageEnhance, ImageFilter, ImageOps = load_pillow()
        adjustments = list(adjustments)
        if isinstance(image, LinearImage):
            image = develop(image, adjustments)
            adjustments = [a for a in adjustments if a.kind not in RAW_ADJUSTMENTS]
        elif any(a.enabled and a.kind in LIGHT_ADJUSTMENTS and a.value for a in adjustments):
            if np is None:
                image = self._pillow_light_fallback(image, adjustments)
            else:
                image = develop(LinearImage.from_display(image), [a for a in adjustments if a.kind in LIGHT_ADJUSTMENTS])
            adjustments = [a for a in adjustments if a.kind not in LIGHT_ADJUSTMENTS]
        temperature = float(_value(adjustments, "temperature", 0.0))
        tint = float(_value(adjustments, "tint", 0.0))
        if temperature or tint:
            image = _white_balance(image, temperature, tint)
        saturation = float(_value(adjustments, "saturation", 0.0))
        vibrance = float(_value(adjustments, "vibrance", 0.0))
        if saturation or vibrance:
            image = ImageEnhance.Color(image).enhance(max(0.0, 1.0 + saturation / 100.0 + vibrance / 180.0))
        clarity = float(_value(adjustments, "clarity", 0.0))
        texture = float(_value(adjustments, "texture", 0.0))
        if clarity or texture:
            if np is None:
                if clarity > 0 or texture > 0:
                    image = image.filter(ImageFilter.UnsharpMask(
                        radius=max(1, round(max(image.size) / 300)),
                        percent=min(300, round(max(clarity, texture) * 2)), threshold=2))
                else:
                    blurred = image.filter(ImageFilter.GaussianBlur(radius=1.5))
                    image = Image.blend(image, blurred, min(1.0, -min(clarity, texture) / 100))
            else:
                pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
                result = pixels.copy()
                # Signed unsharp detail: flat regions do not change brightness.
                for amount, radius in ((texture, 2.0), (clarity, 18.0)):
                    if amount:
                        blurred = np.asarray(image.convert("RGB").filter(ImageFilter.GaussianBlur(radius=radius * max(image.size) / 1800)), dtype=np.float32)
                        result += (amount / 100) * (pixels - blurred)
                alpha = image.getchannel("A")
                image = Image.fromarray(np.rint(np.clip(result, 0, 255)).astype(np.uint8)).convert("RGBA")
                image.putalpha(alpha)
        dehaze = float(_value(adjustments, "dehaze", 0.0))
        # Preserve older recipes; this global-contrast approximation is no
        # longer offered as a Dehaze control in the photo inspector.
        if dehaze:
            image = ImageEnhance.Contrast(image).enhance(max(0.0, 1.0 + dehaze / 140.0))
        curve = _value(adjustments, "tone-curve", None)
        if curve:
            lut = _curve_lut(curve)
            r, g, b, a = image.split()
            image = Image.merge("RGBA", (r.point(lut), g.point(lut), b.point(lut), a))
        sharpen = float(_value(adjustments, "sharpening", 0.0))
        if sharpen > 0:
            image = image.filter(ImageFilter.UnsharpMask(radius=1.4, percent=min(500, round(sharpen * 3)), threshold=2))
        noise = float(_value(adjustments, "noise-reduction", 0.0))
        if noise > 0:
            image = image.filter(ImageFilter.MedianFilter(3 if noise < 60 else 5))
        grayscale = bool(_value(adjustments, "black-and-white", False))
        if grayscale:
            alpha = image.getchannel("A")
            image = ImageOps.grayscale(image).convert("RGBA")
            image.putalpha(alpha)
        return image

    @staticmethod
    def _pillow_light_fallback(image, adjustments):
        """Keep raster editing usable where the optional NumPy stack is absent.

        RAW development still requires NumPy to preserve scene-linear samples;
        this branch is only for already decoded display RGB photographs.
        """
        Image, _, _, _, ImageEnhance, _, _ = load_pillow()
        exposure = float(_value(adjustments, "exposure", 0.0))
        contrast = float(_value(adjustments, "contrast", 0.0))
        highlights = float(_value(adjustments, "highlights", 0.0))
        shadows = float(_value(adjustments, "shadows", 0.0))
        whites = float(_value(adjustments, "whites", 0.0))
        blacks = float(_value(adjustments, "blacks", 0.0))
        if exposure:
            image = ImageEnhance.Brightness(image).enhance(2 ** exposure)
        if contrast:
            image = ImageEnhance.Contrast(image).enhance(max(0.0, 1 + contrast / 120))
        if any((highlights, shadows, whites, blacks)):
            def tone(value):
                x = value / 255
                x += shadows / 150 * x * (1 - x) ** 2
                x += highlights / 150 * x * x * (1 - x)
                x += whites / 200 * x ** 3
                x += blacks / 200 * (1 - x) ** 3
                return round(max(0.0, min(1.0, x)) * 255)
            lut = [tone(value) for value in range(256)]
            red, green, blue, alpha = image.convert("RGBA").split()
            image = Image.merge("RGBA", (red.point(lut), green.point(lut), blue.point(lut), alpha))
        return image

    def _apply_crop(self, image):
        Image, _, _, _, _, _, _ = load_pillow()
        crop = self.document.crop
        if crop.flip_horizontal:
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if crop.flip_vertical:
            image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        angle = crop.rotation + crop.straighten
        if angle:
            image = image.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True)
        left = round(crop.left * image.width)
        top = round(crop.top * image.height)
        right = round(crop.right * image.width)
        bottom = round(crop.bottom * image.height)
        return image.crop((left, top, max(left + 1, right), max(top + 1, bottom)))

    def _composite_layers(self, base, layers: Iterable[Layer]):
        Image, ImageChops, _, _, _, _, _ = load_pillow()
        result = base
        for layer in layers:
            if not layer.visible:
                continue
            if layer.kind == "adjustment":
                adjusted = self._apply_adjustments(result.copy(), layer.adjustments)
                mask = self._layer_mask(layer, result.size)
                if layer.opacity < 1.0:
                    mask = mask.point(lambda value: round(value * layer.opacity))
                result = Image.composite(adjusted, result, mask)
                continue
            if layer.kind == "group":
                result = self._composite_layers(result, layer.children)
                continue
            if layer.kind == "fill":
                color = str(_value(layer.adjustments, "color", "#000000"))
                overlay = Image.new("RGBA", result.size, color)
            elif layer.source_uri:
                path = file_path(layer.source_uri)
                if path is None or not path.exists():
                    layer.missing_resource = True
                    continue
                try:
                    overlay = Image.open(path).convert("RGBA")
                except Exception:
                    layer.missing_resource = True
                    continue
                overlay.thumbnail(result.size, Image.Resampling.LANCZOS)
                positioned = Image.new("RGBA", result.size)
                positioned.alpha_composite(overlay, (round(layer.transform.x * result.width), round(layer.transform.y * result.height)))
                overlay = positioned
            else:
                continue
            overlay = self._apply_adjustments(overlay, layer.adjustments)
            alpha = overlay.getchannel("A")
            mask = self._layer_mask(layer, result.size)
            alpha = ImageChops.multiply(alpha, mask).point(lambda value: round(value * layer.opacity))
            overlay.putalpha(alpha)
            result = self._blend(result, overlay, layer.blend_mode)
        return result

    def _layer_mask(self, layer: Layer, size):
        Image, ImageChops, _, _, _, ImageFilter, _ = load_pillow()
        accumulated = Image.new("L", size, 255)
        first = True
        for mask in layer.masks:
            if not mask.enabled:
                continue
            current = self._mask_image(mask, size)
            if mask.feather:
                current = current.filter(ImageFilter.GaussianBlur(radius=max(size) * mask.feather * 0.04))
            if mask.inverted:
                current = current.point(lambda value: 255 - value)
            current = current.point(lambda value: round(value * mask.density))
            if first or mask.operation == "add":
                accumulated = current if first else ImageChops.lighter(accumulated, current)
            elif mask.operation == "subtract":
                accumulated = ImageChops.subtract(accumulated, current)
            else:
                accumulated = ImageChops.multiply(accumulated, current)
            first = False
        return accumulated

    def _mask_image(self, mask: Mask, size):
        Image, _, _, ImageDraw, _, _, _ = load_pillow()
        width, height = size
        if mask.kind == "linear-gradient":
            start = float(mask.geometry.get("start", 0.0))
            end = float(mask.geometry.get("end", 1.0))
            difference = end - start
            denominator = difference if abs(difference) >= 0.0001 else 0.0001
            vertical = mask.geometry.get("axis") == "y"
            length = height if vertical else width
            line = [round(max(0.0, min(1.0, (position / max(1, length - 1) - start)
                                                 / denominator)) * 255) for position in range(length)]
            output = Image.new("L", (1, height) if vertical else (width, 1))
            output.putdata(line)
            return output.resize(size)
        if mask.kind == "radial-gradient":
            cx = float(mask.geometry.get("x", 0.5)) * width
            cy = float(mask.geometry.get("y", 0.5)) * height
            radius = max(1.0, float(mask.geometry.get("radius", 0.35)) * max(size))
            data = []
            for y in range(height):
                for x in range(width):
                    data.append(round(max(0.0, 1.0 - math.hypot(x - cx, y - cy) / radius) * 255))
            output = Image.new("L", size)
            output.putdata(data)
            return output
        if mask.kind == "brush":
            output = Image.new("L", size, 0)
            draw = ImageDraw.Draw(output)
            radius = max(1, round(float(mask.geometry.get("size", 0.05)) * max(size) / 2))
            for point in mask.geometry.get("points", []):
                if not isinstance(point, list) or len(point) < 2:
                    continue
                x, y = round(float(point[0]) * width), round(float(point[1]) * height)
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)
            return output
        # Brush and automatic masks use persisted normalized coverage if the
        # backend produced it; otherwise they are explicitly empty, never fake.
        coverage = mask.geometry.get("coverage")
        if isinstance(coverage, list) and len(coverage) == width * height:
            output = Image.new("L", size)
            output.putdata([max(0, min(255, int(value))) for value in coverage])
            return output
        return Image.new("L", size, 0)

    @staticmethod
    def _blend(base, overlay, mode: str):
        Image, ImageChops, _, _, _, _, _ = load_pillow()
        alpha = overlay.getchannel("A")
        if mode == "multiply":
            mixed = ImageChops.multiply(base, overlay)
        elif mode == "screen":
            mixed = ImageChops.screen(base, overlay)
        elif mode == "difference":
            mixed = ImageChops.difference(base, overlay)
        elif mode == "darken":
            mixed = ImageChops.darker(base, overlay)
        elif mode == "lighten":
            mixed = ImageChops.lighter(base, overlay)
        elif mode in {"overlay", "soft-light", "hard-light"} and hasattr(ImageChops, "overlay"):
            mixed = ImageChops.overlay(base, overlay)
        else:
            mixed = overlay
        return Image.composite(mixed, base, alpha)

    def _apply_retouch(self, image):
        Image, _, _, ImageDraw, _, ImageFilter, _ = load_pillow()
        result = image
        for operation in self.document.retouch:
            if not operation.enabled:
                continue
            radius = max(1, round(operation.size * max(result.size) / 2))
            for point in operation.points:
                x, y = round(point[0] * result.width), round(point[1] * result.height)
                box = (max(0, x - radius), max(0, y - radius), min(result.width, x + radius), min(result.height, y + radius))
                if box[2] <= box[0] or box[3] <= box[1]:
                    continue
                if operation.kind == "clone" and operation.source:
                    sx, sy = round(operation.source[0] * result.width), round(operation.source[1] * result.height)
                    source_box = (max(0, sx - radius), max(0, sy - radius), min(result.width, sx + radius), min(result.height, sy + radius))
                    patch = result.crop(source_box).resize((box[2] - box[0], box[3] - box[1]))
                else:
                    expanded = (max(0, box[0] - radius), max(0, box[1] - radius), min(result.width, box[2] + radius), min(result.height, box[3] + radius))
                    patch = result.crop(expanded).filter(ImageFilter.GaussianBlur(radius=max(1, radius / 2))).crop((box[0] - expanded[0], box[1] - expanded[1], box[2] - expanded[0], box[3] - expanded[1]))
                patch = patch.convert("RGBA")
                mask = Image.new("L", patch.size, 0)
                draw = ImageDraw.Draw(mask)
                inset = max(0, round(radius * operation.feather * 0.35))
                draw.ellipse((inset, inset, max(inset + 1, patch.width - inset), max(inset + 1, patch.height - inset)), fill=255)
                if operation.feather:
                    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(1, radius * operation.feather * 0.45)))
                if operation.opacity < 1.0:
                    mask = mask.point(lambda value: round(value * operation.opacity))
                result.paste(patch, (box[0], box[1]), mask)
        return result

    @staticmethod
    def histogram(image) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
        Image, _, _, _, _, _, ImageOps = load_pillow()
        rgb = image.convert("RGB")
        values = rgb.histogram()
        red, green, blue = tuple(values[:256]), tuple(values[256:512]), tuple(values[512:768])
        luminance = tuple(ImageOps.grayscale(rgb).histogram())
        return luminance, red, green, blue

    @staticmethod
    def encode_preview(image, format: str = "PNG") -> bytes:
        stream = io.BytesIO()
        image.save(stream, format=format)
        return stream.getvalue()


class ExportJob:
    def __init__(self, document: Document, destination: Path, preset: ExportPreset, *, overwrite: bool = False, progress: Callable[[float, str], None] | None = None, completed: Callable[[Path | None, str | None], None] | None = None) -> None:
        self.document = document.clone()
        self.destination = destination
        self.preset = preset
        self.overwrite = overwrite
        self.progress_callback = progress
        self.completed_callback = completed
        self._cancelled = threading.Event()
        self._finished = threading.Event()
        self._thread: threading.Thread | None = None
        self.result: Path | None = None
        self.error: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RenderError("this export has already started")
        self._thread = threading.Thread(target=self._run, name="darkroom-export", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancelled.set()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for encoder completion without requiring a GLib main loop."""
        return self._finished.wait(timeout)

    def _emit(self, callback, *args) -> None:
        if callback is None:
            return
        try:
            from gi.repository import GLib
        except ImportError:
            callback(*args)
        else:
            GLib.idle_add(callback, *args)

    def _run(self) -> None:
        temporary = self.destination.with_name(f".{self.destination.name}.{uuid.uuid4().hex[:8]}.darkroom-export.tmp")
        try:
            self.preset.validate()
            if self.preset.bit_depth == 16:
                raise RenderError("16-bit delivery encoding is not available. Choose 8-bit export; RAW development retains 16-bit source precision.")
            if self.destination.exists() and not self.overwrite:
                raise RenderError(f"The destination already exists: {self.destination.name}")
            self._emit(self.progress_callback, 0.08, "Decoding original")
            rendered = RasterEngine(self.document).render(max_dimension=None)
            if self._cancelled.is_set():
                raise RenderError("Export cancelled")
            image = rendered.image
            width = self.preset.width or round(image.width * self.preset.scale)
            height = self.preset.height or round(image.height * self.preset.scale)
            if (width, height) != image.size:
                Image, _, _, _, _, _, _ = load_pillow()
                image = image.resize((max(1, width), max(1, height)), Image.Resampling.LANCZOS)
            if self._cancelled.is_set():
                raise RenderError("Export cancelled")
            self._emit(self.progress_callback, 0.62, "Rendering full quality")
            self.destination.parent.mkdir(parents=True, exist_ok=True)
            save_image = image.convert("RGB") if self.preset.format in {"JPEG", "PDF"} else image
            options = {"format": self.preset.format, "dpi": (self.preset.resolution, self.preset.resolution)}
            if self.preset.format in {"JPEG", "WEBP"}:
                options["quality"] = self.preset.quality
            options.update(self._export_metadata())
            save_image.save(temporary, **options)
            if self._cancelled.is_set():
                raise RenderError("Export cancelled")
            if not temporary.exists() or temporary.stat().st_size == 0:
                raise RenderError("The encoder produced no image")
            os.replace(temporary, self.destination)
            self.result = self.destination
            self.error = None
            self._finished.set()
            self._emit(self.progress_callback, 1.0, "Complete")
            self._emit(self.completed_callback, self.destination, None)
        except Exception as error:
            temporary.unlink(missing_ok=True)
            self.result = None
            self.error = str(error)
            self._finished.set()
            self._emit(self.completed_callback, None, self.error)

    def _export_metadata(self) -> dict:
        """Return encoder metadata without ever carrying GPS in privacy mode."""
        if self.preset.format == "PDF":
            return {}
        Image, _, ImageCms, _, _, _, _ = load_pillow()
        options: dict = {}
        try:
            srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
            options["icc_profile"] = srgb.tobytes()
        except Exception:
            pass
        if self.preset.metadata == "none":
            return options
        path = file_path(self.document.source.uri)
        if path is None:
            return options
        try:
            with Image.open(path) as source:
                exif = source.getexif()
                if self.preset.metadata == "all" and exif:
                    # Pixels have already been transposed into display orientation.
                    exif[274] = 1
                    options["exif"] = exif.tobytes()
                elif self.preset.metadata == "copyright" and exif:
                    safe = Image.Exif()
                    # ImageDescription, Artist, Copyright, and XPAuthor.
                    for tag in (270, 315, 33432, 40093):
                        if tag in exif:
                            safe[tag] = exif[tag]
                    if safe:
                        options["exif"] = safe.tobytes()
        except Exception:
            pass
        return options
