# Viewer LumaUI port

The approved surface is simulator v71 Viewer. Every scenario state now has
explicit desktop and phone-shell reference routes in both appearances; the
port is still incomplete and screenshot parity is not yet established.
`python3 -m luma_viewer.conform_assertions <run folders>` checks the captured
interaction outcomes in addition to the unchanged conform measurement gate.
No phone states substitute for omitted desktop interactions.

| Feature | Composition and behavior | Scenario states |
| --- | --- | --- |
| File history/search/open | NavigationSidebar, SidebarRow, RowLead, SidebarFoot, SidebarToggle; real local history and file portal; plain Up/Down files, Left/Right in View, including keypad arrows; editable fields, sliders and selected annotations keep their own keys | view, sidebar, receipt, pdf; native keyboard checks390/1180 |
| Document modes and actions | CornerPill, ModeSwitch, ActionCenter, BarAction; image view/markup/adjust, PDF view/sign | view, markup, adjust, pdf-markup |
| File information | DetailsPane and fact rows; real metadata and OCR availability | information |
| Image viewing | App-owned token canvas, fit/zoom/pan, shared ZoomControl; all four approved standalone image samples | view, frame, terrace, receipt |
| Annotation tools | Shared ToolPalette above the action bar; select, pen, highlight, rectangle, ellipse, arrow, text, numbered steps, blur, signature, irreversible exported redaction; PDF select/pen/highlight/text/redaction/signature | markup, inline-editor, pdf-markup; native Enter/Escape/focus checks |
| Ink and stroke width | Shared palette/style request02; app owns mark color/width, selected-mark recoloring and history | style |
| Save/revert/undo/redo | SplitAction; atomic new copy, Share, Clipboard; native exports preserve originals | save-menu; export/history tests |
| Image adjustments | Shared AdjustmentPanel/ValueSlider; nine adjustments, Auto, rotation, flip, hold-original ends on release/cancel | adjust; actual hold/click ordering and timed-preview takeover390/1180 |
| Crop | Shared overlay request03 still blocks handle/mask/drag interaction; app owns centered90% aspect reset, Escape/cancel/commit and source coordinates | crop; processing/aspect tests and actual native callbacks390/1180 |
| Live Text | Local OCR, native selectable spans, detection and shared action menus/drawers | text, text-tel, text-addr, text-date, text-money |
| PDF reading and navigation | Poppler pixels, native text selection, token paper thumbnail buttons and scrolled pages | pdf, pdf-page-2, pdf-page-3; realized thumbnail pixels dark/light/HC |
| PDF form fields | Native editable fields; read-modify-write of edited field IDs into a new copy | pdf-filled; export tests |
| Open in | Shared OpenInMenu, installed GIO applications; edited content requires saving a copy | open-in |
| Share | Shared ShareSheet; real targets, fixture-only eight approved people and bounded thumbnail | share |
| Compare | App-owned image composition and draggable Lucide handle; shared ModeSwitch/actions, Escape exits; decode completion belongs to its session | compare, compare-side, compare-diff; actual native Escape/Swap/Done390/1180 and stale callback tests |

Shared parts with pending requests must land on foundation; the app never
restyles them or adds accepted deviations. Measurement inventory remains
incomplete until the gate has no unaccepted missing elements.

## Data safety

Fixture mode is in memory and never writes real files, launches applications,
prints, creates contacts/events or copies private data. Real document edits
remain in memory until Save makes an atomic new copy; originals are unchanged.
PDF redaction and blur flatten every exported page so removed text cannot be
recovered from its old text layer. Images export processed pixels and marks.

Existing Viewer history is read from `$XDG_DATA_HOME/luma-viewer/recents.json`
(default `~/.local/share/luma-viewer`). Writes merge changed fields into the
latest file under a `recents.lock`; unknown fields and other records survive.
Before the first edit, `recents.json.before-lumaui` stores its exact old bytes.
The backup is published only after all bytes are written and synced; a backup
failure leaves existing history untouched. An unreadable store is never
overwritten. Clear History explicitly removes
all history and thumbnails; the one-time safety backup remains. No new sync,
migration or cross-app store writes are added. Contacts/Calendar creation and
currency conversion await Nick's approved interfaces/provider; unavailable
real-file actions remain disabled in both desktop menus and phone drawers.

## Preview identity verification

The scratch launcher sets `LUMA_VIEWER_PREVIEW=1`. The application reads that
flag when selecting `APP_ID`, then passes it directly to `Adw.Application`:
`org.projectluma.Viewer.LumaUIPreview`. Without either preview or fixture flags,
the production ID stays `org.projectluma.Viewer`. Before any real-data preview
launch, run `python3 src/luma-viewer/tests/check_preview_identity.py` on the GTK
test host. It uses a fresh private bus, holds the production name, executes the
actual scratch launcher into the actual ViewerApplication constructor and
registration, and requires zero production activations/windows, an unchanged
production owner, and no remaining preview bus owner after exit. It opens no
document or history. The approved isolated host check passed on 26 September:
the actual native preview registration remained local, the production owner
was preserved without activation, and no window or preview owner remained.
The staged package's 50 tests and ten native document startup/close cases
also passed at asserted 390px and 1180px widths against the built platform.
These are host checks; the four full conform PASS reports are still required.
After staging the closed preview, repeat
with `--launcher ~/.local/share/luma-dev/lumaui-viewer/bin/luma-viewer-preview`
to verify that exact installed scratch entry before any real-data launch.

## 1 October v71 follow-up

Shared SidebarToggle provides desktop visibility and a compact drawer below721.
The phone file summary uses FileSummaryRow in ActionCenter.head; recent files
and Information grow from that bar, and editing modes provide Close.
ToolPalette keeps tools separate from Undo/Redo/Save and scrolls at narrow
widths while preserving keyboard focus visibility. The phone-only black
surround reuses ImageViewport; desktop light appearance stays white as v71.
Removed the application rule hiding shared adjustment separators.

Native sidebar/pane checks pass1180/720/402/360; phone head/panels/modes/tool
selection pass360/402/500. Inline entry at390 and1180 still commits on Enter,
cancels on Escape, commits on focus loss and preserves source bytes. The
full visual gate remains required. FileSummaryRow and ToolPalette currently
have Python implementations; their missing C twins are explicit toolkit debt.
