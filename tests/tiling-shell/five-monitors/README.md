# Five-monitor drag coverage

Use the disposable native Shell container contract documented in the
Applications drawer/status-polish runners, with the real Tiling Shell extension
installed and enabled. Run `run-native.sh` at `LUMA_FIVE_SCALE=1` and `1.25`.

Four 1280×720 outputs form two stacks flanking a 2560×1440 centre output. The
centre is primary; the 125% case mixes scaled and unscaled outputs. Real pointer
drags cross from every side and rapidly re-enter the centre. Checks verify the
active grab, visible selection, full column height and actual dropped window.
Screenshots preserve the overlay without pressing Ctrl.

`LUMA_FIVE_LAYOUT=wide` instead uses four 1920×1080 outputs flanking a
5120×1440 centre output, vertically centred between the taller side stacks.
This exercises unequal heights, a primary output with a nonzero vertical
origin, and the same mixed-scale crossings. It is a representative diagnostic
layout, not a claim about the reporter’s exact monitor configuration.

The same-point re-entry case reproduced a persistent zero-height preview before
patch 0019: leave the centre, then return to the same pointer position. Closing
the preview retained the previous cursor/activation state, so the unchanged
position skipped rebuilding it. Resetting that state on monitor exit restores
the preview. All 32 checks pass for each of the four layout/scale combinations
(128 total). This verifies that trigger; other physical configurations remain
outside this fixture's coverage.
