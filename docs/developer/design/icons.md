---
title: Icons
summary: Designing your app icon for Luma’s tile, using Prairie’s Lucide-style symbolic icons, drawing your own symbolic icons in the right format, and sizes.
order: 6
section: Design
---

# Icons

## Your app icon

Your app icon appears in the dock, the app grid, Depot, the identity in your
window’s corner, and search. Luma presents application artwork on a square tile
with a corner radius of a quarter of the tile’s width, a subtle top-to-bottom
light gradient and a faint outline.

Design it so:

- it reads clearly at **16px** (the window identity is 22px) and still looks
  good at **256px**;
- the main shape is simple, recognizable and distinct from system apps;
- it sits well inside a rounded square: a full-bleed tile with its own color, or
  a shape on a transparent background with enough margin;
- it has **no baked-in drop shadow, glow or extra frame**, because Luma adds the
  tile treatment;
- it contains no text, apart from a single letterform if that is your brand;
- it is not confusingly similar to another app’s icon or to a Luma system icon.

Ship a **scalable SVG** named with your app ID:

```text
/app/share/icons/hicolor/scalable/apps/com.example.Notebook.svg
```

Keep the SVG self-contained: no external references, scripts or embedded
raster images. If your artwork only exists as a raster, also ship 256px and
512px PNGs in `hicolor/256x256/apps` and `hicolor/512x512/apps`.

The `Icon=` key in your desktop entry must be your app ID.

## Symbolic icons

Interface icons are **symbolic**: single-color glyphs that GTK recolors to match
the text around them, so they work in light, dark and high contrast
automatically. Luma’s **Prairie** icon theme provides them, and falls back to
the Adwaita icon theme for anything Prairie does not include.

Prairie’s navigation, places and media transport glyphs use **Lucide**
geometry: outlined shapes on a 24-unit grid with a 2-unit stroke and round caps
and joins. Lucide is licensed under ISC, and Prairie retains its notices.

Media transport uses the standard names, so a player gets Prairie’s glyphs
with no extra work: `media-playback-start-symbolic`,
`media-playback-pause-symbolic`, `media-skip-backward-symbolic`,
`media-skip-forward-symbolic`, `media-playlist-shuffle-symbolic` and
`media-playlist-repeat-symbolic`.

Conversation and identity glyphs are Prairie's too, under standard names:
`mail-attachment-symbolic`, `mail-send-symbolic`, `call-start-symbolic`,
`camera-video-symbolic`, `audio-input-microphone-symbolic`,
`document-edit-symbolic`, `system-search-symbolic`, `view-refresh-symbolic`,
`emblem-favorite-symbolic`, `dialog-warning-symbolic`, `emblem-ok-symbolic`,
`go-next-symbolic`, `go-previous-symbolic`, `edit-clear-symbolic`,
`object-select-symbolic`, `phone-symbolic`, `mail-message-new-symbolic`, and for
avatars `avatar-default-symbolic` (a person or a number),
`system-users-symbolic` (a group) and `luma-business-symbolic` (a short code).

LumaUI parts name their icons with Lucide names (`"share-2"`), which resolve to
`lumaui-<name>-symbolic` glyphs that LumaUI ships (`lumaui_icon()` in Python,
`luma_ui_icon_name()` in C).

Use icons from the theme by their standard names wherever one exists:

```python
Gtk.Image(icon_name="folder-symbolic", pixel_size=14)
```

Common names: `list-add-symbolic`, `edit-delete-symbolic`,
`user-trash-symbolic`, `document-open-symbolic`, `document-save-symbolic`,
`edit-undo-symbolic`, `system-search-symbolic`, `view-more-symbolic`,
`go-previous-symbolic`, `go-next-symbolic`, `emblem-system-symbolic`,
`starred-symbolic`, `folder-symbolic`, `user-home-symbolic`.

LumaUI also installs a few icons of its own, such as `luma-plus-symbolic`,
`luma-chevron-down-symbolic`, the text formatting set
(`luma-format-bold-symbolic` and others) and the alignment set
(`luma-align-left-symbolic` and others).

### Drawing your own symbolic icon

When no standard icon fits, draw one in the same style and ship it in your app:

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"
     fill="none" stroke="currentColor" stroke-width="2"
     stroke-linecap="round" stroke-linejoin="round">
  <path d="M12 5v14M5 12h14" class="transparent-fill foreground-stroke"/>
</svg>
```

- 24 by 24 viewBox, 2-unit stroke, round caps and joins, no fills.
- Add `class="transparent-fill foreground-stroke"` to each stroked path so
  GTK 4.20 and newer recolor the stroke. Prairie’s own glyphs do the same. Name
  the file with a `-symbolic.svg` suffix.
- Install it under your app’s icon path, for example
  `/app/share/icons/hicolor/scalable/actions/com.example.Notebook-tag-symbolic.svg`,
  or add a GResource path with `Gtk.IconTheme.add_resource_path()`. Prefix custom
  names with your app ID so they never collide.
- Mirror directional glyphs for right-to-left layouts by also shipping
  `-symbolic-rtl.svg` variants.

## Sizes

These are the sizes LumaUI’s own parts use.

| Size | Where |
| --- | --- |
| 14px | Compact 28px icon buttons (`IconButton`), toolbar buttons |
| 16px | Menu rows, sidebar rows |
| 18px | Standard 36px icon buttons, symbolic icons in content |
| 24px | Quick actions in menus, compact empty states |
| 38px | Empty-state glyph |

Use these sizes for your own icons too, rather than scaling glyphs to sizes in
between.

## Icons and meaning

- Pair an icon with a text label when the action is not universally understood.
  Icon-only buttons need a tooltip and an accessible name; `IconButton` sets both
  from its label.
- Use one icon for one meaning across your app.
- Do not use an icon as the only indicator of state; add a label or an accessible
  description.
