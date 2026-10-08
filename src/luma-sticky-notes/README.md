# Sticky Notes

Sticky Notes is Project Luma's edge-docked note application: one
GTK4/libadwaita application and one `luma-sticky-notes` package, built on the
public Luma Developer Platform rather than as a `prairie-core-apps` member.
Prairie's package contract enumerates the native communications,
personal-information and hardware core; Sticky Notes is additive, the way Tide
is.

## Architecture

- `store.py` owns the SQLite schema, the note model, and forward-only
  `PRAGMA user_version` migration. It imports no toolkit and needs no display,
  so the whole model is testable without a session. Note text never reaches a
  log: the module cannot log at all, and a test asserts it.
- `glyphs.py` draws the two marks no icon theme carries — the new-note plus
  and the pin — plus the rotated tab title. GTK 4 removed `GtkLabel`'s angle,
  so tab text is a Pango layout painted through a rotated matrix in the
  widget's own font and foreground.
- `dock.py` is the tab stack, the peek, and the new-note button.
- `note_window.py` is one note's window: an AppKit `AppWindow`, so the frame,
  the identity corner and the window controls are the toolkit's.
- `application.py` wires the store to the dock and the note windows.

No `AdwBreakpoint` is used anywhere. Adding one makes `AdwBreakpointBin`
report a minimum size of zero, after which the window shrinks to whatever
`set_size_request` claims and clips its own content. Neither surface here
needs a breakpoint — the dock is a fixed-width rail and a note window is
small and square — so the cheapest correct answer is not to introduce one.

## Where the dock actually sits

The dock asks the session for the screen edge and accepts the answer:

- Under **phoc/wlroots** (Luma's handheld lane) `wlr-layer-shell` exists and
  is the supported way to put a surface above ordinary windows without taking
  their keyboard focus. The dock takes the TOP layer, anchors to the right
  edge, and uses `ON_DEMAND` keyboard mode so it never holds the keyboard
  unless somebody is using it. `gtk4-layer-shell` is imported optionally, the
  way `prairie-phone-daemon` already imports it; it is not a package
  dependency and a session without it loses nothing but the layer.
- Under **Mutter** (Luma's desktop lane) there is no such protocol and no
  application window of any kind can claim that layer. The dock is then an
  ordinary undecorated window: it can be covered, and it does take focus when
  clicked. There is no re-raise timer, no always-on-top hint and no
  focus-stealing workaround, because the session offers no contract to honour
  and a loop that fights the compositor would be worse than an honest window.
  The rail is a `GtkWindowHandle` so the window can still be dragged into
  place without a title bar.

The owned answer for the desktop is the Shelf — the dock Luma's own shell
draws through `Main.layoutManager.addChrome`, with `affectsStruts`. A note
stack that is genuinely always available on the desktop belongs there, which
is a shell change and not an application one. See the report accompanying
this branch.

## Data

`$XDG_DATA_HOME/luma/sticky-notes/notes.sqlite3`, directory `0700` and file
`0600`, both asserted on every launch rather than only on the first. All SQL
is parameterised. `Mark complete` is a state the note keeps. `Delete` moves
the note to a trash it can be restored from; the dock offers Undo immediately
and the store keeps the note for thirty days, purged at start-up rather than
by a resident timer.
