---
title: Permissions and privacy
summary: How Depot computes and shows an app’s permissions from its Flatpak metadata, the full permission vocabulary, the portal to use for each need, what triggers extra review, sign-in disclosure, and the data handling Luma expects.
order: 1
section: Permissions and privacy
---

# Permissions and privacy

People decide whether to install your app partly on what it can reach. Depot
shows that in plain language before install, computed from your app’s real
sandbox, and highlights anything that grows between releases. The best
permission list is a short one.

## How permissions are shown

You never type permissions into Depot. Luma reads the Flatpak `metadata` of
the built app (its `[Context]`, `[Session Bus Policy]` and
`[System Bus Policy]` sections) and maps each grant to a key in the vocabulary
below. Listed apps from Flathub go through the same mapping, run on Flathub’s
published metadata, and Depot on Luma shows the result.

Each key has a level:

| Level | Shown | Review |
| --- | --- | --- |
| `standard` | In the permissions list | Normal for the kind of app |
| `sensitive` | Marked | Checked at human review |
| `high` | Marked | Requires a written justification at review |

## The permission vocabulary

| Key | Comes from | Shown to people as | Level |
| --- | --- | --- | --- |
| `network` | `--share=network` | Uses the internet | standard |
| `files.portal` | No home or host filesystem grant, so files come through the file chooser portal | Opens files you choose | standard |
| `files.documents` | `--filesystem=xdg-documents` | Reads and changes your Documents | sensitive |
| `files.pictures` | `--filesystem=xdg-pictures` | Reads and changes your Pictures | sensitive |
| `files.music` | `--filesystem=xdg-music` | Reads and changes your Music | sensitive |
| `files.videos` | `--filesystem=xdg-videos` | Reads and changes your Videos | sensitive |
| `files.downloads` | `--filesystem=xdg-download` | Reads and changes your Downloads | sensitive |
| `files.other` | Any other `--filesystem=` path (each named) | Reads and changes the named folders | sensitive |
| `files.removable` | `--filesystem=` under `/media`, `/run/media` or `/mnt` | Reads and changes removable drives | sensitive |
| `files.home` | `--filesystem=home` | Reads and changes everything in your home folder | high |
| `files.host` | `--filesystem=host`, `host-os`, `host-etc` or `host-root` | Reads and changes all files on this computer | high |
| `devices.camera` | `--device=all` | Uses the camera | sensitive |
| `devices.microphone` | `--socket=pulseaudio`, or `--filesystem=xdg-run/pipewire-0` | Records audio | sensitive |
| `devices.all` | `--device=all` | Uses any connected device | high |
| `devices.input` | `--device=input` | Reads input devices such as game controllers | sensitive |
| `devices.usb` | `--device=usb` | Uses USB devices | sensitive |
| `devices.bluetooth` | `--allow=bluetooth` | Uses Bluetooth directly | sensitive |
| `devices.smartcard` | `--socket=pcsc` | Uses smart cards | sensitive |
| `devices.kvm` | `--device=kvm` | Runs virtual machines | high |
| `notifications` | `--talk-name=org.freedesktop.Notifications` | Sends notifications | standard |
| `location` | `--system-talk-name=org.freedesktop.GeoClue2` | Knows your location | sensitive |
| `printing` | `--socket=cups` | Prints directly | standard |
| `system.bus` | `--system-talk-name=` (each named) | Talks to system services | high |
| `system.bus` | `--socket=system-bus` | Talks to any system service | high |
| `system.ssh-agent` | `--socket=ssh-auth` | Uses your SSH keys | high |
| `system.gpg-agent` | `--socket=gpg-agent` | Uses your GnuPG keys | high |
| `session.bus` | `--talk-name=` or `--own-name=` beyond portals (each named) | Talks to other apps | sensitive |
| `session.bus` | `--socket=session-bus` | Talks to any other app | high |
| `display.x11` | `--socket=x11` without `--socket=fallback-x11` | Can see other X11 windows | high |
| `sandbox.escape` | `--talk-name=org.freedesktop.Flatpak` | Can run commands outside its sandbox | high |
| `sandbox.devel` | `--allow=devel` | Can inspect other processes (developer mode) | high |

A few details:

- A read-only grant (for example `--filesystem=xdg-documents:ro`) is shown as
  “Reads” instead of “Reads and changes”.
- Named keys list each name beside the sentence, for example “Talks to other
  apps (org.freedesktop.secrets)”.
- Portals ask for consent themselves, so talking to
  `org.freedesktop.portal.*` adds nothing. Owning your own app ID’s bus names,
  or an `org.mpris.MediaPlayer2.*` name, adds nothing either.
- If your app has a `luma-app.toml`, `luma lint` rejects every `--filesystem=`
  grant and the broadest grants above, so a Luma tier app reaches files only
  through portals. See [`luma-app.toml`](getting-started/luma-app-toml.md).

## Portals first

Use a portal whenever one exists. The person stays in control, nothing is
granted up front, and your permission list stays short.

| You need to | Use | Instead of |
| --- | --- | --- |
| Open a file the person picks | `Gtk.FileDialog` (file chooser portal) | `--filesystem=home` |
| Save or export a file | `Gtk.FileDialog.save()` | `--filesystem=xdg-documents` |
| Open a folder the person picks | `Gtk.FileDialog.select_folder()` | `--filesystem=home` |
| Reopen recent documents | Keep the document portal path you were given; store it in your app data | A static filesystem grant |
| Open a link or a file in another app | `Gtk.UriLauncher`, `Gtk.FileLauncher` (open URI portal) | Spawning a browser |
| Send a notification | `Gio.Application.send_notification()` | Talking to the notification daemon |
| Take a photo or video | The camera portal, through PipeWire | `--device=all` |
| Record audio | GStreamer or PipeWire with the audio socket (`devices.microphone`) | `--device=all` |
| Share the screen | The screencast portal | `--socket=x11` |
| Keep working after the window closes | A background agent and `request_background()` (the Background portal inside Flatpak) | An autostart file, or a hidden window |
| Prevent sleep during a task | `Gtk.Application.inhibit()` | A system bus call |
| Print | `Gtk.PrintOperation` (print portal) | `--socket=cups` |
| Store a password or token | libsecret (secret portal) | Writing it to a file |
| Know the location | The location portal | Network geolocation of your own |
| Read the light or dark preference | LumaUI, or `Adw.StyleManager` (settings portal) | Reading `~/.config` |
| Move a file to Trash | `Gio.File.trash()` (trash portal) | Deleting it |

List the portals your app uses in `luma-app.toml` under
`distribution.portals`. See [`luma-app.toml`](getting-started/luma-app-toml.md#distribution).

Using a portal keeps a permission off your list entirely: the camera,
location and notification portals add nothing. Using `--device=all` instead of
the camera portal, for example, shows “Uses the camera” and “Uses any connected
device”.

## What triggers extra review

The [developer program](publishing/developer-program.md) is not open yet, so
today these rules apply to Luma’s own releases. A release is held for a person
when any of these is true:

1. It is your app’s first release in the Verified or Luma tier.
2. Its computed permissions **grow** compared with the release published on
   the same branch: a new key, a higher level, read-only access becoming read
   and write, or a new named folder or bus name. The release is not signed
   until a person approves it, and the change is recorded as
   `permission_changes` and shown in Depot and on the web listing.
3. It requests any `high` permission for the first time. You must give a
   justification that review accepts.
4. It is in the Luma tier. Every Luma tier release gets a human design and
   privacy review.

Every other release still runs the automated checks. Removing a permission
never triggers review.

On people’s devices, Depot does not install an update that widens access in the
background either; it waits until the person has seen what changes. See
[updates and permission changes](publishing/updates-and-permission-changes.md#when-a-release-asks-for-more).

## Sign-in disclosure

Every Depot listing says whether and how an app asks people to sign in. You
will declare it on your store page in the developer portal once it opens, and
review checks it against the app. Until a value is declared and checked, the
listing says “Not declared yet”.

| Value | Means | Example |
| --- | --- | --- |
| `none` | The app never asks anyone to sign in | A calculator, a local photo editor |
| `luma` | Signing in uses a Luma Connect account | An app that syncs with Luma Sync |
| `third-party` | Optional sign-in to another service; the core app works without it | A notes app that can sync to a third-party cloud |
| `required-third-party` | The app is not usable without an account on another service | A client for a chat service |

If you ask for sign-in anywhere, including for optional features, the value is not
`none`.

## Data handling expectations

These apply to Verified and Luma apps and are enforced through the
[review guidelines](publishing/review-guidelines.md).

**Collect less.** Collect only the data a feature needs, and only when the person
uses that feature.

**Keep it local by default.** Store app data in your sandbox’s data directory
(`GLib.get_user_data_dir()` inside Flatpak resolves to
`~/.var/app/<app-id>/data`). Do not send a person’s content off the device
unless that is what the feature visibly does.

**Say what leaves the device.** If your app sends any personal data or content to
a server, including crash reports, analytics or usage metrics, you must:

- provide a privacy policy URL in your listing;
- tell people in the app before the first transfer;
- let people turn off anything that is not essential to the feature.

**No hidden tracking.** No advertising identifiers, fingerprinting, or third-party
analytics that follow people across apps or websites.

**Secrets stay secret.** Store passwords and tokens with libsecret. Never log them,
put them in a semantic object, or send them anywhere but their service.

**Respect removal.** Keep app data, cache and user documents in their proper
places so Luma can offer truthful “keep data” and “remove app data” choices,
and so a reset really resets. Never delete documents a person made when your app
is uninstalled.

**Children.** Declare an accurate OARS content rating in your metainfo. Do not
knowingly collect personal data from children without the consent the law
requires.

**Security.** Use TLS for every network connection, validate certificates, keep
bundled libraries patched, and ship fixes for known vulnerabilities promptly.

## Background activity

An app that must keep working with its windows closed ships a small
[background agent](kit/background-agents.md) rather than keeping its interface
running.

- **Ask at a moment people understand.** Call `request_background()` after the
  person does something that needs it, such as adding an account. Luma asks
  “Let *App* work in the background?” with your category’s sentence. Asking at
  launch is rejected at review, and an app that was declined must not ask
  again.
- **The category is a promise.** `communication`, `calendar` and `alarms` keep
  running in Power Saver because people rely on them. Declaring one of them
  for anything else is rejected at review.
- **Agents run as your app and no more.** A Flatpak agent runs inside your
  sandbox and gets no permission your app does not have. Its memory use is
  limited by its category.
- **Decisions are the person’s.** A person can turn background activity off at
  any time in **Settings › Apps › Background Activity**, including for Luma’s
  own apps. When your agent is not running, your app must still work when it is
  opened.

## Semantics and agents

If your app publishes to the Semantic Broker, classify objects honestly (`public`,
`private`, `sensitive`, `secret`) and give every action a truthful risk. Luma
never gives an agent ambient access to your app, and consequential, destructive
and security-sensitive actions always get a fresh confirmation. See
[semantics and actions](kit/semantics-and-actions.md).
