---
title: Typography
summary: Figtree, LumaUI's type roles and text styles, numbers, and how to keep text readable at every size and in every language.
order: 3
section: Design
---

# Typography

## Figtree

Luma’s interface typeface is **Figtree**, a friendly geometric sans serif
licensed under the SIL Open Font License 1.1. Luma ships version 2.0.3 as
upright and italic variable fonts with a continuous weight axis from 300 to
900, so weights such as 550 and 650 are real weights, not approximations.

Luma’s fontconfig prefers Figtree for the interface on every account, and
LumaUI sets Figtree on Luma windows. You do not need to set a font. Do not
bundle a different interface font.

Use a monospace face only for code, terminal output and fixed-width data, and
let the system choose it (`font-family: monospace`).

## Type roles

Name the role of a piece of text and LumaUI sets its size, weight and figures:
`apply_type(label, "title-1")` or `TypeLabel(text, role=)` in Python,
`luma_ui_apply_type(widget, "title-1")` in C. The core roles:

| Role | Size and weight |
| --- | --- |
| `display` | 72px, 600, tabular figures |
| `hero` | 30px, 700 |
| `title-1` | 24px, 650 |
| `title-2` | 15px, 650 |
| `lead` | 14px, 500 |
| `body` | 13px, 400 |
| `caption` | 11.5px, 500, muted |
| `label` | 11.5px, 600, muted |
| `numeric` | 30px, 500, tabular figures |

`TYPE_ROLES` lists every role, including ones for particular surfaces. Use no
one-off sizes or weights.

## Other text styles

The base size of a Luma window is **13px**. LumaUI’s stylesheets also carry
these styles:

| Use | Style | Size and weight |
| --- | --- | --- |
| Body text, list rows, controls | Window default | 13px, 400 |
| Section and card headings | `.heading` | 13px, 650 |
| Toolbar and bar titles | `luma-action-title` | 13px, 650 |
| Captions | `.caption` | 11px |
| Labels in dense panels | `luma-type-label` | 11px, 600 |
| Page titles inside content | `luma-display-title` | 19px, 700 |
| Empty-state title | `EmptyState` | 19px, 650 |
| Empty-state description | `EmptyState` | 13px, 500 |
| Sidebar section labels | `SectionLabel` | Small uppercase with tracking |
| Metadata caps | `luma-type-meta` | 9px, 700, uppercase, 0.11em tracking |
| Status bar text | `StatusBar` | 9px, 550, tabular figures |

`.dim-label` dims text without changing its size. Prefer a type role for large
text inside content. Do not restyle the `.title` class: libadwaita’s preference
rows use it.

## Guidelines

- **One size step for hierarchy is usually enough.** Pair size with weight
  rather than jumping several sizes.
- **Weight carries emphasis;** do not use color alone to emphasize text.
- **Use muted ink** (`@luma_muted`) for secondary text and **faint ink**
  (`@luma_faint`) only for placeholders and disabled text.
- **Numbers that change or align** (counts, times, sizes, prices, coordinates)
  use tabular figures: add the `numeric` class, or `font-variant-numeric:
  tabular-nums`.
- **Sentence case** for titles, headings, buttons, menu items and body
  text. See [writing style](writing-style.md).
- **No all-caps text** except LumaUI’s section labels and metadata caps.

## Long text, translations and text size

- Let labels **wrap** where there is vertical room (empty states, descriptions,
  dialogs) and **ellipsize** where there is not (sidebar rows, toolbar titles).
  Ellipsize file names in the middle.
- Design for text **up to 40% longer** than English. German, Finnish and
  Russian often need it.
- Never fix a height that clips text. Test with the system’s large text setting.
- Support **right-to-left** languages: use `start` and `end`, not `left` and
  `right`, in layout and CSS, and mirror directional icons. GTK flips most
  layouts for you.
- Do not put text in images.
