---
title: Windows, title rows and islands
summary: Design guidance for the Luma window, the identity and title, the sidebar, islands, the details pane, bars, empty states and dialogs.
order: 2
section: Design
---

# Windows, title rows and islands

This page is about design decisions. For the code, see
[windows and islands in LumaUI](../kit/windows-and-islands.md).

## The window

A Luma window is a calm ground with islands set into it. LumaUI draws the
frame: 20px corners (square only in full screen) and a soft layered shadow. An
inactive window is drawn exactly like the active one, so a window in the
background stays readable.

Choose a sensible default size for your main task and a minimum that still
works. The SDK’s app template uses a minimum of 360 by 294 so the app can run
on a phone.

## The title row

The title row is 46px tall and belongs to the system.

- **Identity, left.** Your app’s icon and name, taken from your desktop entry
  so they match the dock. It opens your identity menu; put app-wide commands
  there.
- **Window title.** What this window holds: the document, conversation or
  folder. The shell, the window switcher and screen readers read it; the title
  row does not show it. Leave it as the app’s name when the window has a
  single obvious purpose.
- **Window controls, right.** Minimize, maximize and close, in the order the
  desktop sets. On a phone they are hidden; the window closes from its window
  menu and the keyboard.

The title row has slots for a few controls that act on the whole window, such
as the sidebar toggle (`AppWindow.set_leading()`, `set_title_content()` and
`set_trailing()`). Controls that act on one island belong in that island.

## The sidebar and islands

Think of each island as a table in a workshop: one kind of work per table.
Islands have 12px corners and sit 8px apart and 8px from the window edge. The
navigation sidebar is not an island: it stands on the window frame, beside the
content island.

| Region | Width | Contains |
| --- | --- | --- |
| Navigation sidebar | 272px; 236px when the window is under 900px wide | Destinations, sections, counts, a pinned search or account footer |
| Content island | Flexible | The document, list, canvas or media, with its toolbar and status bar |
| Details pane | 288px | Properties of the current selection, in sections |
| Command bar | Full width, compact height | Playback controls, a document command bar |

`NavigationSidebar` also takes named widths (`narrow` 236px, `regular` 272px,
`wide` 292px). The gutter between islands is part of the design; never close it
or add to it.

### Toolbars and status bars

- A **toolbar** sits at the top of the island it controls. Group related
  controls; use a connected button group for mutually exclusive options.
  Align the most common action to the trailing edge.
- A **status bar** sits at the bottom of a content island for totals, ranges,
  zoom, capacity and sync or save state, in quiet small text.

### Sidebars

- Use short nouns for destinations: “All notes”, “Albums”, “Downloads”.
- Group destinations under section labels only when there are more than about
  six.
- Show live counts (unread, missed) as the row’s trailing value. Update them in
  place.
- Pin search above the list and account or connection state below it.

### The details pane

`DetailsPane` is the one inspector for every app. It sits on the trailing side
of the content island, is closed until people ask for it, and closes on Escape
or when its subject goes away. At phone width it becomes a drawer from the
bottom edge.

- Title each section with a noun.
- When a selection has mixed values, say “Mixed” rather than showing one of
  them.
- When nothing is selected, show a compact empty state explaining what to
  select, not a blank panel.

## Empty states

Every pane that can be empty needs a designed empty state.

- **A whole pane:** use `EmptyState`: a glyph in a soft disc, a short title,
  one sentence, and at most two buttons that run real commands. Keep one
  primary action.
- **A list inside a pane:** use `ListEmptyState`, a single quiet line such as
  “No results for ‘harbor’”.
- Say what goes here and how to add it: “No notes yet” and “Notes you create
  appear here.” Avoid jokes and blame.

## Dialogs and sheets

- **Alert dialogs** confirm consequential or destructive actions and report
  problems that need a decision. Title with a question or a statement
  (“Delete 3 notes?”), explain the consequence in one sentence, and label buttons
  with verbs: “Cancel” and “Delete”. Mark destructive responses as destructive.
  `DestructiveDialog` follows this pattern.
- **Sheets and dialogs** hold short, focused tasks. On a phone, LumaUI moves
  its dialogs, menus and the details pane to the bottom of the screen.
- **Preferences** use the standard preferences dialog, grouped by topic.
- Avoid dialogs for information that can be shown inline, and never stack
  dialogs.

## Welcome surfaces

Creative and document apps may open to a welcome surface with the app’s icon,
a one-line description, **New** and **Open** actions, and a check to skip it
next time. LumaUI’s `WelcomeView` draws this. Other apps should open straight
to their content.
