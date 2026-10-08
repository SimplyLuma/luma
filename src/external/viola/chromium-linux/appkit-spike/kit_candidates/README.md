# AppKit component handback candidates

These components are prototypes for shared Luma AppKit adoption, as requested
in the Luma handoff. They are not accepted system components and do not change
the installed platform. Native menu surfaces, type, colors, focus and elevation
remain inherited from Luma.

`inline_zoom.css` supplies geometry for `NativeZoomRow`: a 34px row, 28×26px
icon buttons, and a 44×26px reset/value button with tabular figures. It removes
generic button minimums/padding only inside `.luma-inline-zoom`. Without that
scope, the installed generic button styling inflates the value to 71px and its
height to 30px. Original Chromium commands and current zoom values remain the
source of behavior; repeated adjustments retain the same native popover while
refreshing the whole retained command model.

The intended upstream component should accept value, enabled states and
callbacks instead of Chromium menu descriptions. The current adapter validates
those descriptions and supplies them to the composition. Before adoption,
qualify keyboard focus, assistive technology, translated/wide values, RTL,
reduced motion and all Luma appearances. The private compositor check measures
actual allocations and verifies repeated zoom and Escape dismissal.

The September 14 revision explicitly applies the installed `luma_menu_ink`
and `luma_menu_raised` tokens and menu typography to the composed zoom row.
`menu_interaction.css` is a second candidate: pointer navigation suppresses
focus-only highlights while keyboard navigation retains the kit's focus
styling. Both require private compositor verification before adoption.

The retained-menu adapter also re-presents each popup once after mapping.
GTK defers section-separator visibility until an idle; the first allocation
can omit their margins, leaving the last command clipped below a separator.
The private test checks the scroller's content extent against its page size
and inspects a compositor capture, rather than trusting `get_mapped()` alone.
