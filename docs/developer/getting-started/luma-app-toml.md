---
title: luma-app.toml
summary: The Luma application contract file, key by key, with the exact rules luma lint enforces for schema 0.2.
order: 5
section: Getting started
---

# `luma-app.toml`

`luma-app.toml` sits at the root of your project. It declares your app’s
identity, the presentations and inputs it supports, where its packaging files
live, what happens to its data, and where its test evidence is. The `luma`
command reads it and checks it against the rest of your project.

It adds nothing to your package format. Your app is still a Flatpak with
AppStream metadata and a desktop entry, and it still runs on other
distributions. The file only makes Luma’s rules checkable.

> **Versions.** Use `schema_version = "0.2"`. Schema 0.1 is still accepted by
> `luma lint` because some of Luma’s own apps still use it, but it skips every
> distribution and lifecycle check and `luma release-check` rejects it. A JSON
> Schema for 0.2 ships in `luma-developer-platform-devel` (and so in the Flatpak
> SDK) at `/usr/share/luma-platform/schemas/luma-application-v0.2.schema.json`.

## Complete example

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

All paths are relative to the project root and may not be absolute or contain
`..`.

## `schema_version`

| Key | Type | Rule |
| --- | --- | --- |
| `schema_version` | string | `"0.2"` (or `"0.1"`, legacy) |

## `[application]`

| Key | Type | Rule |
| --- | --- | --- |
| `id` | string | Reverse-DNS app ID. At least three parts; starts with a letter; letters, digits and underscores only; first two parts lowercase; must not end in `.desktop`. |
| `name` | string | Required. The display name, up to 80 characters in the JSON Schema. |
| `version` | string | Required. Your release version. Keep it equal to the newest `<release>` in your metainfo. |
| `entrypoint` | path | Required. The file that starts your app; it must exist. |

## `[platform]`

| Key | Type | Rule |
| --- | --- | --- |
| `ui_contract` | string | Must be `"1"`: the `LumaUI-1` contract. |
| `semantics_contract` | string | Must be `"1"`: the `LumaSemantics-1` contract. |

## `[responsive]`

This is where your app promises to be one app on every screen.

| Key | Type | Rule |
| --- | --- | --- |
| `adaptive` | boolean | Must be `true`. |
| `test_widths` | array of integers | Must include `360`, `500` and `1024`. Add your widest designed width. Widths are logical pixels; the JSON Schema allows 320 to 3840. |
| `input_modes` | array of strings | Must include `pointer`, `touch` and `keyboard`. `pen` is also allowed. |
| `presentation_modes` | array of strings | Must include `windowed` and `fullscreen-mobile`. |

`luma test` prints the width and input matrix it expects you to cover. Width
selects layout; presentation and input select chrome and target sizes. See
[adaptive layouts](../kit/adaptive-layouts.md).

## `[distribution]`

| Key | Type | Rule |
| --- | --- | --- |
| `substrate` | string | Must be `"flatpak"`. |
| `flatpak_manifest` | path | Must exist and be `.json`, `.yaml` or `.yml`. Its `id` must equal `application.id`; `runtime`, `runtime-version`, `sdk` and `command` are required; `modules` must be non-empty. |
| `appstream_metadata` | path | Must exist and parse. Its `<id>` must equal the app ID, its desktop launchable must be `<id>.desktop`, and it must contain `<value key="org.projectluma.ApplicationContract">0.2</value>`. |
| `desktop_file` | path | Must be named `<id>.desktop`, contain `[Desktop Entry]` with `Type=Application`, a `Name`, `Icon=<id>`, and an `Exec` whose command equals the Flatpak `command`. |
| `icon` | path | Must exist and be named `<id>` plus an extension. |
| `portal_first` | boolean | Must be `true`. |
| `portals` | array of strings | The portals your app expects to use, without duplicates. |

Known portal names: `account`, `background`, `camera`, `file-chooser`,
`global-shortcuts`, `inhibit`, `location`, `microphone`, `network-monitor`,
`notifications`, `open-uri`, `printing`, `realtime`, `remote-desktop`,
`screencast`, `secret`, `settings`, `sharing`, `trash`, `wallpaper`.

Declaring a portal explains intent; it grants nothing. Access is enforced by
the Flatpak sandbox and by the portal asking the person at run time.

### Flatpak permissions `luma lint` rejects

For a Native candidate, `luma lint` fails on any of these in `finish-args`:

- any `--filesystem=` grant, including `xdg-documents` and `home`
- `--device=all`
- `--socket=session-bus`, `--socket=system-bus`, `--socket=x11`
- `--talk-name=` or `--system-talk-name=` containing a `*` wildcard

`--socket=fallback-x11` is allowed. These rules are stricter than Depot’s
permission vocabulary on purpose: they define a Native candidate. An app that
genuinely needs a static filesystem grant can still be published; Depot shows
the grant to people before they install, and grants at the `high` level need a
justification at review. See [permissions and privacy](../permissions-and-privacy.md).

## `[lifecycle]`

This is how Luma can offer a truthful uninstall and reset.

| Key | Type | Rule |
| --- | --- | --- |
| `app_data` | string | `"removable"` or `"retained"`. |
| `cache` | string | Must be `"removable"`. |
| `user_documents` | string | Must be `"preserved"`. Documents a person made are never deleted with the app. |
| `reset_supported` | boolean | Must be `true`. |
| `uninstall_choices` | array of strings | Must include `"keep-data"` and `"remove-app-data"`. |

## `[conformance]`

| Key | Type | Rule |
| --- | --- | --- |
| `accessibility` | boolean | Must be `true`. |
| `keyboard` | boolean | Must be `true`. |
| `semantics` | boolean | Must be `true`. |
| `reduced_motion` | boolean | Must be `true`. |
| `evidence` | path | Must exist. Checked in detail by `luma release-check`. |

These booleans are promises you make, not results. `luma lint` requires them to
be `true`, but they prove nothing on their own. Results live in the evidence
file, which only your CI should change.

## `[background]`

Optional. Declares a background agent: the small, windowless part of your app
that keeps working when its windows are closed.

```toml
[background]
agent = "com.example.Notebook.Agent"
exec = "com.example.Notebook --agent"
category = "sync"
wake = ["login", "network"]
```

| Key | Type | Rule |
| --- | --- | --- |
| `agent` | string | D-Bus name starting with `application.id` and a dot. |
| `exec` | string | Your app’s own command (the Flatpak `command`, or the desktop file’s `Exec` program when there is no manifest) and plain arguments: at most 32, each up to 256 characters. No quoting, field codes or shell syntax. |
| `category` | string | `communication`, `calendar`, `alarms`, `mail`, `sync`, `widget-data` or `other`. |
| `wake` | array of strings | One or more of `login`, `network`, `resume`, `schedule`. |
| `interval` | string | Optional repeating wake, `5m` to `7d`; needs `schedule` in `wake`. |
| `publishes` | array of strings | Optional value names (lowercase, starting with a letter, up to 64 characters) and `live-extension:<id>` entries. |

When `[background]` is present, `distribution.portals` must include
`background`. The full contract, including where the declaration is installed,
is in [background agents](../kit/background-agents.md).

## Evidence file

`luma release-check` requires the evidence file to contain:

| Key | Rule |
| --- | --- |
| `schema_version` | `"0.1"` |
| `application_id` | Equal to `application.id` |
| `source_revision` | A 40 to 64 character lowercase hex commit |
| `tested_at` | An ISO 8601 timestamp |
| `architectures` | `x86_64` and `aarch64` both `true` |
| `presentations` | `desktop` and `mobile` both `true` |
| `lanes` | `accessibility`, `adaptive_layout`, `keyboard`, `portals`, `reduced_motion`, `sandbox`, `semantics`, `uninstall` and `update_and_rollback` all `true` |
| `artifacts` | `sbom` and `test_report` paths that exist |

It also requires every `git` source in your Flatpak manifest to pin a full
`commit`, and every `archive` or `file` source with a URL to declare `sha256`.

## What the status means

`luma inspect` reports one of three statuses, derived from the files, never
typed in:

| Status | Meaning |
| --- | --- |
| `Invalid` | `luma lint` fails. |
| `Development` | The contract is valid; release evidence is incomplete. Every new project starts here. |
| `Native candidate` | The contract and release evidence both pass. |

`Native candidate` is a status the SDK derives on your machine. It is not a
Depot tier, and Depot does not read it today. Depot’s tiers (Luma, Verified
and Listed) are described in the
[review guidelines](../publishing/review-guidelines.md).
