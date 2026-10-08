# SPDX-License-Identifier: Apache-2.0
"""Token-derived Weather gradients interpolated in CSS's Oklab space.

Conversion matrices: https://bottosson.github.io/posts/oklab/ (public domain).
These are color-space coefficients, not app palette values.
"""
import math


def _lab(rgb):
    r, g, b = (v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb)
    l = math.cbrt(.4122214708*r + .5363325363*g + .0514459929*b)
    m = math.cbrt(.2119034982*r + .6806995451*g + .1073969566*b)
    s = math.cbrt(.0883024619*r + .2817188376*g + .6299787005*b)
    return (.2104542553*l + .7936177850*m - .0040720468*s,
            1.9779984951*l - 2.4285922050*m + .4505937099*s,
            .0259040371*l + .7827717662*m - .8086757660*s)


def _rgb(lab):
    lightness, a, b = lab
    l = (lightness + .3963377774*a + .2158037573*b) ** 3
    m = (lightness - .1055613458*a - .0638541728*b) ** 3
    s = (lightness - .0894841775*a - 1.2914855480*b) ** 3
    linear = (4.0767416621*l - 3.3077115913*m + .2309699292*s,
              -1.2684380046*l + 2.6097574011*m - .3413193965*s,
              -.0041960863*l - .7034186147*m + 1.7076147010*s)
    return tuple(max(0, min(1, 12.92*v if v <= .0031308 else 1.055*v**(1/2.4) - .055))
                 for v in linear)


def gradient_stops(stops):
    """Expand opaque kit RGBA stops for renderers limited to sRGB gradients.

    Thirty-two intervals per segment keep the piecewise sRGB approximation
    smooth while preserving each original token and its exact position.
    Weather sky and instrument gradients all use opaque stops.
    """
    result = []
    for (start, left), (end, right) in zip(stops, stops[1:]):
        left_lab, right_lab = _lab(left[:3]), _lab(right[:3])
        for step in range(32):
            t = step / 32
            rgba = left if not step else (*_rgb(tuple(a + (b-a)*t for a, b in zip(left_lab, right_lab))),
                                         left[3] + (right[3]-left[3])*t)
            result.append((start + (end-start)*t, rgba))
    if stops:
        result.append(stops[-1])
    return result
