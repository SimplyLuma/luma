# GNOME Calculator downstream patch

Project Luma carries six isolated patches against Fedora 44's exact
`gnome-calculator-50.0-1.fc44` source package. Its SHA-256 is
`7c241ad809598b857f28d20e681f2d57886ade032dc62477867c3380f961c827`.

`0001-luma-calculator-aesthetic-v1.patch` establishes the shared Prairie visual
language. `0002-prairie-basic-product.patch` deliberately narrows the product to
a normal desktop calculator while preserving GNOME Calculator's mature parser,
arithmetic engine, keyboard editing, clipboard actions, application ID,
schemas, packaging, license, metadata, and upstream attribution.
`0003-prairie-calculator-polish.patch` gives the keypad surface the same shared
neutral material as Prairie sidebars, uses the global neutral hover/active
tokens, applies Figtree explicitly to the readout and keys, and removes the
source-view line padding that could clip or vertically jump long readouts.
`0004-prairie-readout-presentation.patch` separates the visible answer label
from the transparent editing surface, keeps the answer vertically centered,
removes the redundant secondary status line, and disables native horizontal
scrollbar chrome. The underlying equation buffer, key controller, accessible
status announcements, programmatic scrolling, and width-aware type scaling
remain intact.
`0005-prairie-readout-input-boundary.patch` removes the nonvisual editor from
GTK's rendered widget tree, transfers focus and standard Basic-mode keyboard
entry to the Calculator display component, and retains the native equation
buffer as the single parser/editing model. This prevents stale clipped glyphs
from entering the compositor while keeping button input, keyboard input,
clipboard actions, undo/redo, and accessibility behavior source-owned.
`0006-luma-appkit-convergence.patch` moves Calculator onto the shared AppKit
identity trigger, 42px title row, semantic material tokens, and single inset
body island. Its selectors remain explicitly Calculator-scoped, so unrelated
GTK and libadwaita applications retain their upstream presentation.

- a compact identity title bar with no redundant application toolbar;
- a non-caret readout card that still accepts keyboard input through the
  underlying equation buffer;
- one equal 8-pixel keypad rhythm and a standard four-column basic layout;
- semantic number, function, operator, and result button treatments; and
- right-aligned, width-aware display typography.

Advanced, Financial, Programming, conversion, history, and mode-selection UI
are intentionally absent from Prairie Calculator. Their upstream source files
remain buildable for now, but the product surface neither instantiates nor
exposes them. This is a reviewed product decision, not a CSS suppression.

Calculator consumes the global light palette from Luma's downstream
libadwaita package. It does not install a user stylesheet, theme override,
extension, startup hook, or injected CSS provider. The four key families are
selected through native widget classes. Initialization and sign-change behavior
are source-owned actions; there is no injected provider or startup mutation.
The identity trigger and window geometry consume the same shared libadwaita
classes and semantic tokens as Luma-native applications; Calculator does not
carry a parallel theme or application-local window-control implementation.

GNOME Calculator remains GPL-3.0-or-later and retains its upstream name,
identity, metadata, links, and attribution.
