# Luma shell presentation

Native GNOME Shell (St/Clutter) sources for the Luma presentations, copied
into the Shell's JS resource under `ui/lumaPresentation/` by
`patches/gnome-shell/0000-luma-fedora-spec.patch` and registered by the
series. `presentation.js` follows the shell-state service
(`org.project_luma.ShellState1`) and owns the Desktop, Tablet and Mobile
presentations; it never decides policy itself.

## TV posture

`tv.js` is the shell's TV presentation. `presentation.js` activates it when
the snapshot's `posture.effective` is `tv` (`luma-shell-statectl set posture
tv`) and the handheld machinery stays on Desktop underneath. While active it
owns the screen:

- the TV home: the pre-blurred prism backdrop (`tv-backdrop.jpg`, made by
  `scripts/developer/render-tv-backdrop.sh`; never a live blur, so Reduce
  Transparency costs nothing and makes the materials opaque), the shared top
  bar (Home · Apps · Search in a recessed track, weather | time on the right,
  96 in, 56 down, 74 tall), the greeting on Home, the Apps grid and Search;
- the Apps grid lists applications whose desktop entry says `X-Luma-TV=true`,
  from the same catalogue Home uses (`Shell.AppSystem`), each drawn with its
  `X-Luma-TV-Tile` icon (a 16:9 wide tile) and nothing hard-coded;
- the remote: arrows move (spatial, `tvModel.nearest`), moving along the bar
  opens the page, Down enters the content, Up from its first row returns to
  the open tab, OK opens, Back steps out to the bar and then to Home; Super
  and the overview's gestures go Home;
- windows: every application window goes full screen as it maps; minimising
  the last one (LumaUI does that for an unhandled Back) returns to Apps;
  windows open before TV are minimised on entry and restored on exit;
- no Dash, panel, quick settings, overview or sticky stack.

Every length is a TV unit from `tvTokens.js` (generated from the `tv` block of
`config/shared/design-tokens.json`), one pixel of a 1920x1080 layout scaled by
`min(width/1920, height/1080)`, so any screen size or aspect works.
`tvModel.js` holds the pure rules (opt-in, search, the tile grid, spatial
focus, remote keys) and is tested by `tests/shell-surfaces/tv-model.mjs`.
