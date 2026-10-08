# SPDX-License-Identifier: Apache-2.0

"""The map, painted in Luma's colours.

A vendor style is cartography plus a palette. The cartography — which features
appear at which zoom, how roads are classified, where labels go — is worth
keeping; the palette is not ours and does not match the rest of the system. So
the style is recoloured rather than rewritten: every paint colour is replaced
by the one the design states for that kind of feature, and everything else is
left exactly as the vendor drew it.

The design's two palettes are below, copied from the Design Center's `mp-*`
custom properties. They are the only colours Maps paints the map with.
"""

from __future__ import annotations


PALETTES = {
    "light": {
        "land": "#f2efe8", "water": "#b9d3ea", "green": "#cfe5c3",
        "block": "#f2efe8", "street": "#ffffff", "case": "#f2efe8",
        "main": "#ffffff", "motor": "#f5cf7d", "motorcase": "#f5cf7d",
        "label": "#797a7b", "halo": "#f2efe8", "route": "#4e94df",
    },
    "dark": {
        "land": "#1f2327", "water": "#16263a", "green": "#1f3327",
        "block": "#1f2327", "street": "#2b3036", "case": "#1f2327",
        "main": "#3b4148", "motor": "#6b5a3a", "motorcase": "#6b5a3a",
        "label": "#8d9091", "halo": "#1f2327", "route": "#4e94df",
    },
}

# Maps-only cartography from v70's .mpwin custom properties. These describe
# geographic content, not LumaUI chrome. The shared kit still owns every
# control, card surface and type role around the map.
SCENE_PALETTES = {
    "dark": {"land": "#1f2327", "water": "#16263a", "park": "#1f3327",
             "street": "#2b3036", "arterial": "#3b4148", "freeway": "#6b5a3a",
             "rail": "#4d4a64", "text": "#e7e9e8", "ink": "#e7e9e8"},
    "light": {"land": "#f2efe8", "water": "#b9d3ea", "park": "#cfe5c3",
              "street": "#ffffff", "arterial": "#ffffff", "freeway": "#f5cf7d",
              "rail": "#b7b1cf", "text": "#282c32", "ink": "#282c32"},
    "sat": {"land": "#2f3a2c", "water": "#10202e", "park": "#2c4a2a",
            "street": "#4a4d48", "arterial": "#6d6e67", "freeway": "#6b5a3a",
            "rail": "#4d4a64", "text": "#ffffff", "ink": "#ffffff"},
}

# Which source layers count as what. The names are openmaptiles', which is the
# schema every vendor style here is written against.
_WATER = {"water", "waterway", "ocean"}
_GREEN = {"park", "landcover", "landuse_overlay"}
_BLOCK = {"building"}


def _kind(layer: dict) -> str:
    """What the design would call this layer."""
    identifier = (layer.get("id") or "").lower()
    source_layer = (layer.get("source-layer") or "").lower()

    if layer.get("type") == "background":
        return "land"
    if source_layer in _WATER or "water" in identifier or "ocean" in identifier:
        return "water"
    if source_layer in _BLOCK or "building" in identifier:
        return "block"
    if source_layer in _GREEN or any(word in identifier for word in
                                     ("park", "wood", "grass", "forest", "scrub",
                                      "meadow", "cemetery", "pitch", "golf")):
        return "green"
    if source_layer == "landuse" or "landuse" in identifier:
        return "block"
    if source_layer in {"transportation", "transportation_name"} or "road" in identifier \
            or "bridge" in identifier or "tunnel" in identifier or "highway" in identifier:
        if "motorway" in identifier or "trunk" in identifier:
            return "motorcase" if "casing" in identifier or "case" in identifier else "motor"
        if "casing" in identifier or "case" in identifier:
            return "case"
        if "primary" in identifier or "secondary" in identifier or "main" in identifier:
            return "main"
        return "street"
    if source_layer in {"boundary"} or "boundary" in identifier or "admin" in identifier:
        return "case"
    return ""


def _flat(value) -> bool:
    """Whether a paint value is a plain colour rather than an expression.

    A stop function or an expression encodes zoom behaviour the vendor tuned;
    replacing it with one colour would flatten the map. Those are recoloured by
    substituting the colour inside, not by discarding the expression.
    """
    return isinstance(value, str)


def _substitute(value, colour: str):
    if _flat(value):
        return colour
    if isinstance(value, dict) and "stops" in value:
        return {**value, "stops": [[stop[0], colour] for stop in value["stops"]]}
    if isinstance(value, list):
        # An expression: replace only its colour literals, keeping its shape.
        return [
            colour if isinstance(item, str) and item.startswith(("#", "rgb", "hsl")) else item
            for item in value
        ]
    return colour


def recolour(style: dict, treatment: str) -> dict:
    """Return the style painted in the design's palette."""
    palette = PALETTES.get(treatment, PALETTES["dark"])
    for layer in style.get("layers", []):
        kind = _kind(layer)
        paint = layer.setdefault("paint", {})
        layer_type = layer.get("type")

        if layer_type == "background":
            paint["background-color"] = palette["land"]
            paint.pop("background-pattern", None)
            continue
        if layer_type == "symbol":
            # Labels are the design's one legible element on a quiet ground.
            paint["text-color"] = palette["label"]
            paint["text-halo-color"] = palette["halo"]
            paint.setdefault("text-halo-width", 1.1)
            continue
        if layer_type == "raster":
            paint["raster-opacity"] = 0
            continue
        if not kind:
            continue
        if layer_type == "fill":
            if "fill-color" in paint:
                paint["fill-color"] = _substitute(paint["fill-color"], palette[kind])
            else:
                paint["fill-color"] = palette[kind]
            if "fill-outline-color" in paint:
                paint["fill-outline-color"] = palette["case"]
            paint.pop("fill-pattern", None)
        elif layer_type == "line":
            if "line-color" in paint:
                paint["line-color"] = _substitute(paint["line-color"], palette[kind])
            else:
                paint["line-color"] = palette[kind]
            paint.pop("line-pattern", None)
    return style
