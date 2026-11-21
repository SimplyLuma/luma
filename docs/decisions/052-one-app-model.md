# ADR-052: One application model for every package format

- **Status:** Accepted
- **Date:** 2026-11-20
- **Owner:** Project Luma
- **Review basis:** the installer, launcher and install-command sources on main; Flatpak, snap, AppImage, composefs, Landlock, fanotify, idmapped-mount and systemd documentation as of November 2026
- **Revisit trigger:** a format the model cannot hold, or a kernel or systemd mechanism that replaces one named here
- **Extends:** ADR-050 (universal packages). **Replaces for applications:** ADR-038 (`dnf install` adds packages to the system image)

## User problem

Linux has one stable interface, the kernel, and no stable userland. Every package format is a different answer to that, and each brings its own install location, update path, sandbox and permission rules. On Luma today the format decides how an application behaves:

| Arrives as | Today | Weakness |
| --- | --- | --- |
| Flatpak (Depot, Flathub) | Flatpak runtime and sandbox | Permissions are whatever the packager declared; many ask for the whole home |
| `.deb`, `.rpm` file | One podman image per application | 0.5–2.3 GB each; libraries frozen at install; no update source |
| `sudo dnf install`, `sudo apt install` | Layered into the system image | Full trust; files spread through `/usr` and `/etc`; a different outcome from the same package opened as a file |
| AppImage, archive | Unpacked, run on the host `/usr` in a namespace | Depends on host libraries matching |
| snap | snapd | Confinement is degraded without AppArmor; a second store and daemon |

A person cannot say where an application lives, what it can reach, how it updates or what removing it leaves behind without knowing which of these it was.

## Constraints

- A package only runs reliably on the userland it was built against. Copying libraries between userlands (converting a `.deb` into a Flatpak on another runtime) fails on version mismatches; merging packages into the host makes foreign distributions impossible and removal unclean.
- Commands and habits must keep working: opening a package file, `apt install`, `dnf install`, `flatpak install`, `snap install`. The same request gives the same result whichever way it is made.
- Landlock rules never widen, and fanotify permission events cover open and read only. A permission granted while an application runs has to be a mount added to its namespace.
- composefs mounts need a privileged helper and its integrity checks are ours to enforce.
- No application may work worse after a change than before it.

## Decision

A package format is a way an application arrives. It is not a way it runs. Every application becomes the same object, governed by the same rules.

1. **One object.** An application is one read-only, versioned folder under `/apps/<id>` plus one data folder per person. Its record names its origin, its base, what it may reach and the commands it exports. Nothing is written anywhere else. Removing it deletes those two folders, with a choice to keep the data, and leaves nothing.
2. **Bases.** An application runs on the userland it was built for: Luma Platform, the freedesktop runtimes, Debian, Ubuntu, Fedora and Enterprise Linux releases, the snap cores. Bases are read-only, shared by every application that uses them, and downloaded on first use. The application folder holds only what its base lacks.
3. **One store.** Bases and applications live in one content-addressed store with file-level sharing (composefs over EROFS with fs-verity), mounted by a small privileged service. A file present in ten applications is stored and cached once.
4. **One launcher.** Starting an application assembles a namespace: its base as `/usr`, its own layer, its data folder as home, the session's display, sound, portals and accessibility. No container engine runs. The AppImage launcher on main is this shape already; it gains a chosen base in place of the host `/usr`.
5. **One permission system, enforced by the OS.** It applies to every format, Flatpak included; a package's own declarations are a request, not a grant.
   - Private by construction: an application never sees other applications' data, the rest of the home folder, keys or browser profiles.
   - Expected access is on with no prompt. The file chooser reaches anything. Personal folders follow what the application is: a music player has Music, a photo editor Pictures, an office suite Documents, everything Downloads.
   - Unexpected access asks once, at the moment it happens. The folder is absent from the namespace behind a blocking trigger; the first reach prompts, and the answer mounts it or leaves it absent. A failed prompt service means no access.
   - Camera, microphone, location, screen and USB go through brokers and portals; raw device nodes are absent until granted.
   - Network is on by default and switchable per application. Background running and start at sign-in are visible and switchable.
   - Every application runs under its own SELinux category and in its own systemd scope, so the system always knows which application a process, a connection or a file access belongs to.
6. **One list.** Settings shows every application the same way: where it came from, its size, what it can reach, what it has used, its updates, and Remove.
7. **One front door.** Opening a package file and the `apt`, `dnf`, `flatpak`, `snap` and `brew` commands all pass through one resolver. It maps the name to an application and takes the best source in this order: Depot, the vendor's own build, Flathub, then the distribution package on its own base. The commands keep their familiar output.
8. **Tools.** Command-line tools install the same way, with their commands exported to `PATH`. They work on the real home folder and working directory, shown as full file access, and still cannot change the system.
9. **System extensions.** Drivers, kernel modules, daemons and anything else that has to change the system are a separate, named class: signed system extension images, listed apart from applications, removable in one step. `dnf install` layers into the system image only for these.
10. **Updates.** Luma updates the bases centrally. Applications update from their origin; a vendor repository a package adds is tracked. Every update is a new version folder, started once in a hidden test, with the previous version kept for rollback.
11. **Android and Windows applications** follow the same contract through their own bases.

## What each format becomes

| Arrives as | Base | Change from today |
| --- | --- | --- |
| Depot and Flathub Flatpak | Its runtime | Stays a Flatpak on disk; permissions come from the Luma system |
| `.deb`, `apt install` | Debian or Ubuntu release chosen from its dependencies | Thin layer on a shared base; no podman image; no system-image layering |
| `.rpm`, `dnf install` | Fedora or Enterprise Linux release | Same |
| AppImage, archive | The oldest base that satisfies its libraries | No longer depends on the host `/usr` |
| snap | Its core, with its content snaps | Resolver prefers the same application from another source; snapd stays until a converter passes the matrix |

## Alternatives rejected

- **Convert everything to Flatpak on one runtime.** Small, but breaks whenever copied libraries disagree with the runtime, and the `/app` prefix does not fit packages built for `/usr`.
- **Install into the host.** Familiar, but one distribution only, full trust, and no clean removal.
- **A store that rebuilds or patches binaries (Nix).** Content addressing is kept; rebuilding third-party binaries is not possible in general.
- **One virtual machine per distribution.** Strong isolation, poor graphics, files and desktop integration.
- **One container image per application (today).** Reliable, and kept as the fallback, but heavy and never updated.

## Tradeoffs

- Bases cost disk: roughly 150–400 MB each, paid once. Most people will hold two or three.
- Luma takes on tracking security updates for every base it offers. A base past its upstream support is marked and its applications say so.
- Applications that expect the whole home folder meet a prompt the first time. This is intended.
- Snap support without snapd means implementing its interfaces, hooks and `snapctl`. It is last, and optional until proven.
- The privileged store service and the prompt service are new trusted components. Both fail closed.

## Order of work

1. Security refresh, launch test and rollback for the capsules that exist now. The single Settings list.
2. The store, bases and the launcher; move existing capsules across.
3. The permission system for every format.
4. The resolver behind every command and file type; tools; system extensions.
5. Snap conversion.

Each step ships only when the application matrix passes on both architectures: install, start, update and remove for every format, with a check that removal leaves no files. An application that fails the new path stays on the old one.
