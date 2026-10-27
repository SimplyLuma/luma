# Try Luma icon directions

Three source SVG options in the Prairie icon style. **Option 2, Play, is selected.** Its complete play mark is optically shifted 16px left of its area-centered
position, with vertical alignment preserved. The selected source is also installed as `luma_tester/io.luma.Tester.svg`.

1. **Luma Dot** — the orange Luma mark on a charcoal Prairie tile.
2. **Play** — a raised white play symbol on an orange tile.
3. **Portal** — the orange Luma mark inside a raised window on an indigo tile.

They share Prairie's 1024px master, 860px rounded plate, 202px corners, 1.12 dock
transform, upper-left lighting and soft object shadows. Transparency surrounds
the tile; the artwork contains no baked outer desktop shadow.

Export a selected SVG with `scripts/tester/export-icons.py --source ICON.svg
--output ARTIFACT_DIRECTORY`. Exports include:

- macOS: ICNS and the complete 16,32,128,256,512pt iconset at 1x/2x, up to 1024px.
  Native Mac builds use Apple's `iconutil` and declare the icon in Info.plist.
- Windows: 32-bit RGBA ICO with 16,20,24,32,40,48,64,96,128 and 256px frames.
  The Windows packager uses MinGW-w64 gcc/windres to embed the ICO and product
  version metadata in its own launcher. A copied launcher is not sufficient.
- Linux: scalable SVG and PNGs in hicolor size directories, with the same stable
  icon/desktop ID `io.luma.Tester` so existing dock pins continue to resolve.

Build dependencies: Pillow plus rsvg-convert or PyGObject/librsvg. Windows also
requires MinGW-w64 gcc/windres (`--cc`/`--windres` can supply explicit paths).
Generated previews, native exports and packages belong in build artifacts.

References: [Apple app icons](https://developer.apple.com/design/human-interface-guidelines/app-icons),
[Windows icon construction](https://learn.microsoft.com/en-us/windows/apps/design/style/iconography/app-icon-construction),
[freedesktop icon theme specification](https://specifications.freedesktop.org/icon-theme/).
