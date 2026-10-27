# October 27 publisher control

Owner: luma-dev-2 / root. Based on frozen artifact source eccd6bb20. Publisher-only reapplication of the already verified collection-metadata guard from 017c68724. Artifact source remains eccd6bb20; no image changes or publication yet. Preserves all foreign architecture/channel and vendor refs while allowing the generated ostree-metadata ref to advance.

October 27 qualification control: the actual x86 inventory also contains 28
Fedora Wine/ntsync/Mono dependency rows resolved by the existing unpinned recipe.
The separate publisher policy accepts only build 20261027.4/source eccd6bb20,
the exact authenticated delta receipt d687e4fa0a59cf15c71ce50b5b76df5bc536d4c3cd8ed87971148dda6ffafcc3,
identical old/new inventory rows, and an actual candidate-bound fresh-prefix/PE
console smoke. Other package changes still require full qualification. ARM's
actual full inventory contains only the original five changes.

Normal job 3d01e229 passed 13 synthetic admission controls, including rejection
of new kernel changes, future builds, altered inventories, changed evidence,
failed/stale functional checks and missing checks. These are validator tests;
they do not claim that the pending guest Wine smoke or OS qualification passed.
