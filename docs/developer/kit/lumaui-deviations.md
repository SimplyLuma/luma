# LumaUI conformance deviations

This documents the existing exceptions in
[`accepted-deviations.json`](../../../tools/lumaui-conform/accepted-deviations.json).
The registry is authoritative for match conditions and limits. These exceptions
record implementation constraints and pending decisions; they do not authorize
new differences or establish that pending features have shipped.

When changing an exception, update both the registry and this document. Keep
matching narrow, retain explicit bounds where present, and remove a temporary
exception when its underlying limitation is resolved.

## no-backdrop-blur

GTK has no backdrop-filter; the kit paints a denser token surface, so the sampled surface colour differs a little.

## named-hues

No runtime oklch(): avatars and the hero wash use the five category hues, not the person's exact hue.

## hero-field-30

In edit mode the name field stays 30/700 (hero role) instead of v70's 26 px input.

## no-corner-shape

GTK has no corner-shape: round; plain radii stand in.

## pending-block

Nick hasn't decided whether Block ships; the more menu leaves it out until he does.

## pending-video-calling

There is no Luma calling service yet, so the Video tile stays disabled where v70 shows it enabled.

## pending-block-offset

With Block left out, the rows below it in the more menu sit one row (36 px) higher.

## whole-pixel-lines-cards

GTK allocates whole pixels; v70's 1.45 line boxes and rows are fractional (49.525, 16.675), so rows drift by up to half a pixel each. The tokens round so a card's or list's first and last rows stay close; what is left is at most 3 px.

## whole-pixel-lines-sidebar

GTK allocates whole pixels; v70's 1.45 line boxes and rows are fractional (49.525, 16.675), so rows drift by up to half a pixel each. The tokens round so a card's or list's first and last rows stay close; what is left is at most 3 px.

## lcd-system-mono

v70's LCD asks for var(--mono, ui-monospace); outside Terminal --mono is unset (the LCD, hex colour codes), so Chromium draws the Mac's ui-monospace. Luma's system monospace is IBM Plex Mono (v70 line 745, TY1 mono), which the kit uses.

## system-mono-face

v70 asks for ui-monospace, the Mac's system monospace; Chromium resolves it to a platform face. Luma's system monospace is IBM Plex Mono (v70 line 745, TY1 mono), which the kit's mono role draws. Only that pair is accepted: a mono spec face against IBM Plex Mono.

## system-mono-metrics

The same texts as system-mono-face: IBM Plex Mono's advance differs from the face Chromium draws for ui-monospace, so a mono value's width, and what follows it on its line, differ by that much. Settings' values only.

## glyph-fallback-family

A character Figtree lacks (U+23CE ⏎) falls back on both sides; the spec reports the CSS family, GTK the face that drew it.

## nb-hyphen-fallback

Figtree has no non-breaking hyphen (U+2011, v70's "Wi‑Fi"); Pango draws it from the next face in the stack and Chromium from its own fallback, whose advances differ by a few px. The rest of the string is Figtree on both sides.

## pending-identity-menu

Pending Nick: v70's identity is plain header content (no role) in Contacts; the kit's is the app menu's button.

## identity-name-advance

Pango lays Figtree 600 about 5 px narrower than Chromium; the chevron after the name moves with it.

## frost-backdrop

The Frost frame's blur is the compositor's; a headless capture has neither blur nor wallpaper behind it.

## whole-pixel-lines-text-buttons

GTK gives each text button a whole-pixel width (its label's ceil); a row of them (a mode switch) and the bar holding only it grow by up to 1 px a button.

## pending-reading-serif

v70 names Newsreader/Literata for cover art and reading surfaces; neither font is available yet (Nick's decision pending), so both sides fall back to a system serif.

## frost-backdrop-ink

The Frost frame's ink is translucent (v70 rgba(242,243,246,.84)); it composites over the blurred wallpaper in v70 and over nothing in a headless capture.

## frost-backdrop-ink-title

The Frost frame's ink is translucent (v70 rgba(242,243,246,.84)); it composites over the blurred wallpaper in v70 and over nothing in a headless capture.

## calc-linux-shortcut-spec

The v70 macOS glyphs are represented by working Ctrl+1 and Ctrl+2 accelerators with visible Linux Ctrl keycaps.

## calc-linux-shortcut-gtk

The visible Ctrl keycaps describe the actual Linux accelerators for Basic and Scientific.

## calc-linux-shortcut-digit

The 1 and 2 keycaps complete the visible Ctrl+1 and Ctrl+2 Linux shortcuts on their respective menu rows.

## hinted-figtree-advance

The device hints Figtree (Nick's setting, the real one): GTK rounds each glyph's advance to whole pixels where Chromium keeps fractions, so a run comes out up to about 3% wider. Text-run width only, Figtree runs only; never boxes, positions of other parts, size or weight.

## hinted-figtree-anchor

The same hinted advances: an end-aligned Figtree run keeps its right edge, so its start moves by exactly its own width difference (up to 3%) and nowhere else. Only that shift of that text; never other parts.

## pending-nb-hyphen-line

PENDING google-figtree-fonts 1.luma.4 (U+2010/U+2011 mapped to Figtree's hyphen). Until it ships, a line holding U+2011 draws its fallback face's taller line box, 1 px, and what follows on Settings' Wi‑Fi pages sits up to 2.5 px lower. Remove when the font is on the ThinkPad.

## placeholder-ink-dark

v70 leaves some inputs on Chromium's placeholder grey #757575, 3.8:1 on the dark well; the coordinator ruled an AA dark value, so @luma_placeholder_ink is #85878b in dark. Only that pair.

