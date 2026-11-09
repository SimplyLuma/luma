---
title: LumaUI behaviour
summary: How LumaUI parts respond, with their motion tokens: hover, press, focus, keyboard, slide in and out, scroll fades, auto-close, reduced motion and phone width.
order: 10
section: Kit
---

# LumaUI behaviour

Every number below is a LumaUI design token. Python reads them from
`luma_appkit.lumaui_tokens` (motion is `lumaui_tokens.MOTION`) and, for
anything timed, from `luma_appkit.lumaui.duration(name)`, which already honours
reduced motion. CSS reads them as `--lumaui-*` custom properties, such as
`--lumaui-ease` and `--lumaui-duration-hover`.

## Motion tokens

| Token | Value | Used for |
| --- | --- | --- |
| `ease` | `cubic-bezier(.3, .7, .2, 1)` | Anything that settles: panes, drawers, scrims, bar position |
| `spring` | `cubic-bezier(.3, 1.3, .5, 1)` | Anything that arrives: toasts, dialogs, pressed and lifted buttons |
| `hover_ms` | 120 | Hover fills and filters |
| `press_ms` | 150 | Press and lift transforms |
| `fade_ms` | 200 | Plain fades (a toast leaving, a page cross-fade) |
| `morph_ms` | 300 | A bar or panel changing shape (action center, details pane in) |
| `drawer_ms` | 260 | A drawer rising from the bottom edge |
| `toast_in_ms` / `toast_rise_px` | 300 / 12 | A toast springs up 12 px as it fades in |
| `dialog_fade_ms` / `dialog_move_ms` | 160 / 360 | The dialog fades fast and springs from 96% scale, 10 px low |
| `scrim_ms` | 200 | The dim behind a dialog or drawer |
| `nudge_ms` / `nudge_px` | 300 / 6 | A click behind a dialog shakes it sideways |
| `reduced_fade_ms` | 150 | What every movement becomes under reduced motion |
| `toast_ms` / `toast_undo_ms` | 2400 / 4000 | How long a toast stays; 4 s when it offers Undo |
| `tooltip_delay_ms` | 500 | Hover before a tooltip |
| `grow_ms` / `grow_rise_px` | 320 / 16 | The action center growing into its editor (spring, from 98%) |
| `bar_morph_ms` / `morph_rise_px` | 260 / 4 | A bar's actions changing; the bubble and place list arriving |
| `bubble_in_ms` | 140 | The selection bubble and the place suggestions fading in |
| `bubble_settle_ms` / `search_debounce_ms` | 200 / 180 | Waiting for a selection to settle, or for typing to pause (not motion: never shortened) |

Never animate for decoration and never delay a response to wait for an
animation: a state changes on press, and only the transition animates.

## Interaction states

| State | Look | Motion |
| --- | --- | --- |
| Hover | A fill one step up behind the control (`@luma_hover`, `@luma_control_fill_hover`); a raised chip lifts 1 px and brightens (dark) or dims slightly (light), `--lumaui-hover-brightness` | `hover_ms` |
| Pressed | One fill step deeper; a raised chip sinks to 97% | `press_ms`, spring |
| Focus (keyboard only) | A 2 px `@luma_focus_ring` outline 2 px outside the shape (`:focus-visible`); never on a mouse click | none |
| Selected / on | The raised chip (`@luma_chip` and its edge) | none |
| Disabled | 35% opacity, stays in place, no pointer | none |
| Busy | A spinner where the icon was | none |
| Danger | Red ink (`@luma_danger_ink`) on the usual surface; a filled red action only inside the destructive dialog | none |

## Keyboard

- **Tab** reaches everything; **arrows** move inside a group (segments, a
  list, the suggestions above a field); **Enter** and **Space** activate;
  **Esc** backs out one level: it closes a drawer, a dialog, a details pane or
  a menu, and folds an action center back to its bar (keeping the draft).
- A modal card (`LayerHost.present_modal`: the destructive dialog, drawers)
  keeps Tab inside itself, cycling forwards and backwards; the window behind
  cannot take focus; Esc cancels; focus returns to the control that opened it.
- The destructive dialog focuses **Cancel**, so Enter never deletes by
  accident.
- **F9** shows and hides the sidebar (`SidebarToggle`, anywhere in the
  window; at phone width it opens and closes the side drawer). Arrows move
  between table headings, between modes (choosing as they go), between the
  corner pill's controls and between a menu drawer's rows. **Alt+F10** focuses
  the selection bubble; Left and Right move inside it, and Esc hides it and
  returns to the text.
- The **action center**: its prompt is a button (Enter or Space grows the
  editor, which takes focus); **Ctrl+Return** runs the primary action (Send);
  **Esc** folds back, keeping the draft, with focus on the prompt.
- **Place search**: Up and Down move through the places, Enter adds the
  chosen one (the top one unless you moved, even before the typing pause
  ends), Esc closes the list and a second Esc clears the field.
- **`HeroTitleField`**: Enter commits the name, Esc puts back what was there.
- Anchored menus (Open in, modes): Up and Down move, Enter picks, Esc closes
  and focus returns to the button.

## Slide in and out

| Part | In | Out |
| --- | --- | --- |
| Toast | Fades in over `fade_ms` while springing up `toast_rise_px` over `toast_in_ms` | Fades out over `fade_ms`, then leaves the overlay |
| Destructive dialog | Scrim fades in (`scrim_ms`); the card fades (`dialog_fade_ms`) and springs from 96% scale, 10 px low (`dialog_move_ms`) | Both fade; the card is removed after `dialog_fade_ms` |
| Drawer (a modal card at phone width) | Rises 48 px from the bottom edge over `drawer_ms` with `ease`; the window dims (`@luma_drawer_scrim`) | Fades; drag it down more than 80 px or tap outside to close |
| Nudge | A click on the scrim behind a dialog shakes the card ±6 px (`nudge_ms`) | |
| Details pane | Slides 14 px from its edge and fades, 300 ms `ease` | Reverse |
| Action center | The editor grows in place: it fades in and springs up 16 px from 98% over `grow_ms` (320). A bar that changes its actions fades them in from 4 px below over `bar_morph_ms` (260) | Esc folds straight back to the bar, the draft kept in its prompt ("Draft: …") |
| Selection bubble | Fades in from 4 px over `bubble_in_ms` (140), 10 px above the selection, flipped below within 12 px of the top | Hides at once |
| Place suggestions | Open 6 px above the field, fading in over `bubble_in_ms` | Close when a place is added, on Esc, or when the field loses focus |

## Scroll fades

`ScrollView` fades its content at the top edge once it has scrolled more than
2 px, so content slips under a header instead of being cut (a 22 px fade). Pass
`fade_bottom=True` for a 24 px fade at the bottom while there is more below.
`fade_top_start` and `fade_top_end` move the top fade below a transparent
header band, and `fade_top_always=True` keeps it even at the top. It is a real
alpha mask, so it works over any surface.

## Auto-close

- A **toast** leaves after 2.4 s, or 4 s with Undo; a busy toast stays until
  the app dismisses it. A new toast replaces the one showing: there is only
  ever one.
- A **details pane** closes when its subject goes away (the file closes, the
  thread is deleted) unless it is the view's main pane (`main=True`).
- A **selection bubble** waits 200 ms after a drag or a keyboard selection
  ends, and hides on scroll, on typing and when the selection ends.
- **Place search** waits 180 ms after typing stops before it searches.
- A **drawer** closes on a tap outside it or a drag down; a **dialog** does
  not close on a click outside, it nudges.
- **Menus** close on a click outside and on Esc.

## Reduced motion

When the desktop asks for reduced motion (`gtk-interface-reduced-motion`) or
turns animations off, movement becomes a fade:

- CSS: LumaUI parts override their movement inside
  `@media (prefers-reduced-motion: reduce)`: no translate, scale, spring or
  shake, only an opacity transition of `reduced_fade_ms`.
- Python: `lumaui.duration()` returns at most `reduced_fade_ms` for motion
  (and 0 when animations are off entirely); timeouts that are not motion
  (toast lifetimes, the tooltip delay) are never shortened.
- `lumaui.reduced_motion()` answers the question for code that animates by
  hand. The nudge is skipped entirely.

## Phone width

Width adapts layout, never identity. A part is at phone width when the place
it appears in is under 560 px wide (`lumaui.is_phone_width(widget)`, using
`lumaui_tokens.PHONE_MAX_WIDTH`, 559). Then:

- A modal card from `LayerHost.present_modal` becomes a bottom drawer: full
  width, grab handle, 28 px top corners, rows at least 46 px tall for a thumb.
- The destructive dialog becomes a confirm inside the bar's frame: the title
  and one line, no icon, with Cancel and the red action side by side.
- Menus open as drawers unless they ask to stay anchored.
- A toast keeps 12 px from each side.
- The sidebar opens as a drawer from the left over the dimmed window; a tap
  beside it, Esc or choosing a row closes it.
- The action center's editor becomes a sheet from the bottom edge, 60 px below
  the top with 26 px top corners. Its bars span the width less 12 px a side,
  and a split bar stacks.
