---
title: Submitting a release
summary: How to submit an app or an update in Luma Connect, what the listing and package need, the checks and review every submission goes through, and how to prepare a Flatpak manifest that builds offline on the Luma runtime.
order: 5
section: Publishing
---

# Submitting a release

You submit apps and updates in Luma Connect, at
[`simplyluma.com/connect/developer`](https://simplyluma.com/connect/developer).
You need an approved [developer program](developer-program.md) account.

## Submit a new app

Start at [`/connect/developer/new`](https://simplyluma.com/connect/developer/new)
with the app’s name and app ID. Then fill in the listing and add the package.

The listing:

- **Name** and **summary**.
- **Description**.
- **Icon** and **screenshots**.
- **Permissions**: nothing to fill in. Depot reads them from your app’s
  sandbox settings (`finish-args`) and shows them to people.
  People see these before they install, and your package is checked against
  them.
- **What’s new** for this version.

The package: a Flatpak bundle, an RPM package, a Debian package or an
AppImage, for `x86_64`, `aarch64` or both, with its **version**.

## What happens next

Each submission shows where it is:

1. **Sent.**
2. **Checks.** The package is checked automatically.
3. **Review.** A person at Luma looks at it. If something needs to change,
   you are told what, and you send it again.
4. **In Depot.** People can install it.

## Send an update

Open the app from **Your apps** and start an update. Every update carries its
own **What’s new**, shown in Depot with the version. The app page shows the
version history.

## Prepare the manifest

Luma builds exactly what your manifest describes, so:

- **Pin every source.** `git` sources use a full 40-character `commit` (a `tag`
  alone is not enough); `archive` and `file` sources declare `sha256`. Luma’s
  build refuses a `git` source without a full commit, and
  `luma release-check` reports any unpinned source.
- **Vendor build-time downloads.** Assume the build has no network access, as
  on Flathub. Generate sources for language package managers (Python wheels,
  Cargo crates, npm packages) with the `flatpak-builder-tools` generators and
  commit the result.
- **Use the Luma runtime:** `runtime` `org.projectluma.Platform`, `sdk`
  `org.projectluma.Sdk`, `runtime-version` `44`.
- **Keep `finish-args` minimal.** Every entry becomes a permission people see.
  See [permissions and privacy](../permissions-and-privacy.md).
- **Install metainfo, the desktop entry and the icon** under `/app/share`,
  named with your app ID.

## Test the build yourself

Run the same tools Luma runs, from a clean checkout:

```sh
flatpak run org.flatpak.Builder --user --force-clean --install-deps-from=luma \
  --repo=repo build flatpak/com.example.Notebook.json
flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest flatpak/com.example.Notebook.json
flatpak run --command=flatpak-builder-lint org.flatpak.Builder repo repo
appstreamcli validate --no-net data/com.example.Notebook.metainfo.xml
desktop-file-validate data/com.example.Notebook.desktop
luma lint
```

The build step needs the `luma` remote and its runtime and SDK; see
[install the SDK](../getting-started/install-the-sdk.md) and
[runtimes and distributions](../platform/runtimes-and-distributions.md).

## The checks every release runs

These run, in order, on every Flatpak release Luma publishes to its remote:

| Check | Fails the release when |
| --- | --- |
| Build | The manifest does not build with `flatpak-builder`, or a `git` source is not pinned to a full commit |
| `appstreamcli validate --no-net` | The metainfo has errors |
| `desktop-file-validate` | The desktop entry has errors |
| `flatpak-builder-lint` | The manifest, build directory or exported repository has errors |
| Permissions | Never fails. It computes the permission list from the built app and compares it with the release already published on the same branch. |

`flatpak-builder-lint` encodes some Flathub policy that does not apply to the
Luma remote, such as Flathub’s own list of runtimes. Luma skips those named
checks; every other lint error fails the build.

If the permission comparison shows that the release asks for more than the
published one, the release is **held** and is not signed until a person
approves the change. See
[updates and permission changes](updates-and-permission-changes.md#when-a-release-asks-for-more).

Give every release a version newer than the last one, with a matching
`<release>` entry in your metainfo.

## Review

- **Verified tier:** a person reviews your **first release**, and any release
  whose **permissions grow**.
- **Luma tier:** every release gets a person’s design and privacy review.

Reviewers approve, request changes with a reason, or reject with a reason. See
the [review guidelines](review-guidelines.md).

## Branches

- **`beta`** is for people who choose to test.
- **`stable`** is what everyone gets.

Each app on the remote has a `.flatpakref` file for each branch it is on. See
[Open in Depot links](open-in-depot.md#installing-on-other-distributions).

## After publishing

The release is signed into the Luma remote with the Luma Depot key, the
remote’s summary and static deltas are regenerated, and the result is synced
to `dl.simplyluma.com`. Installed copies update through Flatpak; see
[updates and permission changes](updates-and-permission-changes.md).
