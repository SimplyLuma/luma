# SPDX-License-Identifier: Apache-2.0
"""Small previews of Darkroom looks, derived from the selected photograph."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path


def render_look_preview(path: Path, values: dict[str, int], *, size: tuple[int, int] = (240, 160)) -> bytes:
    from PIL import Image, ImageEnhance, ImageOps

    from .raw import RAW_SUFFIXES, raw_thumbnail

    if path.suffix.lower() in RAW_SUFFIXES:
        image = raw_thumbnail(path)
    else:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail(size, Image.Resampling.LANCZOS)
    get = lambda key: values.get(key, 0) / 100
    brightness = 1 + get("exp") * .55 + get("sh") * .12 + get("wh") * .06 - max(0, get("hi")) * .02
    contrast = 1 + get("con") * .45 + get("cla") * .12 - get("sh") * .14 + get("hi") * .1 - get("bl") * .12 - get("fade") * .2
    saturation = max(0, 1 + get("sat") + get("vib") * .5)
    image = ImageEnhance.Brightness(image).enhance(max(0, brightness))
    image = ImageEnhance.Contrast(image).enhance(max(0, contrast))
    image = ImageEnhance.Color(image).enhance(saturation)
    for key, warm, cool, strength in (
        ("temp", (255, 150, 40), (40, 120, 255), .32),
        ("tint", (255, 40, 200), (40, 220, 90), .22),
    ):
        value = get(key)
        if value:
            overlay = Image.new("RGB", image.size, warm if value > 0 else cool)
            image = Image.blend(image, overlay, min(1, abs(value) * strength))
    if get("fade"):
        image = Image.blend(image, Image.new("RGB", image.size, (230, 225, 215)),
                            min(1, get("fade") * .22))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
