---
title: Publishing to Depot
summary: How publishing on Depot works, from applying to the developer program to an app In Depot, how Luma builds and signs the apps on its remote, and where an app is listed.
order: 1
section: Publishing
---

# Publishing to Depot

Depot is Luma’s app store. It is on every Luma device. Each app will also have
a page on the web at `simplyluma.com/apps/<slug>`, built from the same catalog;
those pages are offline for now.

> **Status.** The developer program is open. Apply, manage your apps and
> submit releases in Luma Connect at
> [`simplyluma.com/connect/developer`](https://simplyluma.com/connect/developer).
> Releases are signed into the Luma Flatpak remote at `dl.simplyluma.com`.

## How publishing works

1. Sign in with a Luma Connect account and
   [apply to the developer program](developer-program.md).
2. Luma approves your application.
3. Submit your app: the listing (name, summary, description, icon,
   screenshots and **What’s new**) and
   the package. See [submitting a release](submitting-a-release.md).
4. Your submission goes through **checks**, then **review**, then it is
   **In Depot**.
5. Every update carries its own **What’s new**. The app page shows the version
   history.

## Get your app ready

- Build against the Luma runtime and SDK from the Luma remote. See
  [install the SDK](../getting-started/install-the-sdk.md).
- Choose an [app ID](app-ids-and-namespaces.md) you can prove you control.
- Write [metainfo and screenshots](metainfo-and-screenshots.md).
- Make your manifest build offline from pinned sources, and run the same checks
  Luma runs on its own releases. See
  [submitting a release](submitting-a-release.md#test-the-build-yourself).
- Read the [review guidelines](review-guidelines.md) and keep your
  [permissions](../permissions-and-privacy.md) small.

## What happens to a release on the Luma remote

This is the path a release on the Luma remote follows:

1. Luma builds it with `flatpak-builder` from the manifest. Every `git` source
   must pin a full 40-character commit.
2. `appstreamcli validate --no-net` checks the metainfo, `desktop-file-validate`
   checks the desktop entry, and `flatpak-builder-lint` checks the manifest,
   the build directory and the exported repository.
3. Its permissions are computed from the built app’s Flatpak metadata and
   compared with the release already published on the same branch. If they
   grow, publishing stops until a person approves the change.
4. It is signed into the Luma remote with the Luma Depot key. The remote’s
   summary and static deltas are regenerated and a `.flatpakref` file is
   written for the app.

Nothing unsigned is published.

## Where an app is listed

- **Depot on Luma**, with an **Install** button.
- **`simplyluma.com/apps/<slug>`**, a shareable page with screenshots,
  permissions, sign-in disclosure and the developer, and a **Get** button that
  opens Depot on Luma or explains how to install elsewhere.
- **Other Linux systems**, through the app’s `.flatpakref` file. See
  [Open in Depot links](open-in-depot.md).
- **Collections** that Luma curates. Today they are Office, Creative and
  Studio, shown as rows on Depot’s home page.

## Tiers

| Tier | Means |
| --- | --- |
| Luma | Built with LumaUI and reviewed by Luma for design, accessibility, privacy and quality. Shown as “Made for Luma”. |
| Verified | Published by a developer whose identity Luma has verified, hosted and signed by Luma. This is the tier for apps from the developer program. |
| Listed | A known app Luma lists so people can find it. It installs from its publisher or Flathub, and Luma does not host it or review it for quality. |

## Visibility

Every listing has one of these:

| Visibility | Who sees it |
| --- | --- |
| `public` | Everyone |
| `unlisted` | Anyone with the link; not shown in lists, search or collections |
| `private` | The developer, Luma and the listing’s testers only |
| `withdrawn` | Nobody can install it. Installed copies keep working, are marked end of life through Flatpak, and get no more updates. |

## Before you submit

- [ ] `luma lint` passes, and `appstreamcli validate` and
      `flatpak-builder-lint` pass locally.
- [ ] Your app ID is in a namespace you control.
- [ ] Your permissions are as small as they can be; see
      [permissions and privacy](../permissions-and-privacy.md).
- [ ] Your metainfo has a license, a description, screenshots, release notes and
      an OARS content rating.
- [ ] You know your [sign-in declaration](../permissions-and-privacy.md#sign-in-disclosure).
- [ ] You have read the [review guidelines](review-guidelines.md).
