# Luma browser identity artwork

`org.projectluma.Viola.NativeIntegration.svg` and the nine PNGs in
`viola-icon/` are the existing pointer-and-rings Viola artwork supplied in the
October 4 icon handoff. They are copied unchanged from the qualified native
application source. The SVG retains the supplied viewBox; no redraw or extra
mask is added. The retained Chromium engine artwork is unchanged.

The native toolkit owns identity placement and treatment. The application does
not select or replace a system icon theme. See `viola-icon/PROVENANCE.md` for the
artwork source and the repository's existing artwork licensing notice.

## Sidebar Lucide symbols
The existing Lucide `square-stack` and `layers` geometry is reused under its
ISC license (see `LUCIDE-LICENSE`). Only GTK symbolic stroke-class metadata
is added, following the installed Luma theme’s symbolic asset convention.
No replacement icon theme is selected.
- Source: https://github.com/lucide-icons/lucide/blob/main/icons/square-stack.svg
  downloaded 2026-09-13; original SHA-256 `9f7550f0db1a934166d8c253613c43151554d69bcacaf36883f817b880f037fe`.
- Source: https://github.com/lucide-icons/lucide/blob/main/icons/layers.svg
  downloaded 2026-09-13; original SHA-256 `ce16d0a955e3581cda17f6b2fd5c5bc2034b405f9cecc31c8ebb8495b4afc1bb`.

`icons/viola-chevron-right-symbolic.svg` is an unchanged copy of the installed
Luma Prairie asset `/usr/share/icons/Prairie/symbolic/actions/luma-chevron-right-symbolic.svg`
captured 2026-09-13. It retains the asset’s Lucide ISC notice
and native symbolic metadata. The application-scoped name also resolves when
GTK cannot reach the desktop settings portal in isolated compositor QA.
