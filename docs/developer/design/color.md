---
title: Color
summary: Luma’s color tokens with their light and dark values, the Frost, Glass and high contrast treatments, how to use the accent and tones, and the rule against literal colors.
order: 4
section: Design
---

# Color

Color in a Luma app is **named, never written**. Your stylesheet refers to
tokens such as `@luma_ink` and `@luma_accent`; LumaUI defines them for the
current appearance and re-parses your stylesheet when the appearance changes.
Treat a literal hex or `rgba()` color in an app stylesheet as a bug.

```css
/* Good */
.notebook-card-title { color: @luma_ink; }
.notebook-card-meta  { color: @luma_muted; }
.notebook-tag        { background: @luma_accent_soft; color: @luma_accent_ink; }

/* Wrong: breaks in dark, Frost, Glass and high contrast */
.notebook-card-title { color: #283038; }
```

Load app stylesheets with `add_style_sheet()` (Python) or
`luma_ui_add_style_resource()` (C) so token changes reach them.

## Tokens

These tokens are defined in every appearance (light, dark, Frost, Glass and high
contrast) and are safe to use.

### Surfaces

| Token | Meaning | Light | Dark |
| --- | --- | --- | --- |
| `@luma_window` | The window ground, seen in the gutter | `#eceef1` | `#202124` |
| `@luma_content` | An island’s surface | `#ffffff` | `#25282d` |
| `@luma_island` | Raised fill inside an island, such as cards | `#ffffff` | white at 3.5% |
| `@luma_menu` | Menu surface | `#f6f7f8` | `#252a30` |
| `@luma_menu_raised` | Raised element inside a menu | `#ffffff` | `#30363d` |

### Ink

| Token | Meaning | Light | Dark |
| --- | --- | --- | --- |
| `@luma_ink` | Text and glyphs | `#14161a` | `#f2f3f5` |
| `@luma_muted` | Secondary text | `#6c6f73` | `#8d8f93` |
| `@luma_faint` | Placeholders and disabled text only | `#96989c` | `#616367` |

### Lines and states

In light, these are a dark grey at the opacity shown; in dark, white.

| Token | Meaning | Light | Dark |
| --- | --- | --- | --- |
| `@luma_line` | Rules, rings and hairlines | 9.5% | 8% |
| `@luma_line_faint` | Faint dividers | 8% | 6.5% |
| `@luma_fill` | Quiet fill: segments, inputs, chips | 4% | 3.8% |
| `@luma_hover` | The pointer’s fill | 6% | 6% |
| `@luma_selected` | The selected row’s fill | 10.5% | 10.8% |

### Accent and tones

| Token | Meaning | Light | Dark |
| --- | --- | --- | --- |
| `@luma_accent` | The one accent: focus, selection marks, links, active state | `#527fae` | `#80a8d2` |
| `@luma_accent_ink` | Accent used as text, with enough contrast | a darker accent, 6.5:1 on white | a lighter accent |
| `@luma_accent_link` | Link text | same as accent ink | accent mixed 38% toward white |
| `@luma_accent_soft` | A tinted background behind accent ink | accent at 14% | accent at 20% |
| `@luma_destructive` | Destructive actions and errors | `#b75048` | `#e97870` |
| `@luma_blue`, `@luma_green`, `@luma_violet`, `@luma_amber` | Identity and category tones | `#527fae`, `#3b8967`, `#865f9f`, `#a06d34` | `#80a8d2`, `#65b58c`, `#b58bd0`, `#d19447` |

LumaUI’s primary button (`.luma-button.primary`) is filled with `@luma_ink`
and labeled in `@luma_content`, not in the accent. The `@luma_primary`,
`@luma_primary_ink` and `@luma_primary_hover` tokens exist, but LumaUI’s own
controls do not use them.

`@luma_border`, `@luma_passive` and `@luma_pressed` are older aliases of
`@luma_line`, `@luma_fill` and `@luma_selected`. Use the new names.

A few other tokens are defined only in the light set (`@luma_card`,
`@luma_chrome`, `@luma_hairline`, `@luma_application_ink`). Do not use them;
in dark they keep their light values.

Use the `@luma_*` tokens rather than GTK’s or libadwaita’s named colors
(`@window_bg_color`, `@accent_color` and so on).

## Appearances

| Appearance | What changes | Your job |
| --- | --- | --- |
| **Light** | The default | Use tokens |
| **Dark** | Every token | Use tokens; check images and illustrations |
| **Frost** | A translucent window ground with a strong blur behind it; islands stay opaque | Nothing, if you use tokens and do not paint your own backgrounds over the window ground |
| **Glass** | A clearer window ground with a lighter blur | Same as Frost |
| **High contrast** | Tokens map to the system’s high contrast window, text and accent colors | Never rely on subtle fills alone to show state |

People choose Frost and Glass in Settings. LumaUI changes the treatment for
you in three cases:

- With **reduce transparency** on, Frost and Glass are painted as light.
- With **high contrast** on, the high contrast colors replace every other
  treatment.
- When the compositor cannot blur, the window keeps its Frost or Glass colors
  with nothing blurred behind it.

LumaUI follows the desktop’s light or dark choice. It also reads the portal’s
`org.freedesktop.appearance` color scheme, so a Luma app on another distribution
follows that system’s light or dark preference.

## Using color well

- **One accent per view.** It marks focus, the current selection and the single
  most important action. Do not use it for decoration.
- **Tones identify, they do not rank.** Use `@luma_blue`, `@luma_green`,
  `@luma_violet` and `@luma_amber` for categories, calendars, tags and avatars.
  `Avatar` derives a person’s tone from their name so it matches across apps.
- **Destructive color appears at the moment of commitment,** such as on hover of
  a destructive menu row or on a confirm button, not on every delete icon at rest.
- **Never use color alone** to convey meaning. Pair it with a label, icon or
  shape.
- **Contrast:** body text must reach 4.5:1 against its background and large text
  and essential glyphs 3:1. Tokens meet this in their intended pairings (ink on
  content, accent ink on accent soft); check anything else.
- **Document content keeps its own colors.** A photo, a user’s chosen text
  color, a color swatch or a video surround is data, not interface. Drawing it
  with a literal color is correct; say so in a code comment.
