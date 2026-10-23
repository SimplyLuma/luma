# Home widget bounds integration

Align the Weather and Calendar pair with the artwork centered in the outer
Home grid cells. Derive the gutters from the allocated grid width, column count
and shared icon size. Defer margin updates until after allocation to avoid
losing sibling resize requests. Preserve the 12px card gap and existing text
styles. Lower the minimum content height from 130px to 114px; content can still
require a taller card.

Patch 0049 targets the Mobile shell baseline through 0043, with dock layout 0044
(PR 48). This newer baseline is not integrated into public main. The candidate
is intentionally unwired until the owning package recipe is available. It does
not depend on the status-alignment patch or the dock-surface cleanup.

Validation: zero-fuzz patch replay; compiled native GTK3 preview; layout fixture
at 360, 427, 768 and 427 logical widths with equal card widths within 1px, symmetric
gutters 29/37/80px and an unchanged 12px gap. A rendered Mobile preview was
reviewed for visible card content and artwork alignment. These preview checks
do not qualify the complete source-built shell.

Before promotion, apply with zero fuzz to the recorded baseline, build the
shell, verify repeated Home/app navigation, resize/rotation, empty favorites,
long city/event text and RTL, then update composition pins. Native package and
ARM hardware checks remain pending.

Pre-patch SHA256 values:

- src/app-grid.c: `331af52258d27f4f5dd094a32eeff935ce1cbbeac297ca4497f8ab0467a7df1f`
- src/stylesheet/common.css: `0e64de965dbda61ffbbf102f57503f4d3de185cc747edd6a21678655d9803d46`
