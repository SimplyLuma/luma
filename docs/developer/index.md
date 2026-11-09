---
title: Build for Luma
summary: What building a Luma app means, the three Depot tiers, how publishing works, and where to start.
order: 1
section: Overview
---

# Build for Luma

Luma is a free, open-source operating system for desktops, tablets and phones.
Its apps are ordinary Linux applications: GTK 4 as the engine, Flatpak for
packaging and sandboxing, portals for access to files and devices, and
AppStream for store metadata. On that foundation Luma adds **LumaUI**, its own
app toolkit, which draws the Luma window, islands and every component and gives
apps commands, design tokens and one adaptive layout from phone to desktop. It
also adds Depot, Luma’s app store.

These docs cover everything from your first window to your first release.

> **Maturity.** Luma is in alpha, and the Luma Developer Platform is a **0.x
> preview**. APIs can change between releases and are not yet ABI stable.
> Where something is not available yet, these docs say so and describe what
> exists today. See the [changelog](CHANGELOG.md) for what changed in the docs.

## What building for Luma means

A Luma app is:

- **One app for every screen.** The same source and the same binary run on a
  desktop, a tablet and a phone. Width changes the layout; it never identifies
  a device. Nothing is removed on a small screen; it moves into navigation,
  sheets or overflow.
- **Built on upstream standards.** GTK 4, Flatpak, portals, PipeWire,
  GStreamer and AppStream do the heavy lifting. Luma metadata is additive, so a
  Luma app still runs on other Linux distributions.
- **Composed, not drawn.** LumaUI draws the window frame, the title row, the
  app identity, the window controls, the islands and every component in them.
  Your app says what it needs, arranges those pieces and draws only its own
  content, so a change to the Luma design reaches your app without a change to
  your code.
- **Private by default.** Apps reach files and devices through portals, declare
  what they need, and show people what they can reach before they install.

## The stack at a glance

| Layer | What it is | Details |
| --- | --- | --- |
| Engine | GTK 4 (rendering, input, accessibility) | [The stack](platform/the-stack.md) |
| Toolkit | LumaUI: `LumaUI-1` (C, introspectable) and its Python package `luma_appkit`, plus `LumaSemantics-1` | [LumaUI overview](kit/index.md) |
| SDK tools | The `luma` command: `new`, `lint`, `inspect`, `test`, `release-check` | [The `luma` command](kit/sdk-command-line.md) |
| Languages | Python 3 (PyGObject), C, C++ | [The stack](platform/the-stack.md#languages) |
| Packaging | Flatpak, on the `org.projectluma.Platform//44` runtime from the Luma remote | [Runtimes and distributions](platform/runtimes-and-distributions.md) |
| Store | Depot, on Luma | [Publishing](publishing/index.md) |

## Tiers in Depot

Every app in Depot carries a tier that tells people how much Luma stands behind
it.

| Tier | What it tells people | Who publishes | How it is reviewed |
| --- | --- | --- | --- |
| **Luma** (“Made for Luma”) | Built with LumaUI and reviewed by Luma for design, accessibility, privacy and quality. It looks and behaves like part of the system. | Luma, or a verified developer Luma has invited to the tier | Automated checks and a human design and privacy review of every release |
| **Verified** | Published by a developer whose identity Luma has verified, hosted and signed by Luma, with reviewed permissions | Any approved developer | Automated checks on every release; human review of the first release and of any release that widens permissions |
| **Listed** | A known app Luma lists so you can find it. It installs from its own source, such as Flathub or the publisher, and is not reviewed by Luma for quality. | Chosen by Luma; suggestions from anyone with a Luma account open soon | Provenance check only: the source really is the publisher’s |

A Listed app becomes Verified when its publisher joins the developer program
and publishes to the Luma remote. Verified apps move to the Luma tier by
invitation after review. The full rules are in the
[review guidelines](publishing/review-guidelines.md).

## How publishing works

> **Not open yet.** The developer program has not opened. Applying, “Your
> apps” and “Suggest an app” open soon; today Depot carries Luma’s own apps and
> apps Luma lists. The steps below are how publishing works once it opens.

1. **Join the developer program.** It is free. You apply from the Developer tab
   in Hub and verify your identity.
   [The developer program](publishing/developer-program.md)
2. **Claim your app ID.** Prove you control the domain or code forge account
   your reverse-DNS ID names.
   [App IDs and namespaces](publishing/app-ids-and-namespaces.md)
3. **Prepare your store page.** AppStream metainfo, an icon, screenshots and
   release notes. [Metainfo and screenshots](publishing/metainfo-and-screenshots.md)
4. **Submit a release.** Point Luma at a tagged source commit and your Flatpak
   manifest, or upload a signed bundle if your app is proprietary. Luma builds,
   checks, signs and hosts it on the Luma remote.
   [Submitting a release](publishing/submitting-a-release.md)
5. **Pass review.** Automated checks run on every release. People review your
   first release and any release that asks for more access.
6. **Ship.** Your app appears in Depot on Luma, and installs on any
   distribution with Flatpak.

## Where to start

- New to Luma: [install the SDK](getting-started/install-the-sdk.md), then
  build [your first app in Python](getting-started/first-app-python.md) or
  [in C](getting-started/first-app-c.md).
- Already have a GTK 4 app: read
  [bringing an existing GTK app](getting-started/existing-gtk-apps.md) and
  [windows and islands](kit/windows-and-islands.md).
- Designing: start with the [design principles](design/principles.md).
- Ready to ship: read [permissions and privacy](permissions-and-privacy.md) and
  the [review guidelines](publishing/review-guidelines.md) before you submit.
- Stuck: check the [FAQ](knowledge-base/faq.md) and
  [troubleshooting](knowledge-base/troubleshooting.md).

## What exists today

| Piece | Status |
| --- | --- |
| LumaUI (`LumaUI-1` and `luma_appkit`), `LumaSemantics-1` | Preview, shipping in every Luma image as `luma-developer-platform` |
| `luma` SDK command | Preview, packaged as `luma-developer-platform-sdk`; not on PyPI |
| Semantic Broker and `luma semantic` inspector | Preview; the broker comes with `luma-developer-platform`, the inspector with `luma-developer-platform-sdk` |
| Luma’s own apps on LumaUI | Canvas, Reel, Focus, Tide, Grid, Stage, Session and Write, among others |
| `org.projectluma.Platform//44` and `org.projectluma.Sdk//44` runtimes | Live on the Luma remote, `x86_64` only |
| The signed Luma remote at `dl.simplyluma.com/repo` | Live; Viola is on the `stable` branch, Luma’s other apps on `beta` |
| Developer program, “Your apps” and release submission in Hub | Not open yet; opens soon |
| “Suggest an app” | Not open yet; opens soon |
| `luma-depot://` links | Opened by Depot on Luma |
