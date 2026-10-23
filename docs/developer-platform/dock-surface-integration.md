# Mobile dock surface integration

Remove the noninteractive Home indicator and the dock’s clipped outer shadow.
The rounded glass fill and inset edge remain. The expanding spacer preserves
the dock’s position after the indicator is removed. No Home opacity or visibility
changes are included.

Patch0047 targets the mobile shell baseline through0043 with uniform dock
layout0044 (PR48). That prerequisite is not yet integrated into public main.
This candidate remains unwired until the owning shell baseline is available.
Do not add unrelated baseline patches to this change.

Pre-patch SHA256 values:

- src/ui/app-grid.ui: 4cbe24ada5855e225e1c7f51a8bb3deddd38eb1d8b39229c53f5184045f23fd2
- src/stylesheet/common.css: 0e64de965dbda61ffbbf102f57503f4d3de185cc747edd6a21678655d9803d46

Apply with zero fuzz, wire into the owning package recipe and rebuild the shell.
Check Home startup, app/Home navigation, Library, lock/unlock, rotation and fewer
favorites before updating composition pins. XML parsing, patch replay and a
rendered mobile preview passed; native package and hardware coverage are pending.
