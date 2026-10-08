# SPDX-License-Identifier: Apache-2.0
"""Scene-linear development. Display quantization happens after tonal edits.

LibRaw owns sensor black subtraction, normalization and demosaicing. NumPy owns
float32 pixel arithmetic; Pillow resamples float planes without an 8-bit detour.
Camera RGB remains unbounded through white balance and the camera-to-sRGB matrix.
"""
from __future__ import annotations

try:
    import numpy as np
except ModuleNotFoundError:  # the host's image stack can be Pillow-only
    np = None
from PIL import Image

LIGHT_ADJUSTMENTS = frozenset({'exposure', 'contrast', 'highlights', 'shadows', 'whites', 'blacks'})
RAW_ADJUSTMENTS = LIGHT_ADJUSTMENTS | {'temperature', 'tint', 'saturation', 'vibrance', 'black-and-white', 'tone-curve'}
LUMA = np.array([.2126, .7152, .0722], dtype=np.float32) if np is not None else None


def srgb_decode(values):
    values = np.asarray(values, dtype=np.float32)
    return np.where(values <= .04045, values / 12.92, ((values + .055) / 1.055) ** 2.4)


def srgb_encode(values):
    # This is the output boundary, not an intermediate adjustment clamp.
    values = np.clip(values, 0, 1)
    return np.where(values <= .0031308, values * 12.92, 1.055 * values ** (1 / 2.4) - .055)


class LinearImage:
    def __init__(self, pixels, camera_matrix=None, alpha=None):
        self.pixels = np.asarray(pixels, dtype=np.float32)
        self.camera_matrix = camera_matrix
        self.alpha = alpha

    @property
    def size(self): return self.width, self.height
    @property
    def width(self): return self.pixels.shape[1]
    @property
    def height(self): return self.pixels.shape[0]

    def copy(self):
        return LinearImage(self.pixels.copy(), self.camera_matrix, self.alpha)

    def tobytes(self): return self.pixels.tobytes()

    def thumbnail(self, bounds):
        ratio = min(1, bounds[0] / self.width, bounds[1] / self.height)
        if ratio == 1: return
        size = (max(1, round(self.width * ratio)), max(1, round(self.height * ratio)))
        self.pixels = np.stack([np.asarray(Image.fromarray(self.pixels[..., c]).resize(size, Image.Resampling.LANCZOS)) for c in range(self.pixels.shape[2])], axis=-1)
        if self.alpha is not None: self.alpha = self.alpha.resize(size, Image.Resampling.LANCZOS)

    @classmethod
    def from_display(cls, image):
        return cls(srgb_decode(np.asarray(image.convert('RGB'), dtype=np.float32) / 255), alpha=image.getchannel('A') if image.mode == 'RGBA' else None)


class CameraFrame:
    """Immutable 16-bit demosaiced camera samples; only one source is cached."""
    def __init__(self, samples, matrix, scale):
        self.samples = samples
        self.samples.flags.writeable = False
        self.matrix = matrix
        self.scale = scale

    @property
    def size(self): return self.width, self.height
    @property
    def width(self): return self.samples.shape[1]
    @property
    def height(self): return self.samples.shape[0]

    def preview(self, maximum=None):
        ratio = min(1, maximum / max(self.size)) if maximum else 1
        size = (max(1, round(self.width * ratio)), max(1, round(self.height * ratio)))
        if ratio < 1:
            # Resize each sensor channel at floating precision before the matrix.
            pixels = np.stack([np.asarray(Image.fromarray(self.samples[..., c]).convert('F').resize(size, Image.Resampling.LANCZOS)) for c in range(self.samples.shape[2])], axis=-1)
        else: pixels = self.samples.astype(np.float32)
        pixels *= np.float32(self.scale / 65535)
        return LinearImage(pixels, self.matrix)


def _retone(rgb, luminance, target):
    gain = np.divide(target, luminance, out=np.ones_like(target), where=luminance > 1e-8)
    rgb *= gain[..., None]
    rgb[luminance <= 1e-8] += target[luminance <= 1e-8, None]
    return rgb


def develop_linear(source: LinearImage, adjustments):
    """Return unclipped linear RGB. +1 EV doubles radiance, not sRGB codes.

    Highlight compression is a monotonic luminance shoulder above middle grey;
    negative values compress existing detail, they do not reconstruct saturated
    sensor samples. Shadow gain is confined to the toe and fixes absolute black.
    Whites moves the upper range; positive Blacks lifts the black point.
    """
    values = {a.kind: a.value for a in adjustments if a.enabled}
    rgb = source.pixels.copy()
    if source.camera_matrix is not None:
        temperature, tint = float(values.get('temperature', 0)), float(values.get('tint', 0))
        if temperature or tint:
            # Relative adjustment of as-shot camera WB, not a Kelvin estimator.
            gains = np.array([2 ** (temperature / 200), 2 ** (-tint / 200), 2 ** (-temperature / 200), 2 ** (-tint / 200)], dtype=np.float32)
            rgb *= gains[:rgb.shape[2]]
        rgb = np.einsum('...c,rc->...r', rgb, source.camera_matrix, optimize=False)
    rgb *= np.float32(2 ** float(values.get('exposure', 0)))
    light = any(values.get(key, 0) for key in LIGHT_ADJUSTMENTS - {'exposure'})
    if light:
        luminance = np.maximum(np.einsum('...c,c->...', rgb, LUMA), 0)
        target = luminance.copy()
        contrast = float(values.get('contrast', 0))
        if contrast: target = .18 * (target / .18) ** (2 ** (contrast / 100))
        shadows = float(values.get('shadows', 0))
        if shadows: target *= np.exp2((shadows / 50) * np.maximum(1 - target / .35, 0) ** 2)
        highlights = float(values.get('highlights', 0))
        if highlights:
            excess = np.maximum(target - .18, 0)
            strength = abs(highlights) / 25
            adjusted = excess / (1 + strength * excess) if highlights < 0 else excess * (1 + strength * excess / (1 + excess))
            target += adjusted - excess
        whites = float(values.get('whites', 0))
        if whites: target *= np.exp2((whites / 100) * target / (target + .5))
        blacks = float(values.get('blacks', 0))
        if blacks: target += (blacks / 2500) * np.maximum(1 - target / .25, 0) ** 2
        rgb = _retone(rgb, luminance, np.maximum(target, 0))
    saturation, vibrance = float(values.get('saturation', 0)), float(values.get('vibrance', 0))
    grayscale = values.get('black-and-white', False)
    if saturation or vibrance or grayscale:
        gray = np.einsum('...c,c->...', rgb, LUMA)[..., None]
        if grayscale: rgb = np.repeat(gray, 3, axis=-1)
        else:
            factor = max(0, 1 + saturation / 100)
            if vibrance:
                peak = np.maximum(rgb.max(axis=-1), 1e-6)
                chroma = np.clip((peak - rgb.min(axis=-1)) / peak, 0, 1)
                factor = np.maximum(0, factor + vibrance / 100 * (1 - chroma))[..., None]
            rgb = gray + (rgb - gray) * factor
    return rgb


def develop(source, adjustments):
    # Process output in strips, bounding scratch buffers during full-size export.
    adjustments = list(adjustments)
    curve = next((a.value for a in adjustments if a.enabled and a.kind == 'tone-curve'), None)
    points = sorted((max(0., min(1., float(x))), max(0., min(1., float(y)))) for x, y in curve) if curve else []
    if points:
        if points[0][0] > 0: points.insert(0, (0., points[0][1]))
        if points[-1][0] < 1: points.append((1., points[-1][1]))
        curve_x, curve_y = zip(*points)
    output = np.empty((source.height, source.width, 3), dtype=np.uint8)
    for top in range(0, source.height, 256):
        strip = LinearImage(source.pixels[top:top+256], source.camera_matrix)
        rgb = develop_linear(strip, adjustments)
        encoded = srgb_encode(rgb)
        if points: encoded = np.interp(encoded, curve_x, curve_y)
        output[top:top+256] = np.rint(encoded * 255).astype(np.uint8)
    image = Image.fromarray(output).convert('RGBA')
    if source.alpha is not None: image.putalpha(source.alpha)
    return image
