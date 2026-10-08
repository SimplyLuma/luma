# Luma OS icons — Chamfer family

Approved reference: 0001 Folder, option B (folder-b.svg). Preserve that file as the material and construction master. User selected B on 2026-09-05. 0007 B revision 2 is approved; 0005 reuses 0001 unchanged.

## Reusable design brief
Create an intentional sibling of folder-b.svg. Reuse its exact SVG gradient definitions, color stops, rim strokes, shadow, light direction, tab geometry, and base seam. Change only geometry required to communicate the new meaning. Do not reinterpret the material or add decoration. Use standalone SVG paths and gradients with a transparent canvas; no raster, scripts, external resources, fonts, or filters.

## Construction rules (256 × 256 viewBox)
- Canvas: width/height 256, transparent. Closed folder body x=39–217, top=46, baseline=210. Keep the same baseline and optical center for related icons.
- Open-state exception: front-panel lip may expand symmetrically to x=22–234 to express opening; preserve rear body and tab exactly. Shadow remains inside x=31–229 and y=202–226. Never auto-scale individual subjects to fill the canvas.
- Corners: rear top radii approximately 9–10 units, base 8; front corner transitions approximately 8. Open lip joins use equivalent short quadratic curves under tilt. No oversized pill rounding.
- Tab: plateau ends at x=96, diagonal shoulder passes through (106,50) to (119,63). Reuse master path for folder variants.
- Face gradient: object-bounding-box (0,0) → (.35,1); #a4d4e9 at 0, #62a9cd at .48, #357ca5 at 1.
- Rear gradient: (0,0) → (0,1); #c4e5f2 → #357ca5.
- Light overlay: radial center (.28,.23), radius .86; white at .23 opacity fading to zero. Upper-left illumination, no alternate glossy spots.
- Rim: vertical white .65 → white .1 at .45 → #153b62 .3; rear 1-unit stroke, front 1.2-unit stroke. Rear underlying outline #357ca5 at .7 units. All strokes scale with viewBox.
- Pocket shadow: #1a526e at .15 opacity; front silhouette translated up 1.5 units. An exposed interior may use the same color/opacity.
- Ground shadow: ellipse (130,214), radii (99,12), radial #1b3044 .25 → transparent. No black hard outline or baked background.
- Base seam: y=201, #235977 at .16; highlight y=203, white at .24; 1-unit strokes. Inset endpoints from panel edge by approximately 5 units.
- No texture noise, blur filters, extra stripes, badges, or perspective rotation. Add semantic details only when the icon requires them.
- Review at 256, 64, 32, and 16 pixels on the same page. Keep optical corrections explicit if later requested. Native desktop/librsvg rendering remains a production verification gate; XML validation is not that gate.

## Review workflow
Keep approved originals and alternatives. Label choices on OS Icons. Reference these rules before every future icon. Exact shared gradient definitions are also stored in chamfer-defs.svg for reuse; each deliverable embeds them so it has no runtime dependency.

## Open-folder correction from user review
Front and rear panels must share their full bottom hinge: side endpoints (39,202)/(217,202), bottom corner curves, and baseline from (47,210) to (209,210). Never inset the front base within the rear. The open lip may expand, but both panels connect at the bottom. Use the same face material and light overlay inside; no contrasting interior inset or dark pocket overlay. Applied to 0007 B revision 2. This supersedes the earlier interior-shadow allowance for open folders.

## Inset semantic marks (approved 0003 revision 3)
Use original Lucide geometry, rounded caps/joins, in a 64-unit box centered at (128,146). Keep the folder base unchanged. The mark is a shallow groove in the same blue material, not dark ink. Supersedes the previous navy/high-opacity outline.
- Coordinate transform: translate(96 114) scale(2.666667).
- Lower reflected edge: shift down 1 canvas unit; stroke #c4e5f2 at .4 opacity, width 1.9 in Lucide coordinates.
- Groove edge: stroke #357ca5 (the folder's own darkest face stop) at .55 opacity, width 1.9.
- Groove center: shift down .4 canvas units; width 1.35; vertical user-space gradient from #478fb6 at y=2 to #3c86ad at y=22, opacity .8.
- This layered vector treatment provides a quiet bevel without blur filters, black/navy ink, a badge, or a new material. Apply the same construction to other semantic folder marks as approved. Preserve original Lucide paths and license notices.

## Multi-path inset opacity
For symbols whose Lucide paths touch or overlap, apply opacity to the complete layer group, not stroke-opacity inherited by individual paths. Composite the union once to prevent darker joints. Recycling bin B revision 2 uses this correction.

## Shadow footprint correction
Keep shared shadow color, falloff and baseline, but scale its ellipse to the actual object footprint. Phone options use rx=57, ry=8 for a 98-unit-wide handset; do not reuse the 198-unit-wide folder shadow on narrow objects.

## Paper shadow — flat on surface
Blank and text files use a centered, zero-offset contour shadow around the entire paper silhouette, not a bottom ground ellipse. Four vector layers: #1b3044, stroke widths 12/9/6/3 at opacities .012/.018/.025/.035, rounded joins. Shadow sits behind the opaque sheet. Preserve this treatment for future paper-based file types.

Fold shadows follow the flap contact edge (vertical, rounded elbow, horizontal), layered behind the flap and clipped to the paper silhouette. Do not use a rectangular or polygonal gradient patch below the fold.

Paper edge legibility: use a continuous final #8c9da6 outline at .8 opacity, 1.2 units, above light overlays. Top paper stop #f4f6f7. Do not apply the bright folder rim over the paper outline; it erases the silhouette on white.
