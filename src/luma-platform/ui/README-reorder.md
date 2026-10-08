# Shared reorder feedback

`luma-reorder-hint.h` provides opt-in, non-animated list insertion feedback to
native GTK applications and `LumaUI` GObject-introspection consumers (including
Python). It follows Notes' dimmed-source / boundary-line interaction. Filer is
the first native consumer. No geometry, file operations, ordering data, or
category policy is owned by this helper.

Call `luma_reorder_hint_begin(list, source)` on GtkDragSource's drag-begin.
Validate the target's scope in the application. Resolve the target once, using
`luma_reorder_hint_is_after(row, y)` for a simple vertical half-row split, then
call `luma_reorder_hint_set_target(list, row, after)`. Commit that same resolved
position on drop. Clear the target on leave or invalid motion; call
`luma_reorder_hint_end(list)` on completion/cancellation. Retained row references
are released on clear/end or list disposal. Normal keyboard focus is untouched;
applications retain accessible Move Up/Down commands.

Python uses the same functions through the installed typelib:

```python
LumaUI.reorder_hint_begin(sidebar_list, source_row)
LumaUI.reorder_hint_set_target(sidebar_list, target_row, after)
LumaUI.reorder_hint_clear(sidebar_list)
LumaUI.reorder_hint_end(sidebar_list)
```

The opt-in shared CSS removes misleading hover/selection/drop-container fills,
keeps the source visible, and draws a two-pixel accent boundary. It changes no
padding, margin, height, or hit-testing position. A before/after marker is never
a containment target; applications with nesting can retain their own validated
containment feedback. No app-specific sidebar CSS is necessary for reordering.

The runtime suite checks source visibility, geometry stability, before/after
semantics, marker replacement/cleanup, and preserved keyboard focus in light,
dark, RTL, narrow/wide, large-text and reduced-motion presentations. Native
GTK accessibility roles remain unchanged. Touch uses the same coordinates.
The `luma-ui-reorder-feedback = 1` RPM capability prevents consumers from being
installed against an older runtime lacking the public symbols.
