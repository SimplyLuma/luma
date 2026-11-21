# ADR-052: One application model for every package format

- **Status:** Accepted
- **Date:** 2026-11-20
- **Owner:** Project Luma
- **Review basis:** the installer, launcher and install-command sources on main; Flatpak, OSTree, snap, AppImage, Steam Linux Runtime, Landlock, SELinux and systemd documentation as of November 2026; three independent adversarial reviews (application compatibility, mechanisms, history and outlook)
- **Revisit trigger:** a format the model cannot hold, or a kernel or systemd mechanism that replaces one named here
- **Extends:** ADR-050 (universal packages). **Replaces for applications:** ADR-038 (`dnf install` adds packages to the system image)

## User problem

Linux has one stable interface, the kernel, and no stable userland. Every package format is a different answer to that, and each brings its own install location, update path, sandbox and permission rules. On Luma today the format decides how an application behaves:

| Arrives as | Today | Weakness |
| --- | --- | --- |
| Flatpak (Depot, Flathub) | Flatpak runtime and sandbox | Permissions are whatever the packager declared |
| `.deb`, `.rpm` file | One podman image per application | 0.5–2.3 GB each; libraries frozen at install; no update source |
| `sudo dnf install`, `sudo apt install` | Layered into the system image | Full trust; files spread through `/usr` and `/etc`; `apt` names are translated to Fedora's |
| AppImage, archive | Unpacked, run on the host `/usr` in a namespace | Depends on host libraries matching |
| snap | snapd | Confinement is degraded without AppArmor; a second store and daemon |

A person cannot say where an application lives, what it can reach, how it updates or what removing it leaves behind without knowing which of these it was. The sandboxes that exist also leak: a capsule today can read the whole keyring, record through the raw PipeWire socket, drive other windows through the accessibility bus and, on X11, watch every other X11 application.

## Constraints

- A package only runs reliably on the userland it was built against. Copying libraries between userlands fails on version mismatches; merging packages into the host allows one distribution only and no clean removal.
- Commands and habits must keep working and must not surprise: `apt install` gives a Debian or Ubuntu package, never a silent substitute.
- Command-line software composes. A compiler, its headers, a language runtime and its packages must see each other.
- Graphics drivers belong to the device, not to the application's userland: an old userland's Mesa cannot drive new hardware, and NVIDIA's libraries must match the loaded kernel module exactly.
- A prompt that blocks an application hangs its window, and toolkits probe folders at start. Landlock rules never widen; autofs and EROFS cannot be mounted from a user namespace.
- No application may work worse after a change than before it.

## Decision

A package format is a way an application arrives. It is not a way it runs. Everything installed is one of three things, and each has one set of rules.

### 1. Applications

1. **One object.** An application is one read-only, versioned tree, one record, and one data folder per person. The record names its origin, its userland, what it may reach and what it exports. Nothing is written anywhere else, and removal deletes the tree and, by choice, the data.
2. **Its own userland, exactly.** The tree is the application plus the complete set of packages it depends on, resolved and installed by its distribution's own package manager (`apt`, `dnf`, `pacman`, `zypper`) in a build sandbox, from that distribution's signed repositories. Maintainer scripts and triggers run there, once. Luma hosts no base images. The record keeps the resolved package list.
3. **One store.** Trees are commits in an OSTree repository, checked out as read-only hard links, as Flatpak does. Every file is stored once however many applications contain it, and shared in memory. No privileged mount service is needed. composefs can replace the checkout later without changing the model.
4. **One launcher.** Starting an application assembles a namespace with bubblewrap: its tree as the root, its data folder as home, the session's display, sound, portals, input method, fonts and theme. No container engine runs at launch. Nested user namespaces stay allowed, as Chromium's sandbox needs them.
5. **Graphics come from the device.** Every tree gets the device's graphics drivers in place of its own: Luma builds current Mesa for each supported userland and binds the host's NVIDIA libraries, both moving in step with the system image. Importing host drivers the way Steam Linux Runtime does is the fallback for userlands Luma does not build for.
6. **Supported userlands.** Luma Platform and the freedesktop runtimes (as Flatpaks); Debian stable and oldstable; Ubuntu LTS releases in standard support; Fedora current and previous; Enterprise Linux 9 and 10; openSUSE; Arch as a dated snapshot that moves atomically with its applications. 32-bit libraries are part of a userland where its distribution has them. On aarch64, an x86_64-only application runs on an x86_64 userland under FEX and is labelled; a native build is always preferred. Adding a userland is data, not design.
7. **Updates.** Each application is re-resolved against its repositories on a schedule; a changed package set builds a new tree. The new tree is started once in a hidden test, replaces the old one only between runs, and the previous tree is kept for rollback. A repository a package adds is recorded as part of its origin. People can hold an application. Applications that update themselves get a writable versioned layer and keep doing so. When a userland leaves upstream support, its applications are rebuilt on the next release if they resolve and start there; the rest are marked as no longer receiving security fixes.
8. **Add-ons.** A package that extends an installed application (plugins, language packs, codecs for one player) is added to that application's tree. Fonts and system-wide codecs install for the whole device.

### 2. Permissions

One system, enforced by Luma, for every format. A package's own declarations are a request shown at install, not a grant.

1. **Never, for any application:** keys (SSH, GPG), the keyring beyond its own entries, browser profiles, password stores, other applications' data, and the hidden files that run at sign-in. An application has its own private space for hidden files.
2. **Files, at one of three levels,** chosen at install from what the application is and shown on the install sheet: the file chooser only (the default; the chooser reaches anything); named folders (a music player has Music, a photo editor Pictures, an office suite Documents); or all files (file managers, sync and backup clients, anything that declares it). The level is changed in Settings and applies while the application runs.
3. **Nothing is hidden and nothing blocks.** Every folder in the home folder is visible to the application at its real path. One it may not use refuses access with a permission error, never an empty folder. Luma sees the refusal and offers the grant quietly, once per folder; the application is never suspended waiting for an answer.
4. **Documents never hide in application data.** A visible file an application saves at the top of its home belongs to the real home folder. Removal lists any documents left in the data folder before deleting it.
5. **Devices.** Camera, microphone, location, screen capture and USB go through portals. PipeWire access is restricted per client; raw device nodes are absent until granted. A package's own udev rules become a device request shown at install (controllers, lighting, serial, HID).
6. **Between applications.** Open-with, share, drag and drop and browser-to-application native messaging go through a broker, so a password manager and a browser work together without either reading the other's data.
7. **Session.** Each X11 application gets its own X server; Wayland clients are tagged with their identity; the accessibility bus and D-Bus are filtered per application; abstract sockets and signals are scoped. Network is on by default and switchable. Background running and start at sign-in are visible and switchable.
8. **Identity.** Every application runs under its own allocated SELinux category pair and its own systemd scope. Luma's portal identifies it from those, so permissions, the tray, resource use and network use are always attributed to an application.

### 3. Workspaces

Command-line tools, libraries, development packages and language runtimes are not applications. They live in a workspace: one shared, mutable userland per distribution, kept in the same store, with the real home folder and working directory. A workspace cannot change the system and never sees the keyring, browser profiles or application data. Commands it provides are exported to `PATH`. Development applications (editors, IDEs, terminals) run in a workspace so they reach its tools, `~/.ssh` and `~/.gitconfig`. Homebrew and Nix are workspaces with their own prefix. `pip`, `npm -g`, `cargo install` and `go install` write to their usual places in the home folder and are listed.

### 4. System modules

Anything that must change the system is a separate, named class, listed apart from applications and removable in one step:

- kernel modules and drivers: built and signed by Luma, or refused with a plain reason;
- services (VPN clients, container engines, sync daemons that need root): run as portable services on their own userland with declared privileges;
- printer and scanner drivers: Printer Applications.

`dnf install` changes the system image only for these.

### 5. The front door

1. Opening a package file, Depot, and the `apt`, `dnf`, `pacman`, `zypper`, `flatpak`, `snap` and `brew` commands all reach one dispatcher. It decides which of the three classes the request is, and the real package manager does the work on the right userland, so output and scripted use (`apt-get -y`, `dpkg -s`, `rpm -q`) stay genuine. Queries are answered from wherever the package lives.
2. No silent substitution. A command installs from its own ecosystem. When the same application is in Depot the dispatcher says so in one line and continues. When a name is not in the distribution but a vendor publishes it, the dispatcher names the vendor source and asks.
3. Install scripts (`curl | sh`, `.run`) are captured: what they would install becomes an application or workspace change, and steps that need the system become a system-module offer.
4. Origin is recorded and shown for everything. Names that map to vendor sources come from a signed list; publishers can be verified and revoked.

### 6. One list

Settings shows every application, workspace and system module the same way: origin, size, what it may reach, what it has used, updates, and Remove.

## What each format becomes

| Arrives as | Result | Change from today |
| --- | --- | --- |
| Depot and Flathub Flatpak | Stays a Flatpak on disk | `home` and `host` requests become a Luma file level; protected paths are always excluded; nothing is redirected to temporary storage |
| `.deb` with a window, `apt install` of an application | Application on Debian or Ubuntu, chosen from its dependencies | No podman image; no name translation |
| `.rpm` with a window, `dnf install` of an application | Application on Fedora, Enterprise Linux or openSUSE | No system-image layering |
| Command-line and development packages | Workspace | Tools see each other |
| AppImage, archive | Application on the newest userland that satisfies it | No dependence on the host `/usr`; update source from embedded update information where present |
| snap | Resolver prefers the same application from its vendor or Flathub and says so; snapd stays for the rest | Conversion is optional and last |
| Android, Windows | The same contract through their own runtimes | Listed and governed the same way |

## Alternatives rejected

- **Convert everything to Flatpak on one runtime.** Breaks whenever copied libraries disagree with the runtime; the `/app` prefix does not fit packages built for `/usr`.
- **Install into the host.** One distribution only, full trust, no clean removal.
- **Shared base image plus a thin layer per application.** The layer carries a copy of the package database and trigger caches that goes stale when the base changes, and Luma would have to build and host every base. File-level sharing gives the same disk use without either.
- **composefs with a privileged mount service, now.** Not mountable from a user namespace and still marked experimental by its users; OSTree checkouts do the job today.
- **Blocking prompts at first access.** Hang windows at start and train people to allow.
- **A store that rebuilds binaries (Nix), a virtual machine per distribution, one container image per application.** Third-party binaries cannot be rebuilt; virtual machines integrate poorly; images are heavy and never updated. Today's capsules remain as the fallback.

## Tradeoffs

- A first application from a distribution downloads its minimal userland (roughly 80–200 MB); later ones share it. Phones show the cost before installing and collect unused files.
- Luma owns current graphics drivers for each supported userland, the build sandbox, the dispatcher, the broker and the SELinux policy. It does not own distribution security fixes; it owns delivering them and saying when they stop.
- An all-files application can read a person's documents. It cannot read keys, sessions or other applications' data, cannot persist at sign-in and cannot change the system.
- Workspaces are trusted with the home folder, as a developer's shell always has been. They are the place to watch as software agents install tools on a person's behalf.
- Snap conversion without snapd means implementing its interfaces and hooks. It may never be worth doing.

## Order of work

1. Close the leaks in today's launcher (keyring, PipeWire, accessibility bus, X11, category allocation). Security refresh, launch test and rollback for the capsules that exist. The single list.
2. The store, per-application userlands, the launcher and device graphics; move existing capsules across.
3. Permissions for every format: levels, refusal and quiet offer, devices, the broker, portal identity.
4. Workspaces and the dispatcher behind every command; install-script capture.
5. System modules.
6. More userlands, x86_64 on aarch64, snap conversion.

Each step ships only when the application matrix passes on both architectures: install, start, update, roll back and remove for every format, on Intel, AMD, NVIDIA and the ARM devices, with a check that removal leaves no files. An application that fails the new path stays on the old one.
