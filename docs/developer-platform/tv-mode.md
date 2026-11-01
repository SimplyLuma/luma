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
