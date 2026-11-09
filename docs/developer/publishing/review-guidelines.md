---
title: Depot Review Guidelines
summary: The numbered rules every app in Depot follows, which tier each rule applies to, how review works, what happens when a rule is broken, and how to appeal.
order: 6
section: Publishing
---

# Depot Review Guidelines

People install apps from Depot because they trust that Luma looked. These
guidelines say exactly what Luma looks for. You accept them when you join the
developer program.

> **Status.** Today these guidelines apply to Luma’s own apps. They apply to
> every developer once the [developer program](developer-program.md) opens,
> and may still be refined before then. Changes are listed in the
> [changelog](../CHANGELOG.md).

Each rule has a number so reviewers can cite it, and a scope:

| Scope | Applies to |
| --- | --- |
| **All** | Every listing, including Listed apps Luma does not host |
| **V** | Verified and Luma tier apps |
| **L** | Luma tier apps only |

Listed apps install from their publisher and are not reviewed for quality, but
Luma removes any listing that breaks an **All** rule.

---

## How review works

- **Automated checks** run on every release on the Luma remote: the build,
  metainfo and desktop entry validation, `flatpak-builder-lint`, and permission
  computation. See
  [the checks every release runs](submitting-a-release.md#the-checks-every-release-runs).
- **People review** a Verified app’s first release and any release whose
  permissions grow, and every Luma tier release.
- Reviewers test the release you submitted, on Luma. If your app needs a test
  account or instructions to review, they will ask for them.
- A decision comes with the numbers of the rules involved and what to change.
- Luma may re-review a published app at any time, for example after reports
  from people.

---

## 1. Safety

**1.1 (All) No malware.** No code that harms people, their devices or their
data: viruses, trojans, ransomware, spyware, credential theft, cryptocurrency
mining, botnets, or code that downloads and runs any of these.

**1.2 (All) No hidden functionality.** An app does what its listing says. No
undisclosed features, dormant code that changes behavior after review, or
remote switches that enable behavior reviewers could not see.

**1.3 (V) No self-updating code.** Updates come through Depot. An app must not
download and run executable code, replace its own files, or install other
software outside Flatpak. Plugins and extensions a person chooses to install are
allowed when they run inside the app’s sandbox, and scripting languages that run
code the person writes are allowed.

**1.4 (V) Stay inside the sandbox.** No escaping the Flatpak sandbox, including
through `org.freedesktop.Flatpak` (`sandbox.escape`), unless the app’s core
purpose requires it (an IDE or a terminal, for example), the listing says so,
and review accepts the justification.

**1.5 (V) Least privilege.** Request only the permissions a feature needs, and
prefer portals. Every `high` permission needs a written justification that
review accepts. See [permissions and privacy](../permissions-and-privacy.md).

**1.6 (All) No dangerous instructions or functionality** that facilitates
serious physical harm, such as weapon manufacture, or that bypasses safety
systems of other devices or vehicles.

**1.7 (V) Security maintenance.** Use TLS for network connections and validate
certificates. Fix a known, exploitable vulnerability in your app or its bundled
dependencies within 30 days of it becoming public, or sooner when Luma asks.
Apps left vulnerable may be hidden from new installs until fixed.

**1.8 (V) No harm to the system.** Do not degrade the system or other apps:
no excessive CPU, memory, disk or battery use in the background, no filling
storage, no crashing the session, no preventing sleep without a visible reason.

## 2. Privacy

**2.1 (V) Disclose data collection.** If your app sends personal data or user
content off the device, including analytics, crash reports and telemetry, your
listing links to a privacy policy that says what is collected, why, who receives
it and how long it is kept, and the app tells people before the first transfer.

**2.2 (V) Consent and control.** Anything not essential to a feature the person
is using is opt in, or can be turned off in the app’s settings.

**2.3 (All) No tracking across apps or websites.** No advertising identifiers,
device fingerprinting, or third-party analytics that follow a person beyond
your app.

**2.4 (V) Truthful sign-in declaration.** Your `sign_in` value (`none`, `luma`,
`third-party`, `required-third-party`) matches the app. An app that asks for an
account anywhere is not `none`.

**2.5 (V) Do not require unnecessary accounts.** Features that can work without
an account must work without one. Required sign-in must be declared as
`required-third-party` or `luma`.

**2.6 (V) Protect secrets.** Store credentials with libsecret. Never log them,
include them in semantic objects or Live Extensions, or send them anywhere but
their service.

**2.7 (V) Honest semantics.** If your app publishes to the Semantic Broker,
privacy classes and action risks are truthful, and nothing `secret` is published.

**2.8 (All) Children.** Declare an accurate OARS content rating. Apps aimed at
children must not collect personal data without the consent the law requires
and must not include behavioral advertising.

**2.9 (V) Data removal.** Store data where Luma’s uninstall and reset can find it,
and honor account deletion requests for any account your app creates.

## 3. Honesty

**3.1 (All) Accurate listing.** Name, summary, description, screenshots,
categories and release notes describe the real app. Screenshots show the app
itself running.

**3.2 (All) No impersonation.** Do not present your app as made by, endorsed by
or affiliated with another person, company, project or Luma when it is not. This
includes names, icons, developer names and app IDs.

**3.3 (V) No fake functionality.** No buttons or features that do nothing, fake
system dialogs, fake virus warnings, fake progress, or simulated results
presented as real.

**3.4 (V) No manipulation.** No dark patterns: disguised ads, forced or confusing
subscription flows, pre-checked consent, countdowns that pressure people, or
making it harder to cancel than to sign up.

**3.5 (All) No rating manipulation.** Do not buy, trade, incentivize, or write
your own reviews, or ask people to rate only if they are happy. Asking for a
review in general, at a sensible moment and at most occasionally, is fine.

**3.6 (V) Clear pricing.** If the app charges money or shows ads, the listing
says so. Content that costs money is clearly labeled before purchase.

**3.7 (V) Truthful badges.** Only Luma tier apps may say “Made for Luma”. Do not
use Luma’s name or logo in a way that suggests a relationship you do not have.

## 4. Spam and copycats

**4.1 (All) One app per purpose.** Do not publish several near-identical apps,
reskins or template apps that differ only in content that could be in one app.

**4.2 (All) No copycats.** Do not copy another app’s name, icon, screenshots or
interface closely enough to confuse people, or republish someone else’s app
without permission and meaningful changes the license allows.

**4.3 (V) No keyword stuffing.** Names and summaries describe the app, without
lists of keywords, other apps’ names or pricing claims.

**4.4 (V) Real updates.** Do not submit releases with no changes to stay near the
top of lists.

## 5. Minimum functionality

**5.1 (V) It works.** The app launches, does what its listing says, and does not
crash in normal use. Reviewers must be able to complete its main tasks.

**5.2 (V) Complete, not a placeholder.** No demos presented as the full app,
“coming soon” shells, or apps whose main feature is unfinished. Mark early apps
honestly as such in the description.

**5.3 (V) More than a website.** An app that only shows a website must add real
value: offline use, native integration such as notifications through portals and
files, or a native interface. A plain web page in a window is better as a
bookmark.

**5.4 (V) Graceful failure.** Without a network, with a denied portal request, or
when a service is down, the app explains what happened and does not hang or
crash.

**5.5 (V) Declared architectures work.** The app works on every architecture the
release is built for.

**5.6 (V) Runs outside Luma.** A Depot app installed from the Luma remote on
another distribution works, without requiring Luma-only services such as the
Semantic Broker or Live Extensions.

## 6. Content

**6.1 (All) Legal content only.** Nothing illegal in the places the app is
offered, including sexual content involving minors, which is reported to the
authorities.

**6.2 (All) No hate or harassment.** No content that promotes violence against,
or dehumanizes, people based on protected characteristics.

**6.3 (V) Rated content.** Sexual content, violence, gambling, drugs and
similar themes are allowed only where legal and accurately declared in the OARS
rating. Real-money gambling apps need the licenses their markets require.

**6.4 (V) User-generated content.** Apps that show content from other people
provide a way to report and block, and act on reports.

## 7. Intellectual property and licensing

**7.1 (All) Rights to what you ship.** You have the right to distribute every
part of your app: code, fonts, icons, images, sounds and data.

**7.2 (V) License compliance.** `<project_license>` is accurate. When you ship
software under a license that requires it (the GPL, for example), you meet its
obligations, including source availability and notices.

**7.3 (All) Trademarks.** Do not use other people’s trademarks in your name, icon
or listing in a way that suggests endorsement or causes confusion.

**7.4 (V) No piracy tools.** No apps whose main purpose is circumventing copy
protection or distributing content without permission.

## 8. Listing and metadata

**8.1 (V) Valid metainfo** that passes `appstreamcli validate`, with every item in
[required for submission](metainfo-and-screenshots.md#required-for-submission).

**8.2 (V) Namespace ownership.** The app ID is in a namespace you have proven. See
[app IDs and namespaces](app-ids-and-namespaces.md).

**8.3 (V) Support contact.** A working homepage or support link, and a response
to security reports.

**8.4 (V) Release notes** describe what changed and mention new permissions.

## 9. Technical requirements

**9.1 (V) Flatpak on the Luma runtime,** `org.projectluma.Platform`, built from
pinned sources (source mode) or a signed bundle (bundle mode, proprietary apps
only).

**9.2 (V) Standard integration.** A valid desktop entry and icon named with the
app ID, and `Gtk.Application` or `Adw.Application` created with the same ID.

**9.3 (V) Portals first.** Use portals where one exists for the need. See the
[portal table](../permissions-and-privacy.md#portals-first).

**9.4 (V) Background work only through the background portal,** with a visible
reason.

**9.5 (V) Notifications only for things people asked to know,** sent through the
notification portal, and never for advertising.

## 10. Accessibility

**10.1 (V) Keyboard.** The main tasks can be completed with a keyboard alone.

**10.2 (V) Names.** Interactive controls, including icon-only buttons, have
accessible names.

**10.3 (V) Readable.** Text meets 4.5:1 contrast (3:1 for large text) in the
appearances the app supports, and the app does not break with large text.

**10.4 (L) Full accessibility.** Every item in the
[accessibility requirements](../design/accessibility.md#requirements), tested
with the [test plan](../design/accessibility.md#test-plan): screen reader, high
contrast, reduced motion, right-to-left and touch.

## 11. Luma tier: design and quality

Luma tier apps look and behave like part of the system. In addition to every
rule above:

**11.1 (L) Built on LumaUI.** Windows use `AppWindow` or
`LumaApplicationWindow`; islands, sidebars, toolbars, empty states, dialogs and
menus use LumaUI components. No local imitation of a LumaUI component.

**11.2 (L) Tokens only.** No literal colors in app stylesheets, and no rules for
LumaUI’s frame, title row, identity, window controls, body or islands. See
[windows and islands](../kit/windows-and-islands.md#your-stylesheet).

**11.3 (L) One adaptive app.** One source and one binary work at 360, 500 and
1024 logical pixels and the widest designed width, in windowed and
`fullscreen-mobile` presentations, with pointer, touch and keyboard. No function
is lost at any width. See [adaptive layouts](../kit/adaptive-layouts.md).

**11.4 (L) Contract passes.** `luma lint` passes with schema 0.2, and
`luma release-check` passes with evidence from real test runs on `x86_64` and
`aarch64`, desktop and mobile.

**11.5 (L) Commands.** Every meaningful operation has one stable action ID shared
by its controls, menus and shortcuts; every surface with a context menu supports
right-click, long press and the Menu key.

**11.6 (L) Design guidelines.** Typography, spacing, icons, motion and writing
follow the [design guidelines](../design/principles.md), in light, dark and high
contrast.

**11.7 (L) Reduced motion** is honored by every custom animation.

**11.8 (L) Localizable.** All interface strings are translatable, layouts survive
text 40 percent longer than English, and the app mirrors correctly in
right-to-left languages.

**11.9 (L) Quiet at rest.** No resident helper process, polling loop or network
activity while the app is idle, unless a declared background feature needs it.

**11.10 (L) Lifecycle.** Keep-data and remove-app-data uninstall choices and reset
work as declared in `luma-app.toml`, and user documents are never removed.

**11.11 (L) Source.** Built by Luma from a source commit (source mode), never
from a bundle.

---

## Outcomes

| Outcome | When | Effect |
| --- | --- | --- |
| **Changes requested** | A fixable problem | The release is not published until you resubmit |
| **Rejected** | A serious or repeated problem | The release is not published |
| **Hidden** | A published app breaks a rule | Hidden from new installs while you fix it |
| **Withdrawn** | A serious violation, or an app left broken | Removed from Depot; installs keep working but get no updates |
| **Developer suspended** | Repeated or serious violations | All your apps hidden from new installs; no new releases |
| **Developer revoked** | Malware, fraud, impersonation or continued violations after suspension | Listings withdrawn; marked “no longer maintained” |

Rules 1.1, 1.2, 3.2 and 6.1 can lead directly to revocation.

## Appeals

- For a release decision, reply in the developer portal with the rule number
  and why you believe it does not apply. A different reviewer looks at a
  disputed rejection.
- A revoked developer may appeal once through the developer portal.

## Changes to these guidelines

Luma publishes changes to these guidelines in the
[documentation changelog](../CHANGELOG.md) before enforcing new rules, except
where an urgent safety issue requires immediate action.
