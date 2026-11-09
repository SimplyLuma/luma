---
title: LumaUI principles
summary: Why LumaUI exists, the semantic-API rule, who owns what between GTK, LumaUI and apps, and how the API is versioned.
order: 9
section: Kit
---

# LumaUI principles

LumaUI is Luma’s application toolkit: the standard way a Luma app is made. It is
the C library `LumaUI-1` plus its Python package, `luma_appkit`. It is Luma’s
own layer on **stock** GTK 4 and libadwaita, not a fork of either. LumaUI’s
stylesheets load above libadwaita’s, so a LumaUI window looks the same on a
stock system as it does on Luma.

## Why

1. **Instant upgrades.** When the Luma design improves, every app built with
   LumaUI improves at once, with no app changes. The design lives in one place:
   LumaUI and its design tokens.
2. **Portability.** Luma apps run on other Linux distributions inside the Luma
   runtime, with their dependencies bundled. LumaUI therefore finds its
   stylesheets and icons beside its own package first and in system paths last,
   and needs nothing that exists only on a Luma system.
3. **New primitives where none exist.** GTK has no standard for a bar that grows
   into an editor, a details pane that closes itself, a drawer, or a behaviour
   and motion system. LumaUI designs the right primitive (`LayerHost`, `Toast`,
   `DestructiveDialog`, the action center) instead of a workaround, even when
   that is more work.

## The semantic-API rule

Apps say **what**; LumaUI owns **how**.

```python
CountBadge(3, attention=True)                       # not: a label with a blue background
Toast.show(button, "Copied", kind="copied")         # not: a toast you style and time yourself
DestructiveDialog.ask(button, title="Uninstall Kiln?", body=…, action="Uninstall", on_confirm=…)
StackedButtons([StackedButton("log-out", "Leave"), StackedButton("trash-2", "Delete", danger=True)])
```

- No knobs that restyle a part: no colour, radius, size or duration arguments.
  If a design needs a new variant, it becomes a named variant in LumaUI
  (`small=True`, `kind="place"`) and a token.
- App stylesheets never select a LumaUI part (`.lumaui-*`).
- Parts refuse misuse where it is cheap to detect, by raising an error:
  stacked buttons come in pairs or trios, a destructive dialog never says
  "Are you sure?" or "OK", a toast kind must be one LumaUI knows.
- Icons are Lucide names (`"share-2"`), resolved to stable
  `lumaui-<name>-symbolic` theme names. Share is `share-2`, Export is `share`,
  Upload is `upload`.

## Who owns what

| Layer | Owns | Never |
| --- | --- | --- |
| **GTK 4 (stock)** | Rendering, input, accessibility, Wayland, text | Luma’s look |
| **LumaUI** | The window frame, title row, identity pill, window controls and islands; every component; tokens; motion; phone behaviour | App content |
| **Apps** | Their content (a document, a canvas, a list of songs) and which parts they compose | Restating or restyling a LumaUI part |

Everything a part looks like is a token: colours as `@luma_*` roles in the
generated token sheets, metrics and motion as `--lumaui-*` CSS custom
properties, and the same numbers for Python in `luma_appkit.lumaui_tokens`.
The token sheets reload when the appearance changes, so light, dark, Frost,
Glass and high contrast are a token change, not an app change.

## API level, compatibility and deprecation

- `luma_appkit.LUMAUI_API_LEVEL` is an integer (2 today). It rises by one
  whenever a public name, a keyword argument or a toast kind is added. Apps
  that need a newer part check it (`if LUMAUI_API_LEVEL >= 2:`). The C library
  has its own `LUMA_UI_API_LEVEL`.
- Within a level nothing public is removed, renamed or changes meaning.
- A name that is going away is first **deprecated**: it keeps working, warns
  once, and is documented with its replacement. It is removed no sooner than
  one API level **and** one monthly release later.
- Looks and motion are not API: they change whenever the design does, which is
  the point.
- Deprecated now:
  - `NavigationSidebar(compact=True)`: use `NavigationSidebar(variant="files")`,
    which is what `compact=True` already draws.
- Private names start with an underscore. Module internals (`action_toast`,
  `structure_layers` and so on) are importable, but only names exported from the
  package root are covered by this promise.

## Rules every part follows

Every public part has keyboard, accessibility, reduced-motion, high-contrast,
long-text and phone-width behaviour (see [behaviour](behaviour.md)) and tests.
