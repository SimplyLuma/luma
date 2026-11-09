---
title: Install the SDK
summary: The packages that make up the Luma Developer Platform, what each contains, and how to install the Flatpak runtime and SDK from the Luma remote.
order: 1
section: Getting started
---

# Install the SDK

The Luma Developer Platform is three packages on Luma and one Flatpak SDK on
the Luma remote. This page says exactly what each one contains so you know what
you are building against.

> **Status.** The platform is version **0.1.0, preview**. The packages below
> are what Luma’s own apps build and run against. The Flatpak SDK,
> `org.projectluma.Sdk//44`, is the way to build outside Luma and the way to
> build for Depot.

## What is already on every Luma system

Every Luma image, desktop and mobile, includes the **`luma-developer-platform`**
package. You do not install it; it is part of the operating system. It
contains:

| Path | What it is |
| --- | --- |
| `libluma-ui-1.so.0`, `LumaUI-1.typelib` | LumaUI’s C library, for C and C++ and for any language with GObject introspection |
| `libluma-semantics-1.so.0`, `LumaSemantics-1.typelib` | Semantic objects, actions and Live Extension data |
| `libluma-appearance-1.so.0`, `LumaAppearance-1.typelib` | The shared surface policy LumaUI uses to follow light, dark, Frost, Glass and high contrast |
| `luma_appkit` (Python package) | LumaUI for Python: `AppWindow`, `Island`, commands, menus, navigation, controls and the LumaUI parts |
| `/usr/share/luma-appkit/` | LumaUI’s stylesheets, token sheets and fonts |
| `/usr/share/icons/hicolor/scalable/actions/luma-*.svg`, `lumaui-*.svg` | LumaUI’s own symbolic icons |
| `luma-semantic-broker` (user service) | The Semantic Broker that mediates agent and automation access to apps |

Because the platform is in the image, a Python app that imports `luma_appkit`
runs on Luma with nothing else installed.

## Development packages

| Package | Contains | You need it to |
| --- | --- | --- |
| **`luma-developer-platform-devel`** | Headers in `/usr/include/luma-1/`, `pkg-config` files `luma-ui-1`, `luma-semantics-1` and `luma-appearance-1`, GIR files, and the JSON schemas and project template in `/usr/share/luma-platform/` | Compile C or C++ against `LumaUI-1` |
| **`luma-developer-platform-sdk`** | The `luma` command (also installed as `luma-sdk`), the Python project template, and the Semantic Inspector | Create projects and run `luma lint`, `luma inspect`, `luma test` and `luma release-check` |

The SDK package requires Python 3.11 or newer and PyYAML. The devel package
requires `glib2-devel`, `gtk4-devel` and `libadwaita-devel`.

> **Where to get them.** Luma’s desktop image includes the SDK package, so the
> `luma` command is already there. The devel package is not in the image, and
> Luma does not publish its packages in a public package repository. The
> `luma` command is not on PyPI either. Outside Luma, and for C or C++ on Luma,
> use the Flatpak SDK below: it carries both packages.

Check what you have on Luma:

```sh
rpm -q luma-developer-platform luma-developer-platform-sdk
luma --version            # luma-sdk 0.1.0
python3 -c "import luma_appkit; print(luma_appkit.__file__)"
```

## The Flatpak runtime and SDK

Depot hosts apps as Flatpaks on the signed Luma remote, built on Luma’s own
runtime:

| Ref | Purpose |
| --- | --- |
| `org.projectluma.Platform//44` | The runtime your app runs on. Built from Fedora 44’s Flatpak runtime packages, plus LumaUI (`luma-developer-platform`), the Figtree, Caveat and IBM Plex fonts and the Prairie icon theme. |
| `org.projectluma.Sdk//44` | The matching SDK you build with: compilers, Meson, headers, `pkg-config` files, `luma-developer-platform-devel` and `luma-developer-platform-sdk`, which includes the `luma` command. |
| `org.projectluma.Platform.Office//44` | An extension carrying LibreOfficeKit 26.2, for document apps that declare it. |

Because the runtime carries LumaUI, a Luma app looks and behaves the same on
Luma and inside the sandbox on another distribution. The remote serves
`x86_64` only for now; there are no ARM builds yet. To set it up:

```sh
flatpak remote-add --if-not-exists luma https://dl.simplyluma.com/luma.flatpakrepo
flatpak install luma org.projectluma.Platform//44 org.projectluma.Sdk//44
```

`flatpak-builder --install-deps-from=luma` also installs them for you the first
time you build a manifest that names them.

## Tools you will also want

These are standard Linux tools, not Luma packages:

- `flatpak` and `flatpak-builder` to build and run your app in a sandbox.
- `flatpak-builder-lint`, which Luma runs on the apps it builds for the Luma
  remote. It ships inside the `org.flatpak.Builder` Flatpak.
- `appstreamcli` (package `appstream`) to validate metainfo.
- `desktop-file-validate` (package `desktop-file-utils`).
- `meson` and `ninja` for C and C++ projects.
- Accerciser or another AT-SPI inspector to check accessibility.

## Next

- [Your first app in Python](first-app-python.md)
- [Your first app in C](first-app-c.md)
- [The `luma` command](../kit/sdk-command-line.md)
