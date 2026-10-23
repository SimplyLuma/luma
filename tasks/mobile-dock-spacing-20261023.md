# Approved mobile dock proportions

- Owner: Codex.
- Status: approved candidate; draft integration pending simulator shell baseline.
- Scope: five equal dock slots, Search tile shape, fallback browser apparent size.

The candidate is isolated in 0044-luma-uniform-dock-layout.patch. Its required
v71 Mobile simulator baseline is newer than public main; see
`docs/developer-platform/mobile-dock-integration.md` for the prerequisite,
pre-patch file hashes and builder instructions. No prerequisite shell changes,
clock changes or battery/network changes are bundled here. The patch is not wired
into main's older package recipe until its baseline is available.

Validation: applied with zero fuzz to hash-verified prerequisite files; UI/SVG XML
parses. Native GTK 3 layout checks pass at 360, 427 and 768 logical pixels: center
gaps differ by at most 1.5px, vertical centers align, and the dock stays 88px high.
The approved portrait simulator preview remains active. Complete shell builds,
fewer favorite items, rotation and physical-device qualification remain pending.
