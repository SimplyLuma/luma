# Dock entrance oracle (Shell patch 0135)

Reproduces dock icons stuck a few pixels wide: all dock items are re-added
(their entrance eases from scale 0) and a monitor configuration is applied
during the ease, as happens at login. `run.sh` starts a headless Shell in a
Fedora container with the built RPMs; `eval.js` applies Nick's layout
(`R_LAYOUT=nick`: 3440x1440 @1 primary, 1440x2560 @1, 2400x1500 @1.25) or one
display (`R_LAYOUT=single`, `R_SCALE`), with `R_STRESS=monitors` logs every
item's final scale (1 is correct) and crops the dock. `R_OVERLAY` points at a
`js/ui` overlay to test a change before packaging.
