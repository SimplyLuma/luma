---
title: The luma command
summary: Reference for the SDK command line, luma new, lint, inspect, test, release-check, live-preview and semantic, with usage, output and exit codes.
order: 8
section: Kit
---

# The `luma` command

The `luma` command (also installed as `luma-sdk`) comes with the
`luma-developer-platform-sdk` package, which is installed on Luma and included
in the `org.projectluma.Sdk//44` Flatpak SDK. It is not published on PyPI. It
creates projects and checks them against the
[application contract](../getting-started/luma-app-toml.md). It is version
**0.1.0 (preview)**.

```text
usage: luma [-h] [--version]
            {new,lint,inspect,test,release-check,live-preview,semantic} ...
```

| Command | What `--help` says |
| --- | --- |
| `new` | create a responsive GTK application |
| `lint` | validate a Luma application contract |
| `inspect` | inspect platform and conformance metadata |
| `test` | run the local conformance contract matrix |
| `release-check` | verify evidence required for Luma Native status |
| `live-preview` | preview a Live Extension contract |
| `semantic` | inspect and exercise the live Semantic Broker (developer only) |

`lint`, `inspect`, `test` and `release-check` take an optional manifest path
and default to `luma-app.toml` in the current directory. A wrong or missing
argument prints the usage line and exits with code 2.

## `luma new`

```text
usage: luma new [-h] --id APP_ID [--destination DESTINATION] name
```

Creates a project from the Python template: `luma-app.toml`, a desktop entry,
AppStream metainfo, a starter SVG icon, JSON and YAML Flatpak manifests, a
`run-app` launcher, `src/main.py`, a README, and an evidence file with every
gate `false`. On success it prints `Created <name> at <destination>`.

- `--id` is required. `name` is the display name; quote it if it has spaces.
- `--destination` defaults to the current directory.
- Exits with code 2 and an `error:` line if `APP_ID` is not a reverse-DNS ID
  or the destination exists and is not empty.

What the template contains today, so you are not surprised:

- `src/main.py` uses `LumaUI` through introspection (`LumaUI.ActionBar`,
  `LumaUI.AdaptiveScaffold`) inside a plain `Adw.ApplicationWindow`. It does
  not use the Python kit’s `AppWindow`, so it does not get the full Luma window.
  For a kit-based start, replace it with the code in
  [your first app in Python](../getting-started/first-app-python.md).
- The Flatpak manifest targets `org.gnome.Platform//50` and builds
  `luma-developer-platform` from source as a module; see
  [runtimes and distributions](../platform/runtimes-and-distributions.md#the-gnome-runtime-in-the-sdk-template).
  The YAML manifest is shorter than the JSON one and does not install the
  desktop entry, metainfo or icon.
- The command is named `run-app`. Rename it to something unique to your app, such
  as your app ID, in the desktop entry `Exec`, the manifest `command` and the
  install commands.

## `luma lint`

```text
usage: luma lint [-h] [manifest]
```

Validates the static application contract: identity agreement between
`luma-app.toml`, the Flatpak manifest, metainfo, desktop entry and icon; required
widths, inputs and presentations; the Flatpak permission policy; portals;
lifecycle; and that the entry point and evidence file exist. The full rule list
is in [`luma-app.toml`](../getting-started/luma-app-toml.md).

| Exit | Output |
| --- | --- |
| 0 | `Luma application contract passed: <path>` |
| 1 | One `error: ...` line per problem, on standard error |

Run it in CI on every commit.

## `luma inspect`

```text
usage: luma inspect [-h] [manifest]
```

Prints a JSON report with your `application`, `platform`, `responsive`,
`distribution` and `lifecycle` tables, the derived `status` (`Invalid`,
`Development` or `Native candidate`), `native_candidate`, the `status_gates`
still open, and `errors`. Exit code 0 when there are no contract errors,
otherwise 1. A new project reports `Development`, with every unmet evidence gate
listed.

## `luma test`

```text
usage: luma test [-h] [manifest]
```

Prints the width and input matrix your app must pass, one line per combination
(`contract: width=360 input=touch`), then runs the same checks as `lint`. It does
not open your app. On success it prints:

```text
Static adaptive and semantic contracts passed.
Runtime screenshot, keyboard, screen-reader, and portal lanes require the Luma SDK image.
```

On failure it prints the `error:` lines and exits with code 1. The runtime
lanes (screenshots at each width, keyboard-only use, screen reader, portals)
are not run by this command. Run them yourself or in CI and record the results
in the evidence file.

## `luma release-check`

```text
usage: luma release-check [-h] [manifest]
```

Everything `lint` checks, plus the release evidence: a schema 0.2 contract; an
evidence file (schema 0.1) bound to your app ID with a 40 to 64 character
lowercase hex source revision and an ISO 8601 `tested_at`; passing results for
`x86_64` and `aarch64`, desktop and mobile, and all nine lanes; existing SBOM
and test report files; `git` sources pinned to a commit; and `archive` or
`file` sources with `sha256`.

| Exit | Output |
| --- | --- |
| 0 | `Luma Native candidate evidence passed: <path>` |
| 1 | `error:` lines, then `This project is structurally valid but has not earned Luma Native status.` |

Passing this command does not award a Depot tier. Review decides the tier.

## `luma live-preview`

```text
usage: luma live-preview [-h] activity
```

Reads a Live Extension payload from the JSON file `activity`, checks it (a
supported `category`, a `title`, at most three `actions`) and prints how it
would read on each surface:

```text
desktop-top-bar: Tea (1 actions)
tablet-action-strip: Tea (1 actions)
phone-compact: Tea (1 actions)
lock-screen: Private activity (1 actions)
```

Supported categories are `call`, `media`, `timer`, `navigation`, `event`,
`transfer`, `recording`, `installation` and `generic`. The lock-screen line
shows “Private activity” when `privacy` is `sensitive` or `secret`. Problems
print `error:` lines and exit with code 1. This is a text preview; it does not
render anything.

## `luma semantic`

```text
usage: luma semantic [-h] {list,get,request,revoke,invoke,audit} ...
```

```text
luma semantic list
luma semantic get publication_id
luma semantic request --scope SCOPE [--scope SCOPE ...] [--duration DURATION] application_id
luma semantic revoke application_id
luma semantic invoke [--parameter PARAMETER] publication_id object_id action_id
luma semantic audit [--limit LIMIT]
```

A developer client for the live [Semantic Broker](semantics-and-actions.md) on
Luma. It runs itself inside a managed systemd user scope as the Semantic
Inspector so the broker can identify it, and it goes through the same consent
dialog and confirmations as any client; it has no bypass.

| Subcommand | Does |
| --- | --- |
| `list` | Lists surfaces visible to your current grants |
| `get` | Prints one visible surface |
| `request` | Shows the consent dialog for `application_id` and requests a session grant. `--scope` is required and can be repeated: `observe-public`, `observe-private`, `invoke-low-risk`, `invoke-consequential`, `invoke-destructive`, `invoke-security-sensitive`. `--duration` is in seconds and defaults to 3600. |
| `revoke` | Revokes the grant for `application_id` |
| `invoke` | Invokes an action through broker policy, with confirmation where the risk requires it. `--parameter` is a JSON value and defaults to `{}`. |
| `audit` | Prints this inspector’s own payload-free audit entries. `--limit` defaults to 50. |

Output is JSON. Broker errors print an `error:` line and exit with code 2.

## `luma --version`

Prints `luma-sdk 0.1.0`.
