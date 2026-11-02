# TV Mode for applications

TV Mode is a posture of the Luma shell, beside desktop, tablet and phone. It
is not a separate shell, session or application catalogue, and a TV app is
not a separate app: an application gains a TV presentation and shows it while
the shell is in TV posture. The same application ID, package and data serve
every posture.

## Posture

The shell owns the setting (`luma-shell-state`, key `posture`: `automatic` or
`tv`). Turn it on with `luma-shell-statectl set posture tv` and off with
`luma-shell-statectl set posture auto`. The switch is live; nothing logs out.
A device that declares itself a television (`tv` in `/etc/luma-device-class`)
starts in TV posture while the setting is Automatic.

Posture is independent of presentation. A TV is a desktop presentation
underneath, so `AppContext.presentation`, `mobile_form_factor()` callers that
expect a computer, and width breakpoints behave as on a desktop. In TV posture
`mobile_form_factor()` is always false.

Applications ask LumaUI, never the screen size or aspect:

```python
from luma_appkit import lumaui

if lumaui.tv_posture():
    show_tv_presentation()

stop = lumaui.watch_posture(lambda tv: show_tv_presentation() if tv else show_normal())
```

An `AppWindow` carries the same fact as the `tv-posture` property:

```python
window.connect("notify::tv-posture", lambda w, _p: switch(w.tv_posture))
```

`AppContext.posture` is `"tv"` or `""` (`AppContext.tv` is the boolean). The
live value comes from the shell-state `Posture` D-Bus property through the
session context every `AppWindow` already follows. `LUMA_POSTURE=tv` forces TV
posture for tests, reviews and capture harnesses. Without the shell-state
service or schema (another distribution, a sandbox without the bus name) the
posture is never TV.

## What every window gets in TV posture

- The shell runs the window full screen. LumaUI drops the title row and window
  controls, and adds the `lumaui-tv` style class to the window.
- Back that nothing inside the window handled (Escape or the remote's Back key
  reaching the window itself) minimises it, which returns to the TV home's
  Apps page. A presentation with its own levels handles Back itself until it
  is at its top level. Any application, LumaUI or not, can return to the TV
  home by minimising; the TV home shows whenever no app window does.
- No hover-only affordances: TV is driven by a remote (arrows, OK, Back).

## The TV scale

One unit for every TV length: one pixel of a 1920x1080 layout,
`lumaui.tv_unit(width, height)` = `min(width/1920, height/1080)`. Everything
is sized in units and pinned to an edge or the centre, so any screen size or
aspect ratio works. The values (safe area 96/56, the shared top bar, type,
covers, buttons, wide tiles and the well/chip/key/bar materials) live in the
`tv` block of `config/shared/design-tokens.json`. Python reads them as
`luma_appkit.lumaui_tokens.TV`; the shell as `tvTokens.js`. Use the tokens;
do not copy numbers into an application.

## TV parts

LumaUI gives every TV presentation the same parts (`luma_appkit`, API level 4),
so apps differ in what they show, not in how big a button is or how focus
moves. Build the TV presentation as a view inside the app and show it while
`tv_posture()` is true.

```python
from luma_appkit import TvStage, TvTopBar, TvTab, TvFocus, TvToast, TvIdle, add_tv_style

stage = TvStage()                                   # the unit, the materials, pinned children
bar = stage.place(TvTopBar(), **TvTopBar.PIN)       # the shared bar: same place as the TV home
add_tv_style(".my-hero { font-size: 200tv; }")      # sheets in TV units (`tv` suffix)
focus = TvFocus(stage, on_back=leave_level)         # spatial focus, OK, Back
focus.add(widget, group="days", on_activate=pick, on_focus=preview, on_key=scrub)
TvToast.show(stage, "Added Lisbon", lead="map-pin")
TvIdle(focus, seconds=20, on_idle=show_glance, on_wake=hide_glance)
```

- **TvStage**: `place(child, left=, top=, right=, bottom=, width=, height=,
  center_x=, fill=)` in TV units. `unit` follows the window; `docked` names
  what sits at the bottom edge (`"keyboard"`, `"action-bar"`) so the toast
  rises above it.
- **Sheets in TV units**: `add_tv_style(css)` takes CSS whose lengths end in
  `tv` and keeps it in pixels for the current unit. The kit's sheet defines
  type (`.tv-h1`, `.tv-h2`, `.tv-body`, `.tv-meta`, `.tv-caption`,
  `.tv-caption-meta`), materials (`.tv-well` recessed, `.tv-chip` chosen,
  `.tv-key` the focus, `.tv-bar` floating; opaque under Reduce Transparency or
  High Contrast), `.tv-btn`, `.tv-sheet`, `.tv-scrim`, `.tv-field`, `.tv-row`,
  `.tv-action` and `.tv-ring` (a ring around a recessed thing).
- **TvFocus**: one visible focus, moved to the nearest focusable in the
  pressed direction (`tv_nearest()`). The focused widget gets `tv-focus` and
  GTK focus where focusable; `TvLift` children lift. `set_group_entry()` makes
  a row land on its chosen item; `push_scope()` confines focus to a sheet and
  returns it on Back. `editable=True` items (a `TvField`'s `text`) keep typing,
  Left/Right and Backspace, so a real keyboard and the system's TV keyboard
  type straight in. Back nothing takes falls through to the window (the TV
  home at the app's top level).
- **TvTopBar / TvTab**: the track of tabs and the clock; the open tab is the
  key unless another tab holds the focus.
- **TvButton**: 66 tall, chip at rest, key with the focus; `danger=True`.
- **TvField**: a recessed field around a real `Gtk.Text`.
- **TvToast**: lead, kicker, title, value, sub, tint, ms; one at a time.
- **TvIdle**: an app's idle layer after a delay without input; any key wakes
  and does nothing else; Shift+I forces it, `LUMA_TV_IDLE_MS` sets the delay;
  `hold()` keeps the app awake.

### Media parts

Apps that show records, films, games or pictures use the media parts
(`luma_appkit.tv_media`), so a cover, a play button or the backdrop is the same
in every app. They follow the unit of the `TvStage` they sit on and register
with `TvFocus` like any other part.

```python
from luma_appkit import TvCover, TvTransportButton, TvNowPlayingPill, TvProgress, TvRoom, TvSlide

room = stage.place(TvRoom(), fill=True)             # first: the item's own art, blurred, as the room
room.set_artwork(path)                              # cross-fades over 1.1 s; hues=(h1, h2, h3) without art
cover = TvCover(shape="square", artwork=path, title="Album", subtitle="Artist")
focus.add(cover, on_activate=open_album)            # lifts 1.08, white ring on the art only
play = TvTransportButton("play", main=True, label="Play")   # round: 108 main, 84 otherwise
pill = TvNowPlayingPill()                           # what's playing, in the shared top bar
bar.end.prepend(pill)
pill.show_track("Song", "Artist", artwork, playing=True)    # None hides it
shelf = TvSlide(row, orientation=Gtk.Orientation.HORIZONTAL, clip=False)
```

- **TvCover**: square 260 (music), portrait 230 wide at 3:4 (games, film),
  landscape 400 wide at 16:9 (screenshots, episodes); `width=` for a hero or a
  page's large sleeve, `radius=` for the corner (20 by default). With the focus
  it lifts 1.08 and a 5-unit white ring hugs the art, concentric with its
  corner; the caption (`title`, `subtitle`) never carries the ring. Without
  art it draws a generated sleeve from three `hues` (`hues_for(name)` gives
  stable ones) and `name` on it. Artwork loads off the main loop.
- **TvTransportButton**: round, chip at rest and key with the focus, a filled
  Lucide transport glyph (`play`, `pause`, `skip-back`, `skip-forward`);
  `set_icon()` swaps it. Space it with the `tv.media.transport.gap` (22).
- **TvNowPlayingPill**: the sleeve, song and artist and a three-bar
  equaliser (still while paused), bar material at rest and key with the focus,
  at most 520 wide. It rides before the clock in the shared top bar on every
  page while something plays; OK on it opens the app's Now playing.
- **TvProgress**: a recessed well with a key fill (Now playing), or
  `hairline=True` for an idle view.
- **TvRoom**: the backdrop. The art is decoded small, blurred and graded once
  off the main loop and cached, then drawn as one texture; a change
  cross-fades two textures. There is no live blur, so a 4K TV on a small GPU
  stays smooth. A shade keeps the bar and text legible.
- **TvSlide**: a row or page that slides to keep the focus in view (call
  `reveal(widget)` from `TvFocus`'s change callback), only as far as needed,
  with `peek` of the next item; no scrollbars.
- `.tv-playing` colours the playing song (its title, number, equaliser).

Values come from the `tv` block of `config/shared/design-tokens.json`
(`focus`, `sheet`, `toast`, `keyboard` layout, `idle`, `motion`, `cover`,
`button`) and its `media` fragment, `config/shared/design-tokens.d/tv-media.json`.

## Appearing on the TV home

Only applications that opt in appear on the TV home. Declare TV support in
`luma-app.toml`:

```toml
[tv]
supported = true
tile = "data/org.example.App-tv.svg"   # the 16:9 wide tile
presentation = "tv"                    # the app shows its TV presentation in TV posture
```

The shell reads applications from the same catalogue as Home and the shelf
(installed desktop entries), so the declaration is installed as three
desktop-entry keys, which `luma lint` checks against the manifest:

```ini
X-Luma-TV=true
X-Luma-TV-Tile=org.example.App-tv
X-Luma-TV-Presentation=tv
```

Install the tile as an icon named after the application ID with `-tv`
(`share/icons/hicolor/scalable/apps/org.example.App-tv.svg`), so a Flatpak
exports it like its other icons. Make it from the application's v4 icon with
the Design Center's `tools/wide-tile.py` (plate, art between plate and rim,
rim; art centred at 1.15x); its corner is a share of the tile
(rx 190 of 1600x900), and it carries its own rim.

`presentation` names what the app shows in TV posture. `tv` means the app
follows `tv_posture()` with its own TV presentation; `fullscreen` means it has
none yet and shows its normal view full screen. No `[tv]` table, or
`supported = false`, means the app does not appear on the TV home; it still
opens normally from anywhere else.
