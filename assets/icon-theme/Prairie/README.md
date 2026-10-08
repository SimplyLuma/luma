# Prairie icon theme

Prairie is the shared desktop/handheld application theme, inheriting Adwaita and
hicolor. Application identities and binaries remain unchanged.

`artwork/icon-family-r40` retains the 34 SVGs selected in the owner's 27
September 2026 icon-family review. `selection-manifest.json` records the page
revision and selected variant of each icon. `artwork/icon-family-r41` derives
the displayed copies with a centered 1.12 scale, giving the dock icons the
same apparent size as their neighboring apps. Run
`python3 assets/icon-theme/tools/size-icon-family.py --check` to verify that
derivation. `application-manifest.json` records the displayed copies' SHA-256
digests and lookup aliases. Unselected artwork remains in
`artwork/v3`. Grid, Write, Stage, Valet, Depot, Maps, Viewer, Canvas, Tide and
Relay resolve the same artwork in Filer, the dock and application headers.
Uninstalled catalog entries receive named theme resources, not new launchers.
Monitor is distinct from System Report and has its own selected icon.

Run `python3 assets/icon-theme/tools/sync-application-icons.py` from the
repository to regenerate aliases. Its `--check` mode rejects stale source or
output. The generator preserves vector geometry, authored gradients and depth.
For the older unwrapped v3 sources it clips the complete composition at the
shared `icon.application_corner_ratio` token (0.25). The selected family has
its own inset tile and corner clip, so `precomposed` entries are copied without
a second clip. Runtime App Kit treatments remain owned by the shared
application-icon component.

An application that bundles its own hicolor identity lists that path under
`bundled` in `application-manifest.json`. The eight bundled paths retain the
approved revision 40 geometry for App Kit, while Prairie's dock aliases use
the larger revision 41 treatment. The generator verifies both source hashes
and keeps each output in sync.

Identities without a supplied replacement retain their existing artwork.
Navigation symbolics retain the reviewed icon21 GTK
foreground-stroke geometry, source hashes and ISC/MIT notices in
`upstream/lucide/navigation-manifest.json` and `LUCIDE_REFERENCE_LICENSE.txt`.
The media transport row (`media-playlist-shuffle`, `media-skip-backward`,
`media-playback-start`, `media-playback-pause`, `media-skip-forward`,
`media-playlist-repeat`, plus the two `-symbolic-rtl` skip mirrors Adwaita 50
also ships) came from Lucide's `shuffle`, `skip-back`, `play`, `pause`,
`skip-forward` and `repeat` on 15 September, under those standard names so
theme lookup finds them before Adwaita. Their exact Lucide sources are vendored
in `upstream/lucide/transport/` and digested in the same manifest, so the ISC
geometry a glyph claims is checkable in-tree rather than only against a path on
one machine. An RTL mirror takes the opposite upstream file, as the chevrons do,
never a transform.
Messages' glyphs (attachment, send, call, video, microphone, compose, search,
refresh, favourite, warning, ok, next, phone, new message, and the person,
group and business avatar glyphs) came from Lucide on 16 September under their
standard names, vendored in `upstream/lucide/messages/` and digested the same
way; `luma-business-symbolic` is building-2 from lucide-react 1.31.0, which the
supplied snapshot lacks. `tools/adapt-lucide-symbolic.py` performs the
adaptation and reproduces the transport row byte for byte.
Standard names that Luma code uses and Adwaita 50 no longer ships
(`cursor-default`, `draw-freehand`, `edit-rename`, `emblem-default`,
`emblem-shared`, `emblem-synchronizing`, `notifications`,
`utilities-system-monitor`, `application-x-apk`, all `-symbolic`) came from
Lucide 1.39.0 on 18 September. Unlike the stroke-class glyphs above they are
fills-only: `tools/outline-symbolic.py` outlines each stroke with round caps
and joins, merges one nonzero path and scales it to the 16px grid in GNOME's
symbolic ink, so GTK 4, the shell and GTK 3 recolor them alike. Their exact
sources and the upstream LICENSE are in `upstream/lucide/fills/`, digested in
the navigation manifest. The OS image build fails when first-party code names
an icon that no installed theme, resource or app data provides
(`scripts/os/lib/icon_references.py`).
LumaUI's glyphs (25 September) are every Lucide name the LumaUI spec,
luma-next-70, draws, plus the few the kit itself needs. Each is vendored
verbatim in `upstream/lucide/lumaui/` with the supplied ISC licence and
adapted by `tools/adapt-lucide-symbolic.py` at the mockup's 1.6 stroke to
`symbolic/actions/lumaui-<lucide-name>-symbolic.svg`. The `lumaui-` names are
stable and never reused, and they stay apart from standard names, so adding
one never restyles a third-party application. Share is `share-2`, Export is
`share`, Upload is `upload`. `tools/import-lumaui-icons.py` adds names and
digests them in `upstream/lucide/lumaui-manifest.json`; its `--check` mode
rebuilds every glyph from its source and fails on drift.
The six Weather condition glyphs (cloud-rain, cloud-sun, cloud-moon,
cloud-fog, cloud-lightning, snowflake) are exact `lucide-static` 1.48.0 SVGs,
with per-icon pinned source URLs and hashes in that manifest. The ISC/MIT
license is retained in `upstream/lucide/lumaui/LICENSE.txt`; the Prairie SRPM
includes the manifest and vendored originals alongside adapted symbolics.
They use GTK's symbolic recoloring rather than a baked system accent. Window
controls and status icons retain their separate native 16px fill-only contract.
Trash retains its independent Places and Applications aliases.

Luma-owned application artwork, including the owner's selected icon family,
is CC-BY-SA-4.0. The owner confirmed distribution rights for revision 40 on
2026-09-28. Attributed Lucide geometry retains ISC/MIT notices. Theme metadata, generator, framing, status glyphs and
packaging are Apache-2.0. The current browser tile is Luma's authored globe;
no Mozilla artwork enters the package. Historical upstream reference files and
prior credit remain in repository history.

The canonical package script validates source hashes, aliases, symbolic assets
and SVG XML, builds an identical noarch package for both presentation modes,
and checks the extracted RPM. Adding artwork requires updating the manifest;
adding a launcher remains the owning application's responsibility.

## Approved system artwork (release 23 candidate)

The 51 regular names in `system-manifest.json` map to 41 exact approved SVGs
from the 7 September system-artwork snapshot. `system-integration.json` lists
additional lookup aliases separately. Places, MimeTypes and Devices share the
same noarch theme across desktop and handheld. Regular artwork retains its
authored padding, gradients, masks and vector references; symbolics and
application artwork remain separate. The existing full Trash design is kept.

`tools/validate-system-icons.py` verifies approval metadata, source and payload
hashes, self-contained vector references, and context membership; its negative
tests reject altered bytes and active/external content. The package ships the
frozen manifest and `SYSTEM_ARTWORK_NOTICE.md`; original source snapshots and
license evidence enter the SRPM. Read that notice for the unresolved artwork
redistribution gate. The package has no new service or runtime dependency.

Build and release evidence: `docs/changes/2026-09-07-approved-system-icons.md`.
