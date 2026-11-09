---
title: Documentation changelog
summary: Dated changes to the Luma developer documentation that affect what you build, and the platform release each one describes.
order: 99
section: Overview
---

# Documentation changelog

Changes to these docs that affect what you build, newest first. Platform API
changes name the platform version they apply to.

## October 8, 2026: the alpha

Luma alpha was released on October 7, 2026. The docs describe Luma Developer
Platform **0.1.0 (preview)** as it ships in the alpha.

**Changed**

- Publishing: the developer program, app ID registration, release submission,
  tester lists and suggesting an app are not open yet, and the publishing pages
  now say so.
- [Runtimes and distributions](platform/runtimes-and-distributions.md): the
  Luma remote carries `org.projectluma.Platform//44`, `org.projectluma.Sdk//44`,
  their Locale and GL extensions, the `org.projectluma.Platform.Office`
  extension and Luma’s own apps, most of them on `beta`, for `x86_64` only.
- The Flatpak manifest `luma new` writes builds the platform from a source
  repository that is not publicly reachable. Switch it to the Luma runtime and
  remove that module, as
  [project layout](getting-started/project-layout.md#flatpak-manifest) shows.
- [The stack](platform/the-stack.md): on Luma, Luma’s builds of GTK 4 and
  libadwaita still add parts of the Luma look to GTK apps that do not use
  LumaUI. Do not depend on that styling.

**Known issues**

- The SDK’s project template does not use LumaUI’s `AppWindow` or the Luma
  runtime.
- The Python and C presentation contexts disagree about tablet input. Set
  `LUMA_INPUT_MODE` if you depend on it.

## September 29, 2026: LumaUI

**Changed**

- The toolkit is called **LumaUI** throughout. LumaUI is Luma’s own layer on
  GTK 4, not a fork of GTK, and it draws the whole visible Luma app: the frame,
  title row, identity, window controls, islands and every component.
- LumaUI’s Python package is still imported as `luma_appkit`. Its reference is
  now titled [LumaUI Python reference](kit/appkit-reference.md) at the same
  address.
- [Bringing an existing GTK app](getting-started/existing-gtk-apps.md)
  describes moving onto LumaUI instead of opt-in style classes.
- The signed Luma remote is live, with `org.projectluma.Platform//44`,
  `org.projectluma.Sdk//44` and Luma’s apps on the `beta` branch, for `x86_64`
  only. Install commands and per-app `.flatpakref` addresses were updated.
- Languages: Python and C are supported, and C++ apps use `LumaUI-1` directly.
  Other languages work only through GObject introspection.
- Menus: `Menu(registry, keep_parent=True)` is the menu for a
  `Gtk.MenuButton`, and the identity menu shows check marks, descriptions and
  submenus.
- Typography points to LumaUI’s type roles. `LUMAUI_API_LEVEL` is 2.
- The dialog, motion and color guidance points to LumaUI parts and tokens
  instead of libadwaita widgets and colors.

## September 17, 2026: unread badges

Describes Luma Developer Platform **0.1.0 (preview)**.

**Added**

- Kit: [background agents](kit/background-agents.md#unread-badges): the
  standard `badge` value for an app’s unread badge on its dock icon, and
  `Agent.set_badge`, `BADGE_VALUE` and `BADGE_DOT` in `luma_appkit.background`.

## September 15, 2026: waking for alarms

Describes `luma-background` 0.1.0.

**Added**

- Kit: [background agents](kit/background-agents.md): autostart entries
  while the agent is managed (`X-Luma-Background-Agent`), and
  `org.projectluma.BackgroundWake1` for waking a suspended computer for an
  alarm.

## September 15, 2026: background agents

Describes Luma Developer Platform **0.1.0 (preview)** and `luma-background`
0.1.0.

**Added**

- Kit: [background agents](kit/background-agents.md), the contract for keeping
  an app working with its windows closed: the `[background]` table, categories
  and defaults, `luma_appkit.background`, `LumaBackgroundAgent`, the
  `org.projectluma.Background1` and `org.projectluma.BackgroundAgent1` D-Bus
  interfaces, and how the Background portal reaches the same decisions.
- `luma-app.toml`: the `[background]` table and its `luma lint` rules.
- Permissions and privacy: background activity.

## September 15, 2026: first publication

First publication of the developer documentation, describing Luma Developer
Platform **0.1.0 (preview)** and Depot publishing.

**Added**

- Overview: building for Luma, Depot tiers and how publishing works.
- Getting started: installing the SDK, a first app in Python, a first app in C
  with `LumaUI-1`, project layout, the `luma-app.toml` reference, and bringing
  an existing GTK app.
- Platform: the stack, and runtimes, distributions and architectures.
- Kit: overview, Python reference, `LumaUI-1` reference, windows and
  islands, commands and menus, semantics and actions, adaptive layouts, and the
  `luma` command.
- Design guidelines: principles, windows and islands, typography, color, spacing
  and sizing, icons, motion, accessibility, and writing style.
- Permissions and privacy, including the Depot permission vocabulary.
- Publishing: the developer program, app IDs and namespaces, metainfo and
  screenshots, submitting a release, the Depot Review Guidelines, updates and
  permission changes, ratings and reviews, Open in Depot links, and suggesting a
  Listed app.
- Knowledge base: FAQ and troubleshooting.
