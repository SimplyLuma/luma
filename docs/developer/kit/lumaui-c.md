---
title: LumaUI for C
summary: The LumaUI parts in C (LumaUI-1), each next to its Python twin, the rules they share, and how to build a C or C++ app against them.
order: 12
section: Kit
---

# LumaUI for C

`LumaUI-1` is LumaUI’s C library (`libluma-ui-1.so.0`, `#include <luma-ui.h>`,
GIR namespace `LumaUI`). C++ apps such as Grid, Stage, Session and Write use it
directly, with the same parts as the Python apps. Each part is a final GObject
with introspection metadata, and each builds **the same widget tree with the
same CSS node names and classes** as its Python twin: one LumaUI stylesheet
(`luma-appkit-base.css`) styles both, and a parity test walks both trees and
compares them.

The rules are the same as in Python ([principles](lumaui-principles.md),
[behaviour](behaviour.md)): the app says *what*, LumaUI owns *how*. There are
no colour, size or duration arguments; a new look is a named variant and a
token. C apps never select `.lumaui-*` in their own sheets.

## How the C API says things

- **Commands are `GAction` names.** Where Python takes a callback for a plain
  press, C takes a detailed action name (`"win.edit"`), the command's one
  stable ID, so menus, shortcuts and the part all bind the same command. On/off
  states are stateful boolean actions.
- **Events with data are signals.** A mode change, a sort, a confirmed
  dialog, a picked place: `changed`, `sort-changed`, `confirmed`, `picked`.
  Controls whose menu anchors to them (Open in, Share) emit a signal with the
  anchor.
- **Builders, not keyword lists.** Python's `CornerPill(share=…, actions=[…])`
  is `luma_corner_pill_new()` then `add_share()`, `add_action()`; LumaUI still
  puts everything in its one order.
- **Misuse is a critical.** What Python refuses with `ValueError` (a lone
  stacked button, "Are you sure?", an unknown toast kind) is a `g_critical`.
  Run your tests with `G_DEBUG=fatal-criticals` so they fail on one.
- `LUMA_UI_API_LEVEL` is the C library’s API level (2 today, the same as
  Python’s `LUMAUI_API_LEVEL`), and `luma_ui_get_api_level()` returns the level
  of the library actually loaded. It follows the policy in
  [principles](lumaui-principles.md#api-level-compatibility-and-deprecation).

## The parts

| C (`LumaUI-1`) | Python (`luma_appkit`) |
| --- | --- |
| **Core** `luma-ui-kit.h`: `luma_ui_install()`, `luma_ui_icon_name("share-2")`, `luma_ui_icon_new()`, `luma_ui_apply_type(w, "title-1")`, `luma_ui_reduced_motion()`, `luma_ui_is_phone_width()`, `luma_ui_count_text()`, `luma_ui_initials()`, `luma_ui_person_tone()` | `install_lumaui`, `lumaui_icon`, `apply_type`, `reduced_motion`, `structure_adapt.is_phone`, `count_text`, `content_cards.initials`, `content_cards.person_tone` |
| **Window** `LumaApplicationWindow`: `new(application, context)`, `set_identity()`, `set_menu_model()`, `set_frame(sidebar, island)`, `set_leading()`, `set_title_content(widget, centred)`, `set_trailing()`, `get_layer_host()` | `AppWindow`, with its frame, title row, identity, window controls and body |
| `LumaIsland`: `new(orientation)`, then `GtkBox.append()`; clipped content surface and inset edge | `Island(orientation=)` |
| `LumaNavigationSidebar`: `new(variant, width)`, `set_variant()`, `append_header()`, `append_footer()`, `append_section()`, `append_row()`, `clear()`, `get_list()`; `LumaNavigationRow`: `new(title, subtitle)`, `set_title/subtitle/meta/lead/trail()` | `NavigationSidebar(variant=, width=)`, `SidebarRow` destination subset |
| `LumaTypeLabel`: `new(text, role)`, `set_unit()`, `set_role()` | `TypeLabel(text, role=, unit=)` |
| `LumaLayerHost` (node `overlay`): `new(child)`, `for_widget()`, `window_host()`, `install(window)`, `present_modal(card, focus, LumaDrawerMode) → LumaModalHandle`, `track_bar(bar)`; `LumaModalHandle`: `cancel()`, `close()`, `nudge()`, `::cancelled` | `LayerHost`, `structure_layers.ModalHandle`, `ToastHost` (`track_bar`) |
| **Placement** `LumaModeSwitch`: `new(label)`, `add(key, label, icon)`, `set_current()`, `::changed(key)` | `ModeSwitch([(key, label, icon)], current=, on_change=)` |
| `LumaCornerPill`: `set_modes()`, `set_people()`, `add_open_in()` + `::open-in(anchor)`, `add_share()` + `::share(anchor)`, `add_action(icon, label, action)`, `add_state(icon, label, bool action)`, `set_info_pane(pane)` / `set_info_action()`, `set_more_menu(GMenuModel)`, `set_labelled()`, `set_primary(label)`, `edit(done)`, `stop_editing()`, `::edit-cancelled`, `::edit-done` | `CornerPill(modes=, people=, open_in=, share=, actions=, states=, info=, more=, labelled=, primary=)`, `.edit(on_cancel, on_done)` |
| **Sidebar** `LumaSidebarFoot`: `new(search)`, `add_filter(key, label, icon)`, `set_filter()`, `set_filter_count(key, n)`, `set_add(label, icon, action)`, `open_filters()`, `get_entry()`, `get_heading()`, `::search-changed(text)`, `::filter-changed(key)`; `LumaFilterHeading`: `::show-all` | `SidebarFoot(search=, on_search=, filters=, filter=, on_filter=, add=)`, `FilterHeading` |
| `LumaSidebarToggle` (a `GtkToggleButton`): `new(sidebar \| AdwOverlaySplitView, shown)`, `:shown`, `toggle()` (F9) | `SidebarToggle(sidebar, shown=, closed=)` |
| **Badges** `LumaCountBadge` (node `label`): `new(count, attention)`; `LumaCategoryPill` (node `label`): `new("media")`; `LumaStatusPill`: `new(kind, label)`, `set_kind()` | `CountBadge`, `CategoryPill`, `StatusPill` |
| **Actions** `luma_toast_show(where, message, kind)`, `luma_toast_show_with_undo()` + `::undo`, `luma_toast_show_busy()`, `luma_toast_dismiss()` | `Toast.show(where, msg, kind=, undo=, busy=)` |
| `luma_destructive_dialog_ask(where, title, body, action, icon, option)`, `::confirmed(option_checked)`, `::cancelled` | `DestructiveDialog.ask(...)` |
| `LumaStackedButton`: `new(icon, label, danger)`; `LumaStackedButtons`: `new(small)`, `append()` (pairs or trios) | `StackedButton`, `StackedButtons` |
| **Details** `LumaDetailsPane`: `new(title, main)`, `add_hero()`, `add_section(label, action_label, action)`, `add_fact(key, value)`, `add_row(row)`, `add_photos(model)`, `add()`, `clear()`, `show(open)`, `set_subject()`, `close()`, `toggle()`, `info_button()`, `:shown` | `DetailsPane(title, main=)` with `add_facts([...])`, `add_list([...])`, `show(open=, subject=, main=)` |
| `LumaDetailsRow` (+ `add_action`, `::activated`), `LumaDetailsItem`, `LumaAddRow`, `LumaFactRow` (`copy()`), `LumaDetailsPhotos` (`::photo-activated`) | `DetailsRow`, `DetailsItem`, `AddRow`, `FactRow`, `DetailsPhotos` |
| **Tables** `LumaTableHeader`: `add_column(key, label, expand, width, end)`, `set_sort(key, LumaSortDirection)`, `activate_column()`, `align(row)`, `::sort-changed(key, direction)`; `luma_table_header_for_column_view(view) → LumaColumnViewHeader` (for a `GtkColumnView`) | `TableHeader([Column(...)], sort=, on_sort=)`, `TableHeader.for_column_view(view)` |
| `luma_selection_apply(list)`, `luma_selection_mark(row, on)`, `luma_selection_trail(row, on)` | `Selection.apply/mark/trail` |
| **Action center** `LumaBarItem`: `new_action(icon, label, action)`, `new_chip()`, `new_prompt()`, `new_context()`, `new_search(placeholder)`, `new_modes(mode_switch)`, `new_separator()`, `new_spacer()`, `set_primary/danger/active/sensitive/tooltip()` | `BarAction`, `BarChip`, `BarPrompt`, `BarContext`, `BarSearch`, a hosted `ModeSwitch`, `SEPARATOR`, `SPACER` |
| `LumaActionEditor`: `new(title, icon)`, `set_summary()`, `add_mode()`, `add_field()`, `add_tool()`, `set_body()`, `set_primary()`, `get_draft()`, `clear()`, `::mode-changed`, `::discarded` | `ActionEditor(title, icon, summary=, modes=, fields=, tools=, body=, primary=, draft=)` |
| `LumaActionCenter`: `new(editor)`, `attach(region)`, `show_bar(items, n, context)`, `show_split()`, `grow()`, `fold()`, `discard()`, `hide_bar()`, `:state` + `::state-changed` | `ActionCenter(editor).attach(host)`, `show_bar(items, context=)`, … |
| `LumaSelectionBubble`: `new(view, items, n, label)`, `show_for(rect)`, `hide()`, `set_active(icon, on)` | `SelectionBubble(view, items)` |
| **Menus** `LumaFloatingMenu`: `add_item(label, icon, gicon, note, action, selected)`, `add_heading()`, `add_separator()`, `popup(anchor)`; `luma_menu_drawer_present_model(where, model, title)` | `action_bubble.FloatingMenu`, `action_bubble.MenuItem`, `MenuDrawer.present_model/present_items` |
| **Trail** `LumaNavigationTrailBar`: `new(with_title)`, `set_place(back_to, title)`, `add_meta(text, action)`, `::back` | `NavigationTrailBar(trail, meta=, title=)` |
| **Files** `LumaFileCard`: `new(file, compact)`, `set_kind/size/subtitle/tone/thumbnail/extra()`, `add_action(item)`, `::open`; `LumaOpenButton`: `new(file, type, small)`; `LumaOpenInMenu`: `new(file, type)`, `popup(anchor)`, `::app-chosen`; `luma_file_split_name()` | `FileCard`, `OpenButton`, `OpenInMenu`, `split_name` |
| **Cards** `LumaCard`, `LumaContentLitCard`: `new(child, padded)`; `LumaAccountCard`: `new(name, caption)`; `LumaPersonAvatar`: `new(name, size)`, `set_picture()`; `LumaContentLitHeader`: `set_source(picture, tone, name)` | `Card`, `ContentLitCard`, `AccountCard`, `PersonAvatar`, `ContentLitHeader` |
| **People and places** `LumaPerson`, `LumaContactActions` (`::action` → handled), `LumaContactCard`, `LumaEventCard` (`::add`), `LumaSongCard` (`::play`), `LumaPlaceCard` (`::directions`), `luma_contact_uri()` | `Person`, `ContactActions`, `ContactCard`, `EventCard`, `SongCard`, `PlaceCard`, `content_contact.contact_uri` |
| `LumaTextField`: `new(label, purpose)`, `set_hint()`, `::changed`, `::activate`; `LumaParagraphField`; `LumaHeroTitleField` (a `GtkEntry`): `::committed` | `TextField`, `ParagraphField`, `HeroTitleField` |
| `LumaPlaceSearch`: `set_places(model)` or `::search` + `set_results()`, `set_exclude(GtkFilter)`, `::picked`; `LumaPlaceResult`; `luma_parse_coordinates()` | `PlaceSearch(provider, on_pick=, exclude=)`, `PlaceResult`, `rank_places`, `content_place.parse_coordinates` |
| `LumaMessageBubble`: `new(text, mine)`, `new_with_child()`, `set_joins()`, `set_selected()` | `MessageBubble(text, mine=, joined_above=, joined_below=, selected=)` |
| **Media** `LumaMediaTransportLcd`: `new()`, `set_lcd(bar, time)`, `set_readout("tempo" \| "signature" \| "key", value)`, `set_playing()`, `set_recording()`, `set_looping()`; signals `play-changed(bool)`, `record-changed(bool)`, `loop-changed(bool)`, `seek-start`, `readout-activated(key)` | `MediaTransport("lcd", lcd=…, readouts=…, on_play=…, on_record=…, on_loop=…)` |
| **Creative** `LumaCreativeWorkspace`: `new(content)`, `set_left/right(FloatingPanel)`, `set_tools(widget)`, `set_zoom(widget)`, `set_camera(zoom,x,y)`, `set_grid_visible()`, `set_focused()`; `LumaFloatingPanel`: `new(title, icon)`, `set_child()` or `add_page(key,label,child)`, `set_page()`, `set_title_menu()`, `set_title_editable()`, `set_more_menu()`, `set_summary()`, `set_folded()`, signals `page-changed(key)`, `renamed(name)` | `CreativeWorkspace(content, left=, right=, tools=, zoom=, focused=)`, `FloatingPanel(title, icon, child= or pages=)` |
| **Media** `LumaTimeline`: `new()`, `begin_update()` / `clear()` / `end_update()`, `set_frame_rate(num, den)`, `set_duration_frames()`, `append_lane()`, `set_lane_state()`, `append_region()`, `append_marker()`, `set_loop_frames()`, `set_position_frames()`, `set_selected_region()`, `set_tool()`, `set_snapping()`, `set_zoom()`, `set_export_status()`; signals in `luma-timeline.h` | `Timeline(lanes, duration, fps, position, selected, tool, snapping, zoom, callbacks)` |

The gtk-doc comments in each header are the reference. A Python name written
with a module prefix (`content_cards.initials`) is not exported from the
`luma_appkit` package root, so it is not covered by the
[compatibility promise](lumaui-principles.md#api-level-compatibility-and-deprecation).

`set_frame()` accepts a `LumaNavigationSidebar` (or `NULL`) and a `LumaIsland`,
and places them in the window body with the 8px gutter. The sidebar’s named
variants use the same row styles and width tokens as Python.

The native LCD transport keeps the app's playback state and values separate
from its controls. Presses update the visible toggle immediately and emit a
signal; authoritative player updates call the setters. At a window width of
600 px or less, LumaUI keeps start, play, record and the LCD, and hides the
three auxiliary readouts and loop key. Its CSS comes from the shared media
sheet and is reloaded with the active treatment.

The native timeline accepts integer frame counts at a rational frame rate.
Seek, skim, move, trim and split signals report seconds; apps convert those
intent values into their own commands/undo and then replace the affected data.
For a whole arrangement, use `begin_update`, `clear`, the frame/lane/region
setters, then `end_update` so it paints once. `append_note` and
`set_region_waveform` accept the app's MIDI frames and normalized audio peaks;
LumaUI does no media read or cache work. `set_position_frames` moves only
the playhead, and meter-only `set_lane_state` updates only the meter. LumaUI
owns the 52 px ruler, 64 px lanes, clip and track controls, loop/marker chrome,
and 214 px desktop / 116 px phone headers. Controls remain present on phone.

## Where C differs from Python

- **Refusals come when a part is shown.** C parts are filled in after they
  are made, so "at least two modes", "at least one control", "pairs or trios"
  are checked (as criticals) when the part is first shown.
- **Buttons are always built.** C can't know at build time whether a signal
  will be connected, so Add, Directions and Play on the mini cards are always
  there (`luma_event_card_set_add_label(card, NULL)` hides Add).
- **Menus.** The sidebar foot's filter menu is a `LumaFloatingMenu` with the
  counts as row notes; the corner pill's More is a `GtkPopoverMenu` from the
  `GMenuModel` on a computer and the menu drawer at phone width. Menu rows run
  their `GAction` on the control the menu opened from. An open floating menu
  holds itself; it goes when it closes.
- **SidebarFoot** emits `search-changed` after the typing pause (180 ms).
- **Not in C yet:** SidebarToggle's `closed=` callback, DetailsItem's `when=`,
  the editable FactRow, and `BarEntry`.

## Parity

Luma’s platform tests build each part twice, from `LumaUI` (C, through GObject
introspection) and from `luma_appkit` (Python), with the same content. They walk
both widget trees and compare every node’s CSS name, classes and visibility:
fresh, then in a 1000 px window, then at 400 px. A part whose trees differ fails
the platform’s tests, so the C and Python parts look the same in your app.

## Building against LumaUI-1

The headers, `pkg-config` files and GIR data come in the
`luma-developer-platform-devel` package. Inside Flatpak, the
`org.projectluma.Sdk//44` runtime provides them. Build with Meson:

```meson
luma_ui = dependency('luma-ui-1', version: '>= 0.1.0')
```

`luma-ui-1` brings in GTK 4, libadwaita and `luma-semantics-1` through its
`Requires`. See
[your first app in C](../getting-started/first-app-c.md) for a complete example.
