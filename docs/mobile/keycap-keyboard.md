# Shared Keycap keyboard

This is a shared Stevia patch series for keyboardless phones, tablets and touch
laptops. It does not change Titan hardware-keyboard policy. The source audit
preceded implementation: pinned Stevia 0.55.0
`cd8cc1d33a701573af106c53ed4dc493ef68b88c`, existing Luma LatinIME and compact
keyboard patches, then the existing main-branch theme-variant fix. The visual
reference is the fourth phone in design-center's `mockups/keyboards.html`.

## Source audit and resulting behavior

| Area | Existing implementation reused | Keycap change |
| --- | --- | --- |
| Skin | GTK styles, Cairo key rendering, style manager | Shared light/dark palettes and geometry; Figtree, licensed Lucide outlines, 2px cap travel and 60ms release; live system appearance |
| Suggestions | Completion bar and LatinIME decoder | Literal quoted left, actual backend correction bold in center, third alternative; preference and sensitive-field suppression |
| Tools | Existing layout menu, hide action, clipboard and cursor support | Persistent emoji, history, cursor, preferences and hide strip |
| Letters | Language JSON layouts, shift and accent popover | US/GB number hints and expanded punctuation; double-tap caps lock; sentence caps respecting purpose/hints; drag-select alternatives |
| Delete | Character repeat and word deletion | Single tap without duplicate release deletion; faster repeat after 500ms, word repeat after two seconds; backward word selection on left drag |
| Space | Existing long-press cursor mode | Horizontal swipe cursor, optional double-space period; unlabeled unless multiple languages are enabled |
| Preview | Existing key indicator | Cap styling and a live preference |
| Emoji | Existing database, categories, tone chooser, recents | Eight-column vertical grid, 32 recents, name search using the letter deck, remembered skin tone |
| Clipboard | Wayland data-control collector | Ten memory-only clips, 64KiB limit, one-hour expiry, pin/delete UI; cancels and rejects in-flight reads across sensitive-field transitions |
| Cursor tools | Virtual keyboard driver | Direction controls, select all/copy/paste/cut, return to letters |
| Numeric input | Existing purpose-aware keypad | Three-column PIN/number/phone pads, phone-symbol popup, strip Enter |
| Feedback | libfeedback | Haptics preference, optional libcanberra key sound; sound off by default |
| Languages | GNOME input sources and existing layouts | Preserve layout/source management and show language code when needed |
| Sizing | Existing size manager | At least 44px touch rows; portrait 46px caps plus gutters; compact landscape 38px caps inside 47px rows |

The old input-hint enum used consecutive values, although Wayland sends a bit
mask. It now uses the actual wire bits, including hidden and sensitive data.
Sensitive fields do not receive suggestions, clipboard history, automatic case
changes or double-space punctuation. Clipboard history is never written to disk;
pins last for the keyboard process lifetime, and all ten pinned entries prevent
new captures until a slot is freed.

## Explicit remaining work and acceptance limits

| Requirement | Status / reason |
| --- | --- |
| Learned-only suggestion removal | Not implemented: the LatinIME interface exposes neither learned-word provenance nor removal. No pretend removal control is shown. |
| Send/Search/Next/Done Enter actions | Not implemented: the pinned input-method protocol provides purpose/hints, not these semantic actions. URL shows Go; other fields retain Return. An explicit app/compositor contract is required to avoid submitting forms by guesswork. |
| Settings › Keyboard integration | Six switches and Languages are available in the keyboard's preferences popover. The system Settings page still needs these same schema bindings. |
| Exact 10ms haptic pulse | Not verified: libfeedback owns the device event/profile duration. |
| Lock-screen PIN routing | Keyboard supports PIN purpose; the compositor/greeter must advertise it. No lock-screen deployment or real-device acceptance has run. |
| 200ms show animation and action-bar following | The surface duration is set to 200ms. Application action-bar tracking and physical frame pacing still require compositor/device acceptance. |
| Floating emoji shortcut with physical keyboard | Not implemented; requires shell/shortcut integration. Existing hardware suppression remains enabled by default. |
| Exact preview offset and gesture feel | Existing popover positioning is retained; physical-phone validation remains open. |
| Real FP6/Fold/Titan acceptance | Not run. Headless fixtures do not establish haptic timing, touch feel, lock-screen behavior or physical keyboard suppression. |

## Build and validation

`python3 scripts/mobile/prepare-luma-stevia.py SOURCE` checks a pristine pinned
checkout, preflights the ordered patch series in a temporary Git index, applies
it and copies the shared Figtree font/license. Build dependencies add
`libcanberra-gtk3-devel`. Native GTK tests require a display (Xvfb is sufficient)
and a working SVG loader. Figtree assets and their OFL notice live in
`assets/fonts/figtree`; Lucide attribution is included in the patch.

The FP6 builder uses this same preparation path and bundles matching schemas and
font beside the binary. Candidate assembly validates their hashes, installs the
private schema directory and sets `GSETTINGS_SCHEMA_DIR` for the keyboard service.
This prevents a new binary from requesting keys absent from an older system
schema. The builder keeps the existing real LatinIME decoder test.

Observed validation:

- Eleven native Meson suites passed, including new policy/history, GTK key/drag/
  caps/pad/icon/layout checks and emoji recents/search tests. The eight existing
  component suites also passed.
- Native light/dark captures checked at logical widths 360, 390 and 768; keys stay
  inside their allocation and touch rows meet the minimum.
- A headless Phoc/Wayland fixture activated normal, sensitive PIN (hint mask 192)
  and URL fields, captured the actual keyboard/pad, and accepted synthetic input
  in normal and hidden fields. It uses forced visibility, so it does not qualify
  hardware suppression. Known fixture warnings concern absent session/logind
  services and headless frame timing.
- The full ordered patch series replayed from the exact clean upstream commit;
  shell syntax and Python compilation checks passed.

The ARM bundle build and real decoder suite have not run in the x86 fixture.
No general OS release, phone installation or physical acceptance is claimed.
