---
title: Frequently asked questions
summary: Answers about languages, LumaUI, portals and file access, fonts, dark and light, other distributions, app IDs, testing private releases, tiers and pricing.
order: 1
section: Knowledge base
---

# Frequently asked questions

## Building

### Which language should I use?

**Python** with LumaUI’s Python package, `luma_appkit`, unless you need a native
engine. It has the most complete API, including the command registry and
navigation history, and several of Luma’s own apps are written in it. Choose
**C or C++** with `LumaUI-1` for realtime audio, large documents or heavy
rendering, as Grid, Stage, Session and Write do. See
[the stack](../platform/the-stack.md#languages).

### Can I use another language, such as Rust, JavaScript or Vala?

Only through GObject introspection of `LumaUI-1` and `LumaSemantics-1`. These
are not supported languages yet: there are no examples or tests, and the
Python command registry is not available to them.

### Is the API stable?

No. The platform is a 0.1.0 preview. Note the platform version you tested
against and read the [changelog](../CHANGELOG.md) before upgrading.

### Do I have to use LumaUI?

No. Apps built with plain GTK or other toolkits run on Luma and can be
published in the Verified tier. LumaUI is what gives an app the Luma window and
components, and it is required for the Luma tier. See
[bringing an existing GTK app](../getting-started/existing-gtk-apps.md).

### LumaUI is missing a component I need

Build it from plain GTK widgets and [color tokens](../design/color.md), keeping
it inside your islands. Do not copy or restyle LumaUI components.

### Can I use a web view or Electron?

Yes, in the Verified tier, as long as your app meets
[minimum functionality](../publishing/review-guidelines.md#5-minimum-functionality).
Electron and other toolkits keep their own look; they do not get the Luma window.
A WebKitGTK view inside a GTK app is fine for showing web content. The Luma tier
expects a native LumaUI interface.

## Files and portals

### My app cannot see the user’s files

That is the sandbox working. Ask for the file with `Gtk.FileDialog`; the portal
shows a system file chooser and gives your app access to exactly what the person
picked. See [permissions and privacy](../permissions-and-privacy.md#portals-first).

### How do I reopen a file next time without a filesystem permission?

Store the path the file chooser returned. Inside a Flatpak it points into the
document portal (`/run/user/<uid>/doc/...`), and the grant persists for your app,
so you can open it again later. Use `GtkRecentManager` or your own list.

### The path I get looks strange

Paths under `/run/user/<uid>/doc/` are document portal paths. Treat them as
opaque, show the file’s display name to people (from `Gio.FileInfo`), and do not
try to derive the original location.

### Can I save next to the original file, for example an export?

Only through a save dialog, or with a filesystem permission, which `luma lint`
reports as too broad. The file chooser
grant covers the chosen file, not its folder. Offer **Export…** with
`Gtk.FileDialog.save()` and suggest a name.

### How do I watch a folder?

Ask for the folder with `Gtk.FileDialog.select_folder()` and monitor it with
`Gio.File.monitor_directory()`. An app whose purpose is managing a whole media
library may need a read-only XDG permission such as `--filesystem=xdg-music:ro`,
which Depot shows as a sensitive permission. `luma lint` reports every
`--filesystem=` permission as too broad for a Luma Native candidate, so use one
only when a portal cannot do the job.

## Look and feel

### Why does my app look different outside Luma?

It should not, if it is built on LumaUI and runs on the Luma runtime. LumaUI
draws the Luma window, islands and identity itself, and the
`org.projectluma.Platform` runtime carries LumaUI, Figtree and the Prairie icons
on any distribution. Differences come from running on another runtime, such as
`org.gnome.Platform`, which has none of them. The manifest `luma new` writes
still targets the GNOME runtime; switch it to the Luma runtime. See
[runtimes and distributions](../platform/runtimes-and-distributions.md).

### My colors are wrong in dark mode

You have a literal color, or a token the dark token sheet does not define
(`@luma_card`, `@luma_chrome`, `@luma_hairline`, `@luma_application_ink`), or you
loaded your stylesheet with a plain `Gtk.CssProvider` so it was not re-parsed
when the appearance changed. Use the [tokens](../design/color.md) and
`add_style_sheet()` or `luma_ui_add_style_resource()`.

### How do I force dark or light?

Do not. LumaUI follows the person’s choice. If your content is inherently dark,
such as a photo editor’s canvas or a video monitor, darken that content area
only.

### The font is not Figtree

On Luma, Figtree is the system interface font and LumaUI sets it on Luma
windows. If your app shows another font: check that the window uses `AppWindow`
or `LumaApplicationWindow`; check that you are not setting `font-family` in your
stylesheet; and inside Flatpak, check that you are on the Luma runtime, which
provides Figtree.

### Can I use my own icons?

For your app icon, yes, and you must. For interface icons, use the theme’s
symbolic icons first, and draw missing ones in the same outlined style. See
[icons](../design/icons.md).

## Other distributions

### Will my Depot app run on Ubuntu, Arch or Fedora Workstation?

Yes, on any `x86_64` system with Flatpak, because it runs on the Luma runtime.
People add the Luma remote or open your app’s `.flatpakref`. Luma-only services
(the Semantic Broker, Live Extensions) are not there, so your app must work
without them.

### Should I also publish on Flathub?

You can, with the same app ID everywhere. Flathub builds use Flathub’s
runtimes, which do not carry LumaUI, Figtree or the Prairie icons, so a LumaUI
app would have to build LumaUI itself as a module and would not have Luma’s
font and icons. The `luma-developer-platform` source tag the SDK template
points at is not publicly reachable, so plan on this only for apps that do not
use LumaUI for now.

## App IDs and publishing

> **Not open yet.** The developer program, publishing and tester lists open
> soon. The answers below describe how they will work.

### Can I change my app ID?

Treat it as permanent once public. See
[app IDs and namespaces](../publishing/app-ids-and-namespaces.md#if-an-id-must-change).

### I do not own a domain

Use `io.github.<username>.<AppName>` or `io.gitlab.<username>.<AppName>`. You
prove you own that account when you register the app.

### How do testers install a private release?

Keep the app’s visibility `private` and add testers by their Luma Connect
account. Only Luma staff and your testers can see and install a private app.
Put test builds on the `beta` branch so promoting to `stable` is a separate
step.

### How long does review take?

There are no review times to report yet, because review has not opened.

### Does it cost anything?

Joining the developer program is free.

### Can I sell my app or offer in-app purchases?

Depot has no payment system. You may charge through your own
payment system if the listing says so clearly
([guideline 3.6](../publishing/review-guidelines.md#3-honesty)).

### How do I get into the Luma tier?

By invitation after review. Build on LumaUI, pass `luma release-check` with real
evidence, follow the design guidelines, and meet every **L** rule in the
[review guidelines](../publishing/review-guidelines.md).
