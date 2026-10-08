# Software commands on Luma

Guides for Fedora, Ubuntu and every other Linux say `sudo dnf install htop`,
`sudo apt install firefox` or `flatpak install flathub org.mozilla.firefox`.
Luma routes supported install commands through its application installation
services. The sections below explain each command's ownership and limits;
network availability and package compatibility still determine installation
success.

## dnf and yum

Luma's system is an image (`/usr` is read-only), so dnf5 alone refuses to
change it. `/usr/bin/dnf` and `/usr/bin/yum` lead to Luma's front end, which
keeps dnf5's resolution, table and `Is this ok [y/N]` and makes the change the
way an image-based system can:

| Command | On Luma |
|---|---|
| `install PKG…`, `group install`, `install ./file.rpm` | dnf5 resolves and shows the transaction; on yes, rpm-ostree adds the packages to this computer's image and applies them to the running system (`rpm-ostree install` + `apply-live`). Ready at once; kept through Luma updates and rollbacks; launchers show up in the running session. |
| `install` of a package Luma's image has but this computer removed | Restores it (`rpm-ostree override reset`), live. |
| `install --allowerasing`, `swap` | Replaces image packages with `rpm-ostree override`; takes effect after a restart, and says so first. Never replaces Luma's own packages or the system's foundations. |
| `remove`, `erase` | Added packages: removed live. Packages in Luma's image: removed live where possible and fully after a restart, after saying so; `dnf install` brings them back. |
| `upgrade`, `update`, `distro-sync` | Asks luma-update what is available, shows it, and on yes downloads and prepares it for the next restart. Never restarts. Added packages are updated with Luma's updates. |
| `search`, `info`, `list`, `provides`, `repoquery`, `check-upgrade`, `repolist`, `makecache`, `clean`, `config-manager`, `copr`, `download`, … | dnf5, unchanged. |
| `downgrade`, `offline`, `system-upgrade`, `builddep` | One line saying what to do on Luma instead. |

Options: `-y`/`--assumeyes`, `-n`/`--assumeno`, `-q`, `--refresh` and
repository options work as in dnf. `--enablerepo=X` keeps X enabled (added
packages are updated from it). `--nogpgcheck` is refused: Luma checks
signatures. Changes need `sudo`, exactly as in dnf.

Real dnf5 stays reachable: `dnf5` is dnf5 (on the host it offers its own
`--transient` mode), `LUMA_DNF_PASSTHROUGH=1 dnf …` hands any command to dnf5
unchanged, and inside `toolbox enter` or distrobox containers `dnf` is the
container's own.

## apt and apt-get

`sudo apt install firefox` prints `Luma uses dnf; running: sudo dnf install
firefox` and runs it. install, reinstall, remove, purge, search, show, update
(metadata only), upgrade/full-upgrade (through `dnf upgrade`, with its
question), list `--installed`/`--upgradable`, autoremove and clean are
translated; `-y` and exit code 100 on failure behave like apt. Debian package
names are translated (`build-essential`, `libssl-dev`, the `-dev` → `-devel`
pattern and a table of exceptions); a name Fedora lacks is answered with the
Fedora packages that provide or match it. `.deb` files open in Depot's
installer. There is no dpkg emulation.

## Flathub

`/usr/share/flatpak/remotes.d/flathub.flatpakrepo` is Flathub as an
unfiltered system remote, with Flathub's signing key: `flatpak install
flathub org.mozilla.firefox` and `flatpak install firefox` work for a person
and with sudo, from the first boot, with no network needed to set it up.

## Launchers of live changes

rpm-ostree applies live changes through an overlay on `/usr` that running
sessions do not see. After a live change the front end copies new launchers
and icons to `/run/luma/live-exports/share` (first in every session's
`XDG_DATA_DIRS`, via the `61-luma-live-exports` user environment generator),
hides removed launchers there, and reloads the session and system buses and
systemd when services or units changed. `/run` is empty after a restart, when
the change is part of `/usr`.

## Records

Every change is one journal entry for Luma Vitals:
`journalctl MESSAGE_ID=a08437fa35fc4db9a55f26f08c3e9847`.
Depot's My apps lists added packages under "System tools you installed" and
removes them through `luma-remove-added-package` (polkit
`org.projectluma.install-commands.remove-added`), which refuses anything that
was not added.
