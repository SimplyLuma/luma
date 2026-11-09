---
title: Tools
summary: The luma command, the Luma Platform runtime and SDK, the LumaUI reference, Imager and Depot publishing: what each is for, how to get it, and whether it's available yet.
order: 1
section: Tools
---

# Tools

These are the tools you can use to build, check and ship a Luma app. Each one
says whether it's available now.

## The `luma` command

**Available now, on Luma and in the Luma SDK.**

Start a project and check it against the Luma app contract. It comes with
Luma’s desktop image and with the `org.projectluma.Sdk//44` Flatpak SDK, so
there is nothing extra to install. It is not published on PyPI.

```sh
mkdir my-app && cd my-app
luma new "My App" --id com.example.MyApp
luma lint
luma inspect
luma test
luma release-check
```

- `luma new` fills an empty folder (the current one unless you pass
  `--destination`) from the Python template: `luma-app.toml`, a starter
  `src/main.py`, a Flatpak manifest, AppStream metadata, a desktop file, an
  icon and a verification scaffold, all under one app ID. Replace the
  generated Flatpak manifest before you build; see
  [project layout](getting-started/project-layout.md#flatpak-manifest).
- `luma lint` checks `luma-app.toml` and the files it points to against the
  app contract.
- `luma inspect` prints your app’s platform and conformance metadata as JSON,
  with its status and anything still missing.
- `luma test` checks the static adaptive and semantic contracts and prints the
  width and input combinations to cover. It does not run your app.
- `luma release-check` checks the evidence an app needs for Luma Native
  status. A new project fails it until that evidence exists, and that's
  expected.

Passing `luma lint` means your project is structurally valid. Luma Native
status takes the evidence `luma release-check` asks for; editing
`luma-app.toml` never earns it. It is a status the SDK reports, not a Depot
tier.

Read the full guide: [The luma command](kit/sdk-command-line.md).

## Luma Platform and SDK

**Available now, for PCs with Intel or AMD processors.**

The Flatpak runtime Luma’s own apps ship on, and the SDK to build against it.
They live on the signed Luma remote, so you can build on Luma or on any Linux
system with Flatpak.

```sh
flatpak remote-add --if-not-exists luma https://dl.simplyluma.com/luma.flatpakrepo
flatpak install luma org.projectluma.Platform//44 org.projectluma.Sdk//44
```

ARM builds are not available yet.

Read more: [Install the SDK](getting-started/install-the-sdk.md) and
[runtimes and distributions](platform/runtimes-and-distributions.md).

## LumaUI reference

**Available now, in these docs.**

Every LumaUI component: what it's for, when to use it, and its Python and C
names. LumaUI draws the window, the title row, the controls and every
component; your app says what it needs and LumaUI decides how it looks.

Open it: [LumaUI reference](kit/lumaui-reference.md).

## Imager

**Available now, on Luma.**

Put Luma on a USB drive to test your app on real hardware. Imager writes the
drive, then reads it back to check it holds exactly what was written. It comes
with Luma; it isn't available for macOS, Windows or other Linux systems yet.

Imager is in Depot on Luma.

## Depot publishing

**Not open yet.**

Publishing your own app to Depot opens with the developer program. Applying,
managing your apps and suggesting an app for Depot open soon. Until then,
Depot carries Luma’s own apps and apps Luma lists from other sources.

Read how it will work: [Submitting a release](publishing/submitting-a-release.md).
