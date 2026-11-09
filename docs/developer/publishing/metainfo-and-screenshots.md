---
title: Metainfo and screenshots
summary: What Depot reads from your AppStream metainfo, a complete store-ready example, screenshot requirements, release notes, content rating, and how to validate.
order: 4
section: Publishing
---

# Metainfo and screenshots

Your store page is built from your app’s **AppStream metainfo**, the standard
file every Linux software center reads. Write it well once and your app looks
right in Depot and in software centers on other distributions.

Install it at `/app/share/metainfo/<app-id>.metainfo.xml`.

## What Depot shows, and where it comes from

| On your listing | From |
| --- | --- |
| Name | `<name>` |
| One-line summary | `<summary>` |
| Description | `<description>` |
| Icon | Your installed icon |
| Screenshots and captions | `<screenshots>` |
| Developer name | Your public developer name from the [developer program](developer-program.md) |
| Categories | `<categories>`, or your desktop entry’s `Categories=` |
| License | `<project_license>` |
| Homepage, help, bug tracker, source, privacy links | `<url>` elements |
| Version, date and release notes | The newest `<release>` |
| Age rating | `<content_rating type="oars-1.1">` |
| Permissions | Computed from your Flatpak; see [permissions and privacy](../permissions-and-privacy.md) |
| Sign-in | Your [sign-in declaration](../permissions-and-privacy.md#sign-in-disclosure) |
| Download and installed size | Measured from the build |
| Architectures | The architectures the release was built for |
| Tier, ratings, installs | Luma |

Luma copies your icon and screenshots to `dl.simplyluma.com` and records each
file’s SHA-256, which Depot checks. Nothing on a listing is loaded from your own
server.

When the developer portal opens, its store page will also let you edit the
name, summary, description, links, license and sign-in declaration.

## A store-ready example

This example passes `appstreamcli validate --no-net --pedantic`. The only
pedantic note is that the ID contains capital letters, which is normal.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>com.example.Notebook</id>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>Apache-2.0</project_license>

  <name>Notebook</name>
  <summary>Keep short notes</summary>
  <description>
    <p>Notebook keeps short notes in one adaptive window that works on a desktop,
    a tablet and a phone.</p>
    <ul>
      <li>Create a note with one keystroke</li>
      <li>Find any note as you type</li>
      <li>Works offline; your notes stay on your device</li>
    </ul>
  </description>

  <developer id="com.example">
    <name>Example Developer</name>
  </developer>

  <launchable type="desktop-id">com.example.Notebook.desktop</launchable>
  <provides>
    <binary>com.example.Notebook</binary>
  </provides>

  <categories>
    <category>Office</category>
  </categories>
  <keywords>
    <keyword>notes</keyword>
    <keyword>memo</keyword>
  </keywords>

  <url type="homepage">https://example.com/notebook</url>
  <url type="bugtracker">https://example.com/notebook/issues</url>
  <url type="help">https://example.com/notebook/help</url>
  <url type="vcs-browser">https://example.com/notebook/source</url>
  <url type="contact">https://example.com/contact</url>

  <supports>
    <control>pointing</control>
    <control>keyboard</control>
    <control>touch</control>
  </supports>
  <requires>
    <display_length compare="ge">360</display_length>
  </requires>

  <screenshots>
    <screenshot type="default">
      <caption>Notes in the sidebar and the editor</caption>
      <image type="source" width="2560" height="1600">https://example.com/notebook/screenshots/main.png</image>
    </screenshot>
    <screenshot>
      <caption>The same app on a phone</caption>
      <image type="source" width="1080" height="2400">https://example.com/notebook/screenshots/phone.png</image>
    </screenshot>
  </screenshots>

  <custom>
    <value key="org.projectluma.ApplicationContract">0.2</value>
  </custom>

  <content_rating type="oars-1.1"/>

  <releases>
    <release version="0.2.0" date="2026-10-01">
      <description>
        <p>Search as you type, and a new phone layout.</p>
      </description>
    </release>
    <release version="0.1.0" date="2026-09-15"/>
  </releases>
</component>
```

## Required for submission

Release submission is not open yet. When it opens, a release needs:

1. A valid file: `appstreamcli validate --no-net` passes with no errors.
2. `<id>` equal to your app ID, with a matching `<launchable>`.
3. `<name>`, a `<summary>` of about 35 characters or fewer that does not repeat
   the name, and a `<description>` of at least one paragraph.
4. `<metadata_license>` (use `CC0-1.0`) and an SPDX `<project_license>`. For a
   proprietary app use `LicenseRef-proprietary`.
5. `<developer>` with a `<name>`.
6. At least one `<screenshot>` with a caption.
7. A `<release>` for the version you are submitting, with a date and, from your
   second release on, a description of what changed.
8. `<content_rating type="oars-1.1">`, with every content attribute your app
   actually has declared. Generate it with the OARS questionnaire.
9. The `org.projectluma.ApplicationContract` custom value `0.2` if you use
   `luma-app.toml`. `luma lint` checks it.
10. A `<url type="homepage">`. If your app sends any personal data off the
    device, a privacy policy link in your listing too.

## Screenshots

Screenshots are the most important part of your listing.

**Requirements**

- PNG, of your **real app running**, showing real functionality.
- Hosted at a stable HTTPS URL. Luma keeps its own copy, so changing the file
  at your URL does not change your listing; submit a release with new
  screenshots instead.
- At least **1280 by 800** for desktop screenshots. The Luma reference size is
  **2560 by 1600** (a 16:10 window at 2x).
- No device frames, marketing text overlays, prices, ratings, or other apps’ or
  companies’ content you do not have rights to.
- No personal data of real people. Use sample data.

**Recommendations**

- Lead with the screenshot that shows what the app is for.
- Capture the window alone, with its frame, on Luma’s default light appearance.
  Add a dark appearance screenshot if it helps.
- Include one phone-width screenshot to show your app adapts.
- Use sentence case captions of five to ten words that say what is shown.
- Keep them current: update screenshots when the interface changes.

## Release notes

Write release notes for people, not for developers:

- Lead with what people will notice.
- Use short paragraphs or a list of up to about five items.
- Mention new permissions and why you need them.
- Leave out internal refactors and dependency bumps unless they fix something
  people saw.

## Validate

```sh
appstreamcli validate --no-net data/com.example.Notebook.metainfo.xml
desktop-file-validate data/com.example.Notebook.desktop
luma lint
```
