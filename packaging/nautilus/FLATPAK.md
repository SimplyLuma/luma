# Filer application delivery

Filer's app producer rebuilds the admitted Fedora Nautilus 50.2.2 source with
the complete maintained patch series and `/app` install paths. It does not copy
the native RPM executable, and desktop, tablet and handheld use one binary.
The native RPM remains the system activation gateway: on an OS with the signed
application role it resolves and executes the verified system Flatpak before
GTK or profile initialization. A removed, invalid or ambiguous app never falls
back to an older native Filer. Installer 58 owns the fixed resolver protocol.

The signed application owns its original `org.gnome.Nautilus` identity,
FileManager1 and upstream Nautilus file-chooser portal implementation together.
Its static host-filesystem access is intentional for a general file manager;
Depot must show that actual high-level grant. GVfs and LocalSearch remain host
services. The app neither adds arbitrary host execution nor owns system disks
or mounting privileges. Existing dconf settings and the explicitly shared
GTK/Nautilus bookmark configuration survive independent updates and rollback.
GTK portals continue to mediate requests from other sandboxed applications.

The dependencies absent from the Luma SDK follow Nautilus's own distributed
Flatpak recipe: inih r62, Exiv2 0.28.3, gexiv2 0.16.0, gnome-desktop 44.5,
gnome-autoar 0.4.5 and libportal 0.9.1. Exact official archive URLs and SHA256
digests are part of the app recipe. All are built with `/app` paths; runtime
libraries already provided by the signed SDK are not replaced. Upstream
Nautilus GPL-3.0-or-later and extension LGPL-2.1-or-later, Exiv2 GPL-2.0-or-later,
gexiv2 GPL-2.0-or-later, autoar LGPL-2.1-or-later, and gnome-desktop GPL-2.0-or-later/LGPL-2.0-or-later,
libportal LGPL-3.0-or-later and inih BSD-3-Clause notices must remain in source
and installed license payloads. Lucide ISC notices remain unchanged.

Required acceptance includes native package checks; actual `/app` launch at
360/500/1024/1440 widths; filesystem operations and cancellation; default and
custom profiles; Applications discovery and refresh; Open/Save via another app;
MIME, FileManager1 and launcher ownership; signed install/update/rollback;
independent removal without native fallback; and preserved file/bookmark data.
The recipe and resolver implementation do not themselves close those gates.

The app SDK build uses upstream headless test selection and executes the two filename checks. A source patch defers localsearch discovery until an enabled Tracker test uses it; it does not disable production search. The canonical native producer executes all seven maintained GTK/filename checks. Signed installed app GTK, native FileManager1/file chooser, file operations and update/rollback remain separate mandatory gates.

The actual unsigned app launch caught an additional dependency: Nautilus reads
the host LocalSearch miner's GSettings schema at startup even when the host miner
is unavailable. The app therefore builds the exact upstream 3.11.2 miner schema
from its pinned full official source archive, using upstream's default HOME/empty
index-directory defaults. It includes all upstream license notices and compiles
the installed schema strictly. The miner executable, index database and service
remain native; no second background indexer is launched in the application.
The missing-schema crash evidence is retained, and the fixed actual app launch
must pass before screenshot media or lifecycle admission.
