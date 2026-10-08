# Homebrew on Luma

Instructions on the web say `brew install something`. On Luma that works the
first time it is typed: Homebrew is part of the system's command set, not a
separate installation step.

Homebrew for Linux only uses prebuilt packages when it lives at
`/home/linuxbrew/.linuxbrew`, and on an image-based system only root can
create that folder. The pieces:

- `luma-homebrew-prefix.service` creates the folder at boot, owned by the
  computer's primary account (the first administrator). Nothing is downloaded.
- `/usr/bin/brew` sets Homebrew up from its official repository the first time
  it runs -- the same steps as Homebrew's installer -- and then hands every
  command to the real `brew`. `sudo brew ...` from a guide runs as the person
  who typed it, because Homebrew refuses to run as root.
- `/etc/profile.d/luma-homebrew.sh` puts what Homebrew installs on the
  search path of every new shell, after the system's own commands so a
  formula never replaces a system tool, and turns Homebrew's analytics off
  unless the person turns them on.

Homebrew's official one-line installer also works unchanged: the folder
already belongs to the person, so it needs no sudo.

Another account on the same computer can use Homebrew once the owner hands
the folder over (`sudo chown -R NAME /home/linuxbrew`); Homebrew itself
supports one owner per installation.
