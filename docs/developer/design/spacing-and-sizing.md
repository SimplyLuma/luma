---
title: Spacing and sizing
summary: A spacing scale for your own content, the window gutter, corner radii, control heights, touch targets, row heights and widths used across Luma.
order: 5
section: Design
---

# Spacing and sizing

All measurements are logical pixels at 100% scale. LumaUI components already
size themselves; use these values for your own content so it sits naturally
beside them.

## Spacing scale

LumaUI has no spacing tokens. These steps match what its components use, so
use them for your own layouts.

| Step | Value | Typical use |
| --- | --- | --- |
| 1 | 4px | Icon to label inside a compact control, tight stacks |
| 2 | 6px | Controls inside a connected group, list insets |
| 3 | 8px | Between controls in a toolbar, between related rows |
| 4 | 12px | Padding inside cards and details sections |
| 5 | 16px | Between groups of content |
| 6 | 24px | Around empty states and large content blocks |

The 8px gutter around and between islands belongs to LumaUI
(`--lumaui-window-gutter`); do not add to it.

Avoid values in between the steps. Spacing that separates groups should be
visibly larger than spacing inside a group.

## Radii

| Element | Radius |
| --- | --- |
| Window | 20px (square only in full screen) |
| Island | 12px |
| Buttons, inputs, menu rows | 10px, owned by LumaUI |
| Application icon tile | About a quarter of the tile’s width, owned by LumaUI |
| Avatar | Soft square, owned by `Avatar` |

Do not set a radius on anything LumaUI draws. For your own content inside an
island, such as cards, use a radius smaller than the island’s 12px so corners
nest.

## Controls and targets

| Size | Height | Use |
| --- | --- | --- |
| Compact | 28px | Icon buttons, toolbar buttons, small text buttons, text fields |
| Standard | 36px | Text buttons (`.luma-button`), dialog buttons, menu rows |
| Touch | 44px | Minimum target with touch input |

The title row is 46px; window controls are 30px squares.

With touch input, every interactive target must be at least 44 by 44px, even if
the visible control is smaller. Extend the hit area with padding rather than
drawing a larger control.

Icon sizes are on the [icons](icons.md) page.

## Widths

| Element | Width |
| --- | --- |
| Navigation sidebar | 272px; 236px when the window is under 900px wide |
| Details pane | 288px |
| Adaptive scaffold sidebar (C) | 180px once you set a width, limited to 160 to 320px by default |
| Empty-state text | At most 320px (260px compact) |
| App template minimum window | 360 by 294px |

LumaUI’s layout tiers change at 560px (phone below) and 900px (regular above).
