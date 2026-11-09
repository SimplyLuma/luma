---
title: Runtimes, distributions and architectures
summary: Where a Luma app runs, how it gets there, the Luma runtime and remote, what differs on other distributions, and which CPU architectures the remote carries today.
order: 2
section: Platform
---

# Runtimes, distributions and architectures

## Where your app runs

| Where | How it installs | What it runs with | Looks like |
| --- | --- | --- | --- |
| **Luma** | From Depot, as a Flatpak from the Luma remote | The `org.projectluma.Platform//44` runtime, which carries LumaUI | Luma |
| **Other distributions with Flatpak** (Fedora Workstation, Ubuntu, Debian, Arch, openSUSE and others) | `flatpak install` from the Luma remote, a `.flatpakref`, or a software center that opens one | The same Luma runtime inside the sandbox | Luma |

LumaUI draws a Luma app’s window and components itself, so the same Flatpak
looks and behaves the same on Luma and on another distribution. Luma-specific
system features are only present on Luma: the Semantic Broker and Live
Extensions, for example. Your app must keep working without them.

LumaUI is not packaged for other distributions, so a LumaUI app cannot be
shipped there as a native RPM or DEB. Use the Flatpak.

> **Status.** The signed Luma remote is live. It carries the Luma runtimes on
> branch `44` and Luma’s own apps, for `x86_64` only. Third-party apps reach it
> through Depot publishing, which is not open yet.

## The Luma remote

| | |
| --- | --- |
| Repository | `https://dl.simplyluma.com/repo/` (OSTree, signed summary and commits) |
| Remote file | `https://dl.simplyluma.com/luma.flatpakrepo` |
| Per-app install file | `https://dl.simplyluma.com/apps/<app-id>.beta.flatpakref` for an app on the `beta` branch, `https://dl.simplyluma.com/apps/<app-id>.flatpakref` for one on `stable` |
| Branches | `stable` (the remote’s default) and `beta`. Most of Luma’s apps are on `beta` today. |
| Architectures | `x86_64` only for now |
| Signing | A dedicated Luma Depot OpenPGP key. The public key is in the `.flatpakrepo` file. |

On a system with Flatpak:

```sh
flatpak remote-add --if-not-exists luma https://dl.simplyluma.com/luma.flatpakrepo
flatpak install luma org.projectluma.Tide//beta
# or, for one app:
flatpak install --from https://dl.simplyluma.com/apps/org.projectluma.Tide.beta.flatpakref
```

To build against the Luma runtime, install the runtime and the SDK:

```sh
flatpak install luma org.projectluma.Platform//44 org.projectluma.Sdk//44
```

## Runtimes

| Ref | What it is |
| --- | --- |
| `org.projectluma.Platform//44` | The runtime. Built from Fedora 44’s Flatpak runtime package set, with Luma’s builds of GTK 4 and libadwaita, LumaUI (`luma-developer-platform`), the Figtree font and the Prairie icon theme. |
| `org.projectluma.Sdk//44` | The SDK for `flatpak-builder`. It adds the LumaUI headers and `pkg-config` files and the `luma` command. |
| `org.projectluma.Platform.Locale`, `org.projectluma.Platform.GL.default` | The runtime’s translations and default graphics drivers. Flatpak installs them with the runtime. |
| `org.projectluma.Platform.Office` | Extension with LibreOfficeKit 26.2 for document apps. |

The branch, `44`, matches the Fedora release the runtime is built from.

### The GNOME runtime in the SDK template

`luma new` still writes a Flatpak manifest on `org.gnome.Platform//50` that
builds `luma-developer-platform` as its first module from a source tag. That
source repository is not publicly reachable, so the generated manifest does not
build as it is, and the GNOME runtime does not carry Figtree or the Prairie
icons. Switch `runtime`, `sdk` and `runtime-version` to the Luma runtime and
remove that module; see
[project layout](../getting-started/project-layout.md#flatpak-manifest).

## Presentations on Luma

One binary serves every Luma device. LumaUI tells your app how it is being
presented through `LumaContext` (C) and `AppContext` (Python), read from the
environment:

| Variable | Values | Default |
| --- | --- | --- |
| `LUMA_DEVICE_CLASS` | `desktop`, `tablet`, `handheld` | Read from `/etc/luma-device-class`, else `desktop` |
| `LUMA_PRESENTATION_MODE` | `windowed`, `fullscreen-mobile` | `fullscreen-mobile` on a handheld, else `windowed` |
| `LUMA_INPUT_MODE` | `pointer`, `touch` | `touch` on a handheld, else `pointer` |

Set these yourself to test a phone presentation on a desktop:

```sh
LUMA_PRESENTATION_MODE=fullscreen-mobile LUMA_INPUT_MODE=touch python3 src/main.py
```

Inside a Flatpak, `/etc` is the runtime’s, not the host’s, so the app cannot
read the host’s `/etc/luma-device-class`. Use the environment variables there.

> **Known difference.** The Python `AppContext` treats a `tablet` device class as
> touch input; the C `LumaContext` only switches to touch for `handheld`. Set
> `LUMA_INPUT_MODE` explicitly if you depend on it. See
> [adaptive layouts](../kit/adaptive-layouts.md).

## Architectures

| Architecture | Status |
| --- | --- |
| `x86_64` | Supported. The Luma remote carries `x86_64` runtimes and apps. |
| `aarch64` | Not on the Luma remote yet. |

`luma release-check` asks for passing evidence on both `x86_64` and `aarch64`
before an app is a Native candidate (see
[`luma-app.toml`](../getting-started/luma-app-toml.md#evidence-file)), so avoid
code that assumes one architecture. Luma isn’t built for `aarch64` yet, so no
app can pass that check today.

## What changes between Luma and elsewhere

| Feature | On Luma | Elsewhere, on the Luma runtime |
| --- | --- | --- |
| Luma window, islands, identity | Yes | Yes |
| Figtree and Prairie icons | Yes | Yes, from the runtime |
| Portals | Luma’s portal configuration, including Luma’s Open With and screen sharing choosers | The host’s portal backends (GNOME, KDE and others) |
| Light, dark, high contrast | Follows Luma settings, including the Frost and Glass materials | Follows the host’s light or dark preference (the `org.freedesktop.appearance` color scheme); Frost and Glass are Luma settings and are not used |
| Semantic Broker, Live Extensions | Available | Absent; your app must not require them |
| Presentation context | Read from the environment and the device | Defaults to desktop, windowed, pointer |
