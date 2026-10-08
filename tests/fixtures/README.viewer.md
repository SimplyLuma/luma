# Viewer fixture sources

Offsite and Terrace: copied byte for byte from repository commit 5a9f886c,
`tests/fixtures/darkroom-v70/life-hero-studio.webp` and
`tests/fixtures/darkroom-v70/life-sync-terrace.webp`, respectively. SHA-256:
`88aa33940488fbe27da899ac0176e93cb0163481fc79de4bee35fd2b373de717` and
`1f33ade3672636a9414ffc2a786e55c623cf7de7e7b73ef7ba63c1ff2cca72d7`.

Share-sheet face crops: copied from tracked tests/fixtures/phone-v70/face-{PR,NF,TH,SK,AR}.jpg in repository commit 57d6d7c0 (Phone v70 fixture), with the names/usernames/hues from v70 PPL. No portrait files were copied from LumaDesign.

Receipt, Frame and Lease are generated in memory by `luma_viewer.fixture`
from the approved Viewer sample content.

Comparison fixture `viewer-v70/v6.png` is the unmodified native Filer grid
capture `conform/filer/20260926-104521-dark/gtk-grid.png`; `v7.png` is the
unmodified native Information capture `gtk-information.png` in that same run.
Both are1180×740 PNGs rendered from Filer's synthetic fixture by the normal
private headless conform route (Mutter50.4, GTK4.22.4, NautilusWindow), not
browser/spec screenshots. The capture JSONs have matching state names and
native widget/window records. Source revision is not recorded in that returned
folder; these exact output bytes are identified by SHA256:

- v6: `8ece3129c9a6444056e048ec5e127f87b21c05d1e57dab01007a021da6a5e7c9`
- v7: `6d866f9fb47db49f672744335966341dfce2b2bad1bd2813da328ecaacb11108`

This source route was explicitly authorized by the coordinator in
`viewer-05-fixture-artwork.md`: use Luma's own native Filer fixture captures,
with provenance. The old reference-only WebP files are preserved outside the
source checkout and are never committed. Logical sample names, metadata,
comparison modes and actions remain v70's; only the sample image bytes change.
The browser scenario needs the same approved two-image asset mapping before
comparison pixel parity can pass; no reference HTML or thresholds are changed.
