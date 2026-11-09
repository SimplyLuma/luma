---
title: Project layout
summary: Every file a Luma app project contains, how their identities must agree, and complete checked examples of the desktop entry, metainfo, icon and Flatpak manifest.
order: 4
section: Getting started
---

# Project layout

A Luma app is an ordinary Linux app project plus one Luma file,
`luma-app.toml`, that ties everything together. This page shows the complete
Notebook project from [your first app in Python](first-app-python.md). The
files below pass `luma lint`, and the metainfo passes
`appstreamcli validate --no-net`. Run `desktop-file-validate` on your desktop
entry too.

```text
notebook/
├── luma-app.toml                           Luma application contract
├── src/
│   └── main.py                             The app
├── data/
│   ├── com.example.Notebook.desktop        Desktop entry: launcher, name, icon
│   ├── com.example.Notebook.metainfo.xml   AppStream: the store page
│   └── com.example.Notebook.svg            App icon
├── flatpak/
│   └── com.example.Notebook.json           Flatpak manifest: build and sandbox
└── verification/
    └── luma-native-evidence.json           Test evidence, false until CI fills it
```

Larger apps add what they need: `po/` for translations, a GSettings schema
(`data/<app-id>.gschema.xml`), a MIME type (`data/<app-id>.mime.xml`), a D-Bus
service file for activation, `tests/`, and a Meson build.

## One identity everywhere

Your reverse-DNS app ID is the name of your app in every system that touches
it. `luma lint` fails the project if any of these disagree:

| Where | Must be |
| --- | --- |
| `luma-app.toml` `application.id` | The canonical ID, for example `com.example.Notebook` |
| Flatpak manifest `id` (or legacy `app-id`) | The same ID |
| AppStream `<id>` | The same ID |
| AppStream `<launchable type="desktop-id">` | The ID plus `.desktop` |
| Desktop entry filename | The ID plus `.desktop` |
| Desktop entry `Icon=` | The same ID |
| Icon filename | The ID, with any extension |
| Desktop entry `Exec=` first word | The Flatpak manifest `command` |

The ID must have at least three dot-separated parts, start with a letter, use
only letters, digits and underscores in each part, keep the first two parts
lowercase, and not end in `.desktop`. IDs are permanent once your app is public;
see [app IDs and namespaces](../publishing/app-ids-and-namespaces.md).

Why this matters: a mismatch produces duplicate launchers, permissions granted
to the wrong app, orphaned data after uninstall, and a store page that points
at the wrong thing.

## `luma-app.toml`

```toml
schema_version = "0.2"

[application]
id = "com.example.Notebook"
name = "Notebook"
version = "0.1.0"
entrypoint = "src/main.py"

[platform]
ui_contract = "1"
semantics_contract = "1"

[responsive]
adaptive = true
test_widths = [360, 500, 1024, 1440]
input_modes = ["pointer", "touch", "keyboard"]
presentation_modes = ["windowed", "fullscreen-mobile"]

[distribution]
substrate = "flatpak"
flatpak_manifest = "flatpak/com.example.Notebook.json"
appstream_metadata = "data/com.example.Notebook.metainfo.xml"
desktop_file = "data/com.example.Notebook.desktop"
icon = "data/com.example.Notebook.svg"
portal_first = true
portals = ["file-chooser", "notifications"]

[lifecycle]
app_data = "removable"
cache = "removable"
user_documents = "preserved"
reset_supported = true
uninstall_choices = ["keep-data", "remove-app-data"]

[conformance]
accessibility = true
keyboard = true
semantics = true
reduced_motion = true
evidence = "verification/luma-native-evidence.json"
```

Every key is explained in [`luma-app.toml`](luma-app-toml.md).

## Desktop entry

```ini
[Desktop Entry]
Type=Application
Name=Notebook
Comment=Keep short notes
Exec=com.example.Notebook
Icon=com.example.Notebook
Terminal=false
Categories=Utility;
StartupNotify=true
```

LumaUI reads `Name` and `Icon` from this file to draw your app’s identity
in the top-left corner of every window, so the corner always says what the dock
says. Add `MimeType=` and `%U` or `%F` to `Exec` if your app opens files.

## AppStream metainfo

```xml
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>com.example.Notebook</id>
  <name>Notebook</name>
  <summary>Keep short notes</summary>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>Apache-2.0</project_license>
  <developer id="com.example">
    <name>Example Developer</name>
  </developer>
  <description>
    <p>Notebook keeps short notes in one adaptive window that works on a
    desktop, a tablet and a phone.</p>
  </description>
  <launchable type="desktop-id">com.example.Notebook.desktop</launchable>
  <url type="homepage">https://example.com/notebook</url>
  <provides>
    <binary>com.example.Notebook</binary>
  </provides>
  <supports>
    <control>pointing</control>
    <control>keyboard</control>
    <control>touch</control>
  </supports>
  <custom>
    <value key="org.projectluma.ApplicationContract">0.2</value>
  </custom>
  <content_rating type="oars-1.1"/>
  <releases>
    <release version="0.1.0" date="2026-09-15"/>
  </releases>
</component>
```

The `org.projectluma.ApplicationContract` custom value is required by
`luma lint`. Depot builds your store page from this file; see
[metainfo and screenshots](../publishing/metainfo-and-screenshots.md) for
screenshots, release notes, content rating and what review checks.

## Icon

Ship a scalable SVG named with your app ID. Luma presents application artwork on
its own rounded tile with a corner radius of a quarter of the icon’s width, so
draw artwork that reads well inside a rounded square and do not bake in your
own drop shadow. See [icons](../design/icons.md).

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <rect width="128" height="128" rx="32" fill="#527fae"/>
  <rect x="38" y="30" width="52" height="68" rx="8" fill="#ffffff"/>
</svg>
```

## Flatpak manifest

```json
{
  "id": "com.example.Notebook",
  "runtime": "org.projectluma.Platform",
  "runtime-version": "44",
  "sdk": "org.projectluma.Sdk",
  "command": "com.example.Notebook",
  "finish-args": [
    "--share=ipc",
    "--socket=wayland",
    "--socket=fallback-x11",
    "--device=dri"
  ],
  "modules": [
    {
      "name": "notebook",
      "buildsystem": "simple",
      "build-commands": [
        "install -Dm755 src/main.py /app/bin/com.example.Notebook",
        "install -Dm644 data/com.example.Notebook.desktop /app/share/applications/com.example.Notebook.desktop",
        "install -Dm644 data/com.example.Notebook.metainfo.xml /app/share/metainfo/com.example.Notebook.metainfo.xml",
        "install -Dm644 data/com.example.Notebook.svg /app/share/icons/hicolor/scalable/apps/com.example.Notebook.svg"
      ],
      "sources": [
        {
          "type": "dir",
          "path": ".."
        }
      ]
    }
  ]
}
```

This manifest targets `org.projectluma.Platform//44` from the Luma remote (see
[install the SDK](install-the-sdk.md)). The runtime already carries LumaUI,
Figtree and the Prairie icons, so the manifest does not build LumaUI itself.
Build, install and run it from the project root:

```sh
flatpak-builder --user --install --install-deps-from=luma --force-clean build-dir flatpak/com.example.Notebook.json
flatpak run com.example.Notebook
```

What the SDK generates today: `luma new` writes a manifest on
`org.gnome.Platform//50` whose first module builds `luma-developer-platform`
from Luma’s source repository. That repository is not public, so the generated
manifest does not build as it is. Replace it with a manifest like the one
above: switch `runtime`, `sdk` and `runtime-version` to the Luma runtime and
remove that module. Luma’s own Depot apps build on the Luma runtime.

Permissions: `--share=ipc`, `--socket=wayland`, `--socket=fallback-x11` and
`--device=dri` are the normal baseline for a GTK app. Everything else is a
permission people see before they install. Read
[permissions and privacy](../permissions-and-privacy.md) before adding any
`finish-args`.

## Evidence file

`verification/luma-native-evidence.json` records the results of the test lanes
that make an app a Native candidate: both architectures, desktop and mobile
presentations, and accessibility, adaptive layout, keyboard, portals, reduced
motion, sandbox, semantics, uninstall, and update and rollback. Every value
starts `false`. Only real CI results should change them;
`luma release-check` also requires a pinned source revision, a timestamp, an
SBOM and a test report. See [the `luma` command](../kit/sdk-command-line.md#luma-release-check).

```json
{
  "schema_version": "0.1",
  "application_id": "com.example.Notebook",
  "tested_at": null,
  "source_revision": null,
  "architectures": {
    "x86_64": false,
    "aarch64": false
  },
  "presentations": {
    "desktop": false,
    "mobile": false
  },
  "lanes": {
    "accessibility": false,
    "adaptive_layout": false,
    "keyboard": false,
    "portals": false,
    "reduced_motion": false,
    "sandbox": false,
    "semantics": false,
    "uninstall": false,
    "update_and_rollback": false
  },
  "artifacts": {
    "sbom": null,
    "test_report": null
  }
}
```
