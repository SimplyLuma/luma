# Changelog

## 2026-10-02 — shared v71 package completeness and approved mail artwork

- Install the shared v71 window and its optional test mailbox module through
  normal Meson/RPM ownership. An installed package previously failed when
  importing the application because the required window module was absent.
- Ship the approved v71 mail SVG as the single icon source; generate every
  normal hicolor raster size from it during the package build.
- Replace obsolete monolithic UI checks with native shared-window resize,
  action, search, avatar and resident-task checks. Real XTest typing still
  verifies that E/Delete never archive or trash from the actual multiline
  quick-reply field, and still act from the retained thread's mailbox row.
- Check imports from the actual installed package outside the source tree,
  preventing source-path tests from hiding omitted runtime modules. Mail,
  authentication and sending behavior are unchanged.
- Preserve mailbox and conversation ownership when crossing phone/desktop
  widths; release the toolkit overlay's main child with its native API.
- Declare the native navigation breakpoint so a desktop split cannot prevent
  resizing back to the supported phone width while retaining the open thread.
- Keep the focused native search field across mailbox filtering; restore the
  normal phone action row only when search closes.

## 2026-09-17 — 0.2.14 provider-first account setup

- Typing "e" in the inline reply no longer archives the conversation. One-key
  shortcuts (E, Delete) only act when focus is outside a text field.
- Replaced the dense legacy server form with a Luma AppKit provider chooser,
  familiar provider identity rows and a focused per-provider setup surface.
- Added presets and provider-specific app-password guidance for iCloud, Yahoo,
  Fastmail, Zoho and AOL, while retaining a complete Other IMAP fallback.
- Added browser-owned Microsoft 365 / Outlook authorization-code sign-in with
  PKCE, loopback return, delegated IMAP/SMTP scopes and Secret Service token
  persistence; Charlie never asks for or stores a Microsoft account password.
  The window and background mail agent share the same refresh/XOAUTH2 path.
- Kept raw hosts, ports, usernames and STARTTLS settings behind an explicit
  disclosure for known providers, while preserving custom settings when an
  existing account is edited.
- Verified the real provider picker at its default size on the ThinkPad and
  removed a redundant footer that competed with the final provider row.
- Kept asynchronously loaded photo avatars centered at the same canonical
  32px or compact 24px allocation as their AppKit initials placeholders;
  photos no longer stretch to the surrounding row height.
- Reconciled Charlie's complete native type hierarchy with the installed
  AppKit, Notes, Contacts and Messages contracts: Figtree remains owned by the
  application root, navigation and primary row labels stay at 13px, supporting
  row text and timestamps use 11px, and native conversation copy returns to
  the shared 13px reading size. Account setup now uses AppKit's display-title
  semantic instead of carrying a nearly matching private heading size.

## 2026-09-17 — 0.2.13 stale authentication alert cleanup

- Withdraw the account's deterministic sign-in notification after a real IMAP
  login succeeds, including alerts left behind by an older agent process.
- Keep the warning visible during reload until the replacement worker has
  actually authenticated, rather than clearing it optimistically.

## 2026-09-17 — 0.2.12 resilient Gmail and real avatars

- Made the rendered email surface open Charlie's complete formatted-message
  window while leaving “Click to expand” as a separate inline-only action.
- Suppressed external link activation in the bounded preview so a single click
  cannot both open the full reader and launch another application; links remain
  active in the complete reader.
- Force-refresh a still-dated Google access token once when Gmail explicitly
  rejects it, and treat temporary IMAP login refusals as retryable server errors
  instead of permanently stopping the account with a false sign-in warning.
- Restart and clear a stale background-agent authentication latch after a
  successful foreground sync.
- Added real cached photos throughout thread rows, conversation identity,
  message metadata, account navigation and account management: authenticated
  Google profile photos for the person's own accounts, hashed Libravatar
  lookups for people, domain-only brand resolution, and AppKit initials as the
  immediate fallback.

## 2026-09-16 — 0.2.11 complete inline expansion

- Expanded designed HTML to its full measured height so the conversation owns
  the only scrollbar and “Click to expand” reveals the complete message.
- Measure the complete rendered document through WebKit's native snapshot API,
  keeping sender JavaScript disabled instead of relying on a blocked DOM query.
- Keep the WebView behind a non-scrolling viewport so collapsing reliably
  returns to 340 px even after the full document has been measured.
- Replaced the oversized footer containing a second button with one compact,
  44 px full-width “Show less” control.

## 2026-09-16 — 0.2.10 formatted-mail controls

- Replaced the ThinkPad's 0.2.6 Charlie overlay with this qualified RPM in the
  existing staged deployment, without rebooting, and launched the exact
  extracted 0.2.10 payload for immediate testing.
- Lengthened the collapsed HTML overlay and faded it into Luma's dark menu
  shadow token so “Click to expand” remains legible over light, dark or busy
  email artwork.
- Replaced the floating text-style “Show less” control with a full-width,
  60 px footer surface, including a separator and comfortable vertical padding.
- Gave transparent HTML messages a light system canvas and text baseline, while
  leaving explicit authored backgrounds free to override it; dark app chrome
  can no longer turn common `#333`/`#666` email text into dark-on-dark content.

## 2026-09-15 — 0.2.9 in-place formatted mail

- Centered the formatted-message expansion affordance as text only and faded
  the document itself into its content surface so the label remains legible.
- Expanded the existing conversation bubble to its measured document height,
  capped at 900 px with internal scrolling for exceptionally long newsletters;
  the action no longer opens a separate formatted-message window.
- Kept an equally centered, text-only Show less action beneath the expanded
  document so the conversation can return to its 340 px preview.

## 2026-09-15 — 0.2.8 local message times

- Kept canonical message instants stored as timezone-aware UTC while converting
  every thread-list, conversation and full-message timestamp through one local
  presentation boundary.
- Based Today/Yesterday labels on the local calendar date after conversion, so
  late-evening mail no longer crosses the day boundary just because UTC did.
- Added deterministic coverage for the observed 2:52 AM UTC → 9:52 PM Chicago
  conversion and for the calendar-day edge around midnight.

## 2026-09-15 — 0.2.7 background mail agent

- Added `org.projectluma.Charlie --agent`, a windowless agent (ADR-033,
  category mail) that never loads GTK. It keeps one connection per enabled
  account, waits in IMAP IDLE (re-issued every 25 minutes) or polls every ten
  minutes, stores new Inbox mail so the window shows it without a sync, and
  publishes `UnreadCount` and `UnreadByAccount`.
- New unread mail posts one notification per message naming the sender and
  subject (more than three at once are grouped); the lock screen shows only
  "Charlie" / "New mail" unless details are allowed there. Open and Mark Read
  act on the exact message; mail read elsewhere withdraws its notification.
- The first run and a UIDVALIDITY change set a baseline and announce nothing.
  Errors back off from one minute to thirty; a rejected sign-in stops that
  account with one "Sign in again" notification until accounts are reloaded.
  Network return, resume and unlock reconnect at once.
- Follows the published luma-background contract (package release
  1.luma.2): the agent exports `org.projectluma.BackgroundAgent1` with
  `Wake` accepted only from `org.projectluma.Background1` and publishes
  `unread-count` and `unread-by-account` in `Values`. It runs on
  `luma_appkit.background` when installed. Charlie installs its declaration in
  `/usr/share/luma/background/` and ships no systemd unit or D-Bus activation
  file: the service generates the unit, applies the person's decision and
  delivers wakes. An autostart entry (`--agent --autostart`) starts the agent
  only in sessions without the service, where it watches the network, resume
  and unlock itself.
- The window asks luma-background for background activity when it opens with
  an account or after one is added, tells a running agent to reload after
  account changes and to check from Check for Mail, and never starts it
  directly. The unused in-window notification path was removed.

## 2026-09-14 — 0.2.6 search activation

- The Shell starts Charlie over D-Bus to search mail. That start now runs as a
  GApplication service and opens no window; a search at sign-in no longer
  brings up the mail window.
- Exported the search provider during D-Bus registration, before the bus name
  is owned, so the Shell's first query no longer fails with an unknown object.

## 2026-09-14 — 0.2.5 sync-error classification

- Classified credential rejection at the IMAP login boundary instead of
  treating every `imaplib` protocol exception as an authentication failure.
- Kept IMAP aborts, socket loss and timeouts in the connection-error path; an
  interrupted refresh can no longer produce the false account-password alert.
- Added regression coverage for Python's non-obvious `IMAP4.abort` inheritance.
- Made thread-list sender, subject and time emphasis follow unread state; opening
  a conversation now removes both the unread dot and bold typography immediately.

## 2026-09-14 — 0.2.4 resident-close behavior

- Changed the primary window's close request into a real minimize operation so
  Charlie remains resident for mail services and later activation presents the
  same window and in-memory state.
- Preserved the explicit Quit Charlie / Ctrl+Q path for intentional shutdown
  and left auxiliary compose, account and formatted-message windows with normal
  close behavior.
- Added source and mapped-window lifecycle checks covering minimize-on-close,
  same-window reactivation and explicit quit.

## 2026-09-14 — 0.2.3 platform-icon correction

- Baked Luma Icon Canvas's 240 px platform silhouette into Charlie's
  freedesktop icon so hicolor consumers cannot expose the full-bleed source's
  square corners.
- Added a source contract test for the named clip and radius and regenerated
  every raster size from the corrected scalable asset.

## 2026-09-13 — 0.2.2 conversational-content candidate

- Added a conservative MIME presentation classifier so ordinary Gmail,
  Outlook and plain formatted replies use native conversation bubbles while
  genuinely designed layouts retain the bounded WebKit card.
- Removed conventional HTML and plain-text reply history from the visible
  authored message without fuzzy signature deletion.
- Resolved referenced CID images from Charlie's private MIME payload and
  rendered them inline without duplicating them as attachment tiles.
- Fully mirrored sent-message metadata with the time before the right-side
  AppKit avatar, and restored the canonical 5px directional tail corners.
- Layered the qualified RPM into the ThinkPad's staged rpm-ostree deployment,
  booted and launched it from `/usr`, verified both canonical icon hashes, and
  hid the obsolete portable launcher in a recoverable backup without deleting
  its profile.

## 2026-09-13 — 0.2.1 conversation polish candidate

- Matched Luma Messages' left/right transcript language with responsive bounded
  bubbles, header identity, full-span separation and HTML-only dark elevation.
- Added rounded 340 px formatted previews with one document inset, a visible
  lower fade, expand-on-click, remote images and an eight-entry rendered cache.
- Added inline attachment open/save controls and exact-message reply, reply-all
  and forward context actions with RFC reply headers.
- Removed the thread-row unread gutter, corrected navigation-section insets and
  kept account rows on one line with account-scoped unread totals.
- Added Sent special-use discovery/sync, `In-Reply-To` ancestry and an
  account-scoped subject/peer fallback so inbox and sent halves form one thread.
- Qualified canonical `$XDG_DATA_HOME/charlie` persistence on the ThinkPad and
  expanded the local/runtime suite to 43 tests.

## 2026-09-13 — 0.2.0 connected-account candidate

- Added the Accounts manager and a functional Add Account editor with provider
  presets, manual TLS IMAP/SMTP enrollment, edit/remove actions and automatic
  first synchronization.
- Restored Charlie's Google browser authentication with authorization-code
  PKCE, loopback return, encrypted token persistence, refresh and Gmail
  IMAP/SMTP XOAUTH2. Google passwords and tokens never enter SQLite or logs.
- Fixed both Accounts-page add controls aborting on Fedora because GTK 4's
  `PasswordEntry` does not expose the `Entry` placeholder API, and retained
  account windows explicitly for deterministic GTK lifecycle ownership.
- Kept first run empty unless the test-only `CHARLIE_DEMO=1` switch is set.

## 2026-09-13 — 0.1.1 adaptive polish candidate

- Rebuilt the three-pane surface with nested AppKit split views so the 178 px
  mailbox rail and 348 px thread list collapse into native single-pane
  navigation at compact widths.
- Aligned thread content with a permanent unread-state column, corrected the
  active-row jump, and restored AppKit's fixed avatar and account-dot measures.
- Matched Luma toolbar typography, search spacing, transcript rhythm and reader
  identity sizing across wide and compact layouts.
- Added isolated width-qualified geometry and contract coverage for the
  responsive hierarchy.

## 2026-09-13 — 0.1.0 native integration candidate

- Added the GTK 4/libadwaita/Luma AppKit wide, medium and one-pane layouts.
- Added a presentation-independent MIME/IMAP/SMTP/SQLite engine with local FTS.
- Added transactional local read, flag, archive, trash and undo behavior.
- Added Secret Service, locked-down WebKitGTK, attachment-save portal, mailto,
  replaceable notification and SearchProvider2 integration boundaries.
- Added one reverse-DNS identity, approved Luma Mail artwork, AppStream/desktop
  metadata, Meson packaging, RPM packaging and runtime/contract tests.
- Kept compact reader actions available through the AppKit command overflow.
- Kept the Flutter Charlie installation and data completely separate.
