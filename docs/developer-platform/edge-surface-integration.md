# Edge-to-edge shell surface continuity

The mobile shell must leave edge-to-edge application backgrounds unchanged
behind its status and gesture bars. Patch0045 removes the status scrim and
suppresses overview paint only when both Home and its drag state are folded.
It retains overview allocation and the gesture target during native transitions.

This patch targets the Mobile20261022-r10 shell after downstream patches through
0043. That baseline is not yet integrated into public main. The patch is
intentionally unwired in main’s older package recipe; integrate the prerequisite
baseline before enabling it. Dock, status icon and application changes are
independent and excluded.

Pre-patch SHA256 values for the prerequisite source:

- src/stylesheet/common.css: 14fdbfbe924a3a586329d9028e6206157b855a4b93e8060d7a8672f3f96be8fc
- src/home.c: 06bfbf1478b51978b8d57301068e5c1ad860370bf499b3f419abd9a1f63da9c3

Apply with zero fuzz, wire the patch into the owning shell recipe and builder,
then rebuild and verify folded/unfolded/drag states, keyboard dismissal, Home
navigation, deep/immersive surfaces and status text contrast. Update composition
pins only after package qualification. A GTK property-binding preview and patch
replay do not qualify the native C change or a physical device.
