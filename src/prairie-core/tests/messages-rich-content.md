# Messages content preview and native MMS gate

Status: **preview with remaining defects; not accepted, committed, packaged, or released**.
The requested exact screenshot match has not been achieved. This note describes
an expanded Messages-only source change; it does not certify carrier operation.

The source is on `work/messages-visual-content`, based on
`work/context-menu-on-one-kit` at `7ed86ee3`. The original content-only preview
is preserved separately from the expanded transport implementation in
`build/visual/messages-content/visual-preview/`. The coordinator owns package
pins, integration, image composition, and emulator installation.

## Implemented ownership

Messages widgets and scoped content CSS now use the target's compact search,
conversation rows, avatar dimensions, header controls, message widths, attachment
card, quoted reply, reaction badge, and composer. Existing toolkit islands,
frame, menu styling, and shared tokens are unchanged. New colors reference only
existing `@luma_…` tokens. RTL content margins mirror correctly. Pointer controls
use the target's dimensions; touch controls retain 44px hit areas, including
draft attachment removal and reply cancellation.

The same app/store serves desktop and handheld. Additive SQLite tables preserve
private attachment copies, quoted-reply snapshots, draft replies, reactions,
wire-text fallbacks, and deleted transport identities. Attachment-only drafts
are discoverable after restart. Copies are bounded to 25 MiB, regular files only,
private, flushed before publication, and collected only after their final
reference is removed. Received PDU slices must be inside the native MMS store
and within checked bounds. PNG previews validate the mandatory IHDR header and
64-million-pixel limit before using GTK's existing decoder. Other image previews
require the installed Pixbuf probe. No decoder or sandbox is replaced.

The app uses the documented `org.ofono.mms` session-bus API without activating a
daemon, changing an APN, or taking modem ownership. SendMessage acceptance is
queued, not sent; daemon status advances delivery. Discovery buffers intervening
signals so a stale history snapshot cannot overwrite newer status. Received
one-to-one MMS is imported, and local deletion tombstones prevent reimport.
The API references are the upstream [service](https://gitlab.com/kop316/mmsd/-/blob/master/doc/service-api.txt),
[message](https://gitlab.com/kop316/mmsd/-/blob/master/doc/message-api.txt), and
[manager](https://gitlab.com/kop316/mmsd/-/blob/master/doc/manager-api.txt) contracts.
No upstream implementation was copied or vendored.

SMS/MMS quotes use an explicit text representation on the wire. Heart reactions
require review of the actual SMS text before sending; they are not represented
as RCS. Presence accepts only an expiring provider event and is not persisted;
there is currently no authenticated provider wired to it. Neither SMS nor MMS
invents online presence.

## Observed checks

- 33 focused backend/content/MMS unit tests pass.
- Four runtime tests invoke the real UI transport handlers: durable received
  parts, duplicate/deletion handling, group rejection, partial-import cleanup,
  exact quoted caption, and queued-to-sent delivery.
- A private D-Bus service test validates actual GI marshalling for SendMessage,
  discovery ordering, and the status signal race. No test contacts a modem.
- Native-core independence smoke passes; the new MMS client uses native D-Bus.
- Installed baseline package smoke previously passed with edited source on
  PYTHONPATH. This is not a freshly built/installed package acceptance result.
- Composited X11 capture uses installed patched GTK .13, libadwaita .34 and
  Figtree, plus unmodified integration AppKit source. The installed platform
  package is older than that source, so packaged runtime acceptance remains open.
- Actual compositor owner `0x200001`, real root pixmap, and actual `csd` without
  `solid-csd` were verified. All owned display102 helpers were cleaned with a
  subreaper. Phone/Menu displays and shared scripts were untouched.
- Rendered 360px inbox/conversation/error/long text, 500px, 1024px, 1400px,
  860px empty/light/RTL/large-text states. Narrow layouts remain within the
  window and the composer is usable. Large-text settings enlarge the split but
  do not yet enlarge the explicit pixel-sized content fonts: that gate is open.
- The baseline convergence script had a missing operand at line92, causing the
  following GTK4 check to be swallowed despite exit0. That run is INVALID.
  `build/visual/messages-content/convergence-prerequisite.patch` contains the
  isolated one-line repair. Running the repaired copy against the unchanged
  source manifests passes all intended checks plus35 convergence tests.
  The repository's shared smoke script was not edited by this app change.
- Desktop and mobile still reference the shared `prairie-core-apps` package;
  baseline desktop input/package pins agree at .59. No new package was built.

## Visual comparison

Evidence lives in `build/visual/messages-content/`: target-preview.png,
composited-after.png, compositor/evidence/root.png, latest-diff.png, region
4x crops, and latest-measurements.json. Coordinates below are relative to the
860x580 target window (the supplied target is cropped at22,15).

| Difference from supplied current state | Latest observed result |
| --- | --- |
| Sidebar too wide | 218px island and9px gap; target geometry matched |
| Search oversized and inset too far | 172x28 at15,48; target geometry matched |
| Conversation list absent | Real store-backed fixture rows render at46px with1px gaps; row/font raster alignment still differs about1px |
| Avatars oversized | 32px,9px identity gap, single initial matched |
| Avatar palette | Three fixture identities still map to different default tones than the target; unresolved |
| Header too tall / crowded identity | 40px header,32px avatar,9px gap,13/11px type,28px pointer controls; geometry matched, glyph raster differs |
| Header action circles | Removed within app content; target outline actions render; video remains truthfully unavailable |
| Empty history / bottom alignment | Top-aligned store-backed fixture history; first incoming at248,137 matched |
| Incoming bubble | 269x36; target padding/corners matched; target fill RGB50,54,60 versus native token composite50,53,59 |
| Outgoing bubble | 429x57 at399,179, matching wrap; target ink229,232,234 versus installed token228,232,234 |
| Third bubble | At248,256; width364 versus target approximately365; font shaping difference remains |
| Day marker | Compact pill; approximately1px horizontal alignment and font-weight/raster difference remains |
| Reaction | Real stored badge at781,228,37x22; overlap matched, icon raster not identical |
| Image attachment | 290x187 at538,298 with280x150 preview; matched geometry,77.48% identical pixels, mean channel error0.21/255 in the card region |
| Quoted reply | Real quote begins at248,491; clipping by composer matched; font/inner outline differ |
| Composer | 36px pill at278,527 and28px send at815,531; target geometry matched |
| Disabled send appearance | Target bright appearance restored with app-scoped filter override; one-channel ink token difference remains |
| Surface colors | Content RGB42,46,52 exact; fill/selection and some avatar blends differ1–2 channels |
| Text sizes/weights | Source font roles matched; native Pango/FreeType glyph coverage differs from target; not pixel-identical |
| Scrollbar | Native scrollbar remains thinner/positioned differently than target; unresolved toolkit/content boundary |
| Target data | Explicit synthetic fixture supplies messages, image, quote, reaction and presence; target Nora blank preview differs from valid SMS fixture data |
| Frame/icon/wallpaper | Toolkit/shell-owned differences remain; no app compensation applied |

The picture fixture is a crop from the supplied target, used only to compare
media layout; it is not a shipped asset or evidence of receiving the original
file. Synthetic conversations and the presence event exist only in the isolated
render harness. Production Messages does not inject them.

## Remaining release gates and rollback

Exact visual parity remains open; no near-black diff or user acceptance is
claimed. Carrier mmsd/service availability, provisioning, real send/receive,
reconnect, background MMS import while the app is closed, group conversation
model, authenticated RCS/presence/received reaction integration, video calling,
full keyboard/accessibility/high-contrast/large-text validation, independent
review, package build/runtime, clean image composition, upgrade/rollback, and
physical-device acceptance remain open. The existing RCS feasibility record
also describes carrier onboarding limits; no Android fallback was added.

Rollback removes this app branch's code/CSS while preserving the SQLite database
and attachment directory. Older app versions can still read original SMS
columns; they cannot display new rich records. Do not drop new tables or delete
attachment files to downgrade. Preserve a backup before package/device testing.
Source-level tests do not establish live carrier, package, image or
physical-device acceptance; record the applicable runtime results separately.
