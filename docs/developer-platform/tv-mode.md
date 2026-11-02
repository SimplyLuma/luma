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
- No hover-only affordances: TV is driven by a remote or a gamepad (arrows,
  OK, Back); see "Remotes and gamepads".

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
- **TvField**: a recessed field around a real `Gtk.Text`; `action=` labels the
  TV keyboard's action key (see "Typing").
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

## Remotes and gamepads

Every controller drives TV Mode through the same keys, so an application
reads keys and never a gamepad or a remote. In TV posture one Shell service
(`src/luma-shell-presentation/tvInput.js`, rules in `tvInputModel.js`) turns
gamepads (USB, 2.4 GHz dongles, Bluetooth), TV remotes (Bluetooth and 2.4 GHz
HID remotes) and the TV's own remote over HDMI-CEC into the TV's keys before
they reach any window. `TvFocus`, the TV home and the TV keyboard need nothing
more.

| TV action | Key the app sees | Keyboard / remote | Gamepad |
|---|---|---|---|
| Move | Up, Down, Left, Right | arrows | D-pad, left stick |
| OK | Return | Return, Enter, OK, Select | the confirm face button (A) |
| Back | Escape | Escape, Back, Exit (CEC Back) | the other face button (B), Select (View/Back) |
| Menu (reserved for the action bar) | Menu | Menu, Context Menu, CEC Contents Menu | Start (Menu) |
| Home | none: the Shell opens the TV home | Home, CEC Root Menu | Guide, or Select+Start |

- **Keys, not a protocol.** OK is Return, Back is Escape, Menu is the `Menu`
  keysym. Handle those and every controller works. Backspace still counts as
  Back where nothing is being typed (`TvFocus`). Home never reaches an app.
- **Gamepads.** The Shell reads pads through libmanette, which maps every pad
  it knows onto one layout. The D-pad and the left stick move; a held
  direction steps once, again after 400 ms and then every 120 ms
  (`tv.input` in `config/shared/design-tokens.json`, with the stick's
  thresholds). North, West, the shoulders, the triggers, the stick clicks and
  the right stick are not assigned yet. OK is held: Return goes down with the
  button (or the remote's OK, Select or CEC Select) and up with it, so a long
  press opens an item's menu (`TvFocus`); its key repeat is not more OKs.
  Back, Menu and the arrows are whole presses.
- **A and B.** OK is the bottom face button (South: A on Xbox-style pads,
  Cross on PlayStation) or the right one (East: A on Nintendo-style pads),
  and Back is the other. The luma-shell-state key `tv-confirm-button`
  (`automatic`, `south`, `east`) chooses; `automatic` picks East for
  controllers that identify as Nintendo layout (Nintendo's own and 8BitDo in
  Switch mode by vendor, known SNES-style USB pads by vendor and product, or
  the name) and South for the rest. A pad that misreports itself (an SNES-style
  pad presenting as an Xbox 360 pad) needs the explicit choice:
  `gsettings set org.project_luma.shell-state tv-confirm-button east`. The
  change applies at once; TV Settings will offer it under Remotes.
- **Remotes.** Arrows and Enter are already TV keys. Keys that are not
  (`XF86Back`, `XF86Close`, `XF86OK`, `XF86Select`, `XF86HomePage`,
  `XF86RootMenu`, `XF86MenuKB`, `XF86ContextMenu`) are taken by the Shell and
  sent on as the TV key, so an application never sees them in TV posture.
- **HDMI-CEC.** `luma-tv-cec` (from `luma-shell-state`, started for every
  `/dev/cec*` by udev) puts the adapter on the bus as a playback device named
  Luma, with remote passthrough allowed. The kernel then answers the core CEC
  messages itself and turns the TV remote's buttons into keys on the adapter's
  `rc-cec` input device, and the mapping above applies. The TV's CEC setting
  (Anynet+, SimpLink, Bravia Sync, ...) must be on, and the TV sends its
  remote to the input it is showing.
- **Outside TV posture** nothing is translated: pads and remotes behave as on
  any desktop.

### Games read the pad themselves

A gamepad's keys go only to the Shell (the TV home, dialogs), the TV keyboard
while it is up, and applications that declare `presentation = "tv"` (built
for the remote). Every other application, a game or an emulator that
declares no TV presentation among them, gets the pad untouched and no keys
from it, as on a desktop; Home (Guide, or Select+Start) still returns to the
TV home. The pad is never grabbed: an application can always read it.

A TV application that reads the pad itself in some views (an emulator's game,
a game with a TV library) claims the pads while it does. A claiming process's
windows get no keys from a pad at all, Home included; the TV keyboard still
takes the pad while it is up:

```python
from luma_appkit import claim_gamepads

claim_gamepads(True)    # the game starts: read the pad yourself
claim_gamepads(False)   # back to the library: TV keys again
```

Underneath, for other toolkits: the session-bus service
`org.projectluma.Shell.Remote` at `/org/projectluma/Shell/Remote`, interface
`org.projectluma.Shell.Remote1`, method `ClaimGamepads(b claimed)` (the claim
covers the calling process's windows and ends with `false` or when the caller
leaves the bus; say it again if the name's owner changes) and property
`Gamepads` (as, the names of connected pads). The service exists only in TV
posture.

## Typing

There is one on-screen keyboard. In TV posture the shell's Keycap keyboard
(the one phones and tablets use) takes its TV form for every text field, the
shell's and every application's: docked to the bottom, the person's language
layout in an aligned grid, as Android TV and tvOS do: every key the same
size on the same 11 columns, so the remote and a gamepad's D-pad move in
straight lines (Left and Right wrap in a row, Up and Down keep their column):

```
1 2 3 4 5 6 7 8 9 0 ⌫
q w e r t y u i o p @
a s d f g h j k l _ &
z x c v b n m , . - ?
123  ⇧  [    space    ] [ Search ]
```

123 swaps the letter rows for symbols (one page, as Leanback); the
focus starts on q. Applications use ordinary text
widgets (a `TvField`'s `Gtk.Text`, a `Gtk.Entry`); there is no keyboard
widget to embed and nothing to draw.

- **It belongs to its field.** It opens when a field takes the focus (the
  remote moving onto it is the request to type) and closes when the focus
  leaves the field. Back, or Up from the top row, puts it away and leaves the
  field focused; OK on the field opens it again.
- **The remote.** While it is open the shell takes Up, Down, Left, Right, OK
  (Return, KP_Enter) and Back (Escape) from every window, so a
  `TvFocus` sees none of them and needs no grab; when it closes they reach the
  app again. Backspace and letters always go to the field.
- **A real keyboard types straight in.** The first letter typed on a real
  keyboard puts the on-screen keyboard away; Return then submits as usual.
- **The action key** sends Enter to the field (its `activate` signal, or the
  `on_activate` of a `TvFocus` editable item) and puts the keyboard away. Its
  label says what Enter does here. Set it, the one way:

  ```python
  field = TvField(placeholder=_("Add a place"), action=_("Add"))
  set_keyboard_action(entry, _("Search"))   # any Gtk widget; applies to fields inside it
  ```

  Without a label it reads Go for an address field
  (`Gtk.InputPurpose.URL`) and Done otherwise. Number, PIN and phone
  purposes get the number pads.
- **Its height.** The keyboard is 428 TV units tall and never resizes or
  slides a TV window: the app keeps its whole screen and lifts what sits at
  the bottom. A `TvStage` does this by itself: `docked` is `"keyboard"`
  while it is up, so `TvToast` rises to `toast.above_keyboard` (586) and an
  action bar should sit at `keyboard.above` (470). Anything else can follow
  it:

  ```python
  stop = watch_tv_keyboard(lambda visible, height: lift(height))   # TV units, 0 when hidden
  visible, height = tv_keyboard_state()
  ```

The contract underneath, for toolkits other than LumaUI, is the session-bus
service `org.projectluma.Shell.Keyboard` at `/org/projectluma/Shell/Keyboard`,
interface `org.projectluma.Shell.Keyboard1`: `SetActionLabel(s label)` labels
the action key for the window that has the focus (call it as a field takes
the focus, and with `""` as it leaves), and the properties `Visible` (b) and
`Height` (u, TV units) change with the keyboard. Outside TV posture the
keyboard ignores the label and never reports itself visible. The layout and
look are the `tv.keyboard` tokens; the shell side is
`patches/gnome-shell/0309-tv-keyboard.patch` with its pure rules in
`src/luma-shell-presentation/tvKeyboard.js`.

## Menus and long press

An item's menu on TV is the same command model as its desktop context menu,
never a TV copy. Pass the `CommandRegistry` you give `attach_context_menu`
(or a callable returning it) when registering the item with `TvFocus`:

```python
attach_context_menu(row, commands_for(place))                      # desktop
focus.add(tab, on_activate=open_place, menu=lambda: commands_for(place))   # TV
```

- **Hold OK** (`tv.focus.hold_ms`, 500 ms) or press **Menu** to open it. On an
  item with a menu a short press is still OK: it runs `on_activate` when OK is
  released; auto-repeat while OK is held changes nothing, and the release
  that follows a hold is swallowed. Items without a menu act on the press.
- `TvMenu` shows the model's visible commands, one level (commands with
  children are left out), as a floating panel in the bar material anchored
  under the item (above it when there is no room): icon, label, a tick for a
  checked command, destructive commands in the danger colour. OK runs a
  command; Back closes; either way the focus returns to the item.
- A command's `visible` and `enabled` predicates decide what TV shows, so a
  posture rule lives in the model (Weather hides Remove for your location in
  TV posture).
- `TvFocus.open_menu()` opens the focused item's menu from code;
  `TvFocus.release(keyval)` feeds a key release (tests, scripted reviews).

The hold needs a real key release: a keyboard, a remote, or a gamepad whose
OK button is sent as a press on button-down and a release on button-up.

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
