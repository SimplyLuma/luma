---
title: Updates and permission changes
summary: How Depot updates installed apps, beta and stable branches, what happens on the remote and on people's devices when a release asks for more permissions, withdrawing an app, and what suspension or revocation does to installs.
order: 7
section: Publishing
---

# Updates and permission changes

## How updates reach people

1. A release passes its checks and any review, and is signed into the Luma
   remote.
2. The remote’s static deltas are regenerated from each app’s previous commit
   and from empty, so an update downloads one compressed file of what changed.
   Older commits are pruned from the remote; a device that far behind still
   updates, by fetching objects one by one.
3. The catalog updates, so Depot shows the new version and its permissions.
4. On Luma, Depot updates the apps it installed every six hours in the
   background. It skips a round when **Update apps automatically** is off (it is
   on by default), the connection is metered, power saving is on, or the battery
   is low. A running app is never stopped: it moves to the new version the next
   time it is opened.

On other systems, installed copies update through Flatpak as usual.

Keep each release’s download small: do not bundle what the runtime already
provides, and keep large data out of your app when it can be downloaded on
demand with the person’s knowledge.

## Branches

| Branch | Who gets it | Use it for |
| --- | --- | --- |
| `beta` | People who choose to test | Trying a release with real users first |
| `stable` | Everyone | Releases you stand behind |

## When a release asks for more

Every release’s permissions are computed from its Flatpak metadata and compared
with the release already published on the same branch. A release **grows**
when it adds a permission, or widens one: a higher level, read-only access
becoming read and write (for example `files.documents`), or a new named bus
name or folder. Gaining the file chooser (`files.portal`) is not growth.

What happens then:

- **On the remote,** a release that grows is held and is not signed until a
  person approves the change, even if earlier releases no longer need review.
  The approved changes are recorded with the release as `permission_changes`.
- **On people’s devices,** Depot does not install an update that widens access
  in the background. It waits in the Updates tab, with each new or wider
  permission marked, until the person has looked at it and pressed Update.
  Depot sends one notification per such release, and it is the only kind of
  app update Depot notifies about.
- **On the web listing,** the change is shown beside the permissions.

A release that only removes permissions goes through as usual.

To make a permission change go smoothly:

1. Ask whether a portal can do the job instead. See
   [permissions and privacy](../permissions-and-privacy.md#portals-first).
2. Explain the new permission in your release notes, in plain words: what
   feature needs it and what you do with it.
3. Put the release on `beta` first.

Any new `high` permission needs a justification that review accepts.

## Changing your sign-in or data practices

If your app starts asking for an account, or starts sending data off the
device, update your sign-in declaration and privacy policy link in the same
release, and tell people in your release notes and in the app before it
happens. See [review guideline 2](review-guidelines.md#2-privacy).

## Withdrawing an app

A withdrawn app:

- disappears from Depot, search and collections, and can no longer be
  installed;
- keeps working for people who have it, and Flatpak tells them it has reached
  end of life;
- gets no more updates.

Tell people why in a final release note if you can, and recommend an
alternative if there is one. Once the developer portal opens, you will
withdraw an app from **Your apps**.

## If your developer account is suspended or revoked

| | New installs | Existing installs | New releases |
| --- | --- | --- | --- |
| **Suspended** | Hidden | Keep working | Blocked |
| **Revoked** | Listings withdrawn | Keep working, no updates | Blocked |

A revoked developer may appeal once. See
[the developer program](developer-program.md#suspension-and-revocation).

## Versioning

- Use a version scheme people can compare, such as semantic versioning
  (`1.4.2`) or date versions (`2026.10.1`).
- Every release needs a new, higher version and a matching `<release>` entry in
  your metainfo.
- Never reuse a version number for different contents.
