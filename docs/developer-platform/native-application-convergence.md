# Native application convergence

Luma presents one operating-system design language without pretending that
every application has the same implementation. The ownership boundary is the
application toolkit, not a login script or a stylesheet copied into a user's
home directory.

## Coverage tiers

### Luma applications

Luma-owned applications use Luma AppKit. AppKit owns their responsive
composition, title row, work islands, commands, focus behavior, materials,
active/backdrop elevation, and desktop-to-handheld adaptation. A Luma
application must not locally redraw a shared AppKit component.

### Curated system applications

Applications selected as part of the operating system use native packages
built against Luma's host toolkit whenever exact toolkit convergence matters.
The shared GTK 4, libadwaita, GTK 3 theme, Prairie icons, Figtree metrics,
window-control order, appearance settings, and portals give these applications
one native frame and material vocabulary while retaining their upstream
behavior.

The desktop composer installs the roles in
`config/shared/application-packages.txt`. A system Flatpak may be replaced by
its native package only through the explicit allowlist in
`config/desktop/system-flatpak-replacements.txt`. This is a product-integration
choice, not a rejection of Flatpak.

### Installed third-party applications

Third-party Flatpak applications receive standard appearance, font, icon,
window-layout, and portal settings where their runtime supports them. Their
runtime and application remain responsible for internal widgets. Qt,
Electron, games, and applications with custom client-side chrome keep their
own supported presentation.

Luma does not inject `GTK_THEME`, mount host libraries over a sandbox runtime,
write user CSS, mutate widgets after launch, or wrap an application merely to
imitate AppKit. Those techniques are brittle, can break accessibility, and
misrepresent the application's security boundary.

## Structural limit

Work islands are application structure, not decoration. GTK 3, GTK 4, and
libadwaita mark an ordinary application window and its top-level content at the
real toolkit boundary, so the system can safely provide one polished native
work surface without naming or patching the application. A top-level
`AdwToolbarView` keeps the application's real command bars while its content
receives that surface.

The toolkit cannot infer whether children inside that surface represent a
sidebar, document, inspector, canvas, or intentionally edge-to-edge region, so
it does not try. A window that composes its own islands says so by carrying
`luma-app-window`: the toolkit then dresses the frame, the identity and the
window controls exactly as it does for Filer and Calculator, and leaves the
content to the Application Kit's islands, which carry `luma-island`. That is
the contract every native Luma application is written against; see
`application-kit.md`. `luma-no-native-surfaces` remains only for a renderer
that owns every pixel of its window, and is never selected from an
application-name list.

## Acceptance

- Active and inactive Luma windows have distinct, stable elevation without a
  false divider below the title row.
- Native GTK 4/libadwaita applications use the shared 42px header geometry,
  Figtree, Prairie symbols, right-side connected controls, Luma material, and
  one application identity derived from their real application metadata and
  action model.
- GTK 3 applications use the packaged Luma theme, the same system settings,
  the same single native application-identity contract, and the toolkit-owned
  native window/work-surface classes.
- Ordinary GTK 4 and libadwaita application windows receive the shared Luma
  frame and one top-level work surface without any per-application allowlist,
  launcher wrapper, or widget mutation after launch.
- Explicit AppKit identity widgets suppress the compatibility identity, and
  `luma-no-automatic-identity` is the documented source-level escape class for
  applications whose title structure cannot safely host one.
- Toolkit identities follow the application-window lifecycle: header bars
  created before a builder assigns `GtkWindow:application` receive the same
  identity when that property becomes available. Applications do not need a
  launch wrapper, delayed mutation, or per-application compatibility patch.
- Toolkit identity applies to any real `GtkWindow` registered with a
  `GtkApplication`; using the narrower `GtkApplicationWindow` subclass is not
  required. Transient dialogs and custom-rendered chrome remain outside this
  compatibility treatment.
- The bundled Text Editor is a native package using the host toolkit; its
  Fedora system Flatpak is absent from a composed Luma image.
- User-installed Flatpaks are never removed or globally overridden.
- No result is described as full AppKit conformance unless its real source
  owns the responsive and semantic component structure.
