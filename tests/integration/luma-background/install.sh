#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
#
# Install luma-background, the kit, the Semantic Broker, two sample kit apps,
# a Flatpak-style app and the test fakes into a disposable Fedora 44 container
# with a real systemd user session (user "luma", uid 1000, lingering).
# Run as root from the source tree. Never run this on a real machine.
set -euo pipefail

[ -f /run/.containerenv ] || { echo "refusing to run outside a container" >&2; exit 1; }
S=${1:-/home/luma/src}
I=$S/tests/integration/luma-background
B=$S/src/luma-background
P=$S/src/luma-platform
PY=$(python3 -c 'import sysconfig; print(sysconfig.get_path("purelib", vars={"base": "/usr"}))')
install -d "$PY"

rm -rf "$PY/luma_appkit" "$PY/luma_semantic_broker"
cp -r "$P/appkit/luma_appkit" "$P/broker/luma_semantic_broker" "$PY/"
if [ -n "${BGA_RPM:-}" ]; then
  # The packaged service, exactly as built.
  rpm -q luma-background >/dev/null 2>&1 && rpm -e luma-background
  rm -rf "$PY/luma_background"
  dnf5 -y -q install "$BGA_RPM"
  rpm -q luma-background
else
  rm -rf "$PY/luma_background"
  cp -r "$B/luma_background" "$PY/"
  install -m 0755 "$B/bin/luma-background-service" /usr/libexec/luma-background-service
  install -m 0755 "$B/bin/luma-background" /usr/bin/luma-background
  install -m 0644 "$B/data/luma-background.service" "$B"/data/*.slice /usr/lib/systemd/user/
  install -m 0644 "$B/data/org.projectluma.Background1.service" \
    "$B/data/org.freedesktop.impl.portal.desktop.luma.background.service" /usr/share/dbus-1/services/
  install -D -m 0644 "$B/data/luma-background.portal" /usr/share/xdg-desktop-portal/portals/luma-background.portal
  install -D -m 0644 "$B/data/defaults.toml" /usr/share/luma-background/defaults.toml
  install -D -m 0644 "$B/data/org.projectluma.Background.desktop" /usr/share/applications/org.projectluma.Background.desktop
fi
find "$PY/luma_appkit" "$PY/luma_semantic_broker" -name __pycache__ -prune -exec rm -rf {} +

install -m 0755 "$P/broker/bin/luma-semantic-broker" /usr/libexec/luma-semantic-broker
install -D -m 0644 "$P/broker/org.projectluma.SemanticBroker1.xml" /usr/share/dbus-1/interfaces/org.projectluma.SemanticBroker1.xml
install -m 0644 "$P/broker/data/luma-semantic-broker.service" /usr/lib/systemd/user/
install -m 0644 "$P/broker/data/org.projectluma.SemanticBroker1.service" /usr/share/dbus-1/services/
# The broker's mount-namespace hardening needs user namespaces a rootless
# container's user manager does not have; the identity checks are unaffected.
install -d /etc/systemd/user/luma-semantic-broker.service.d
cat >/etc/systemd/user/luma-semantic-broker.service.d/50-container.conf <<'EOF'
[Service]
PrivateDevices=no
PrivateTmp=no
ProtectClock=no
ProtectControlGroups=no
ProtectHome=no
ProtectKernelLogs=no
ProtectKernelModules=no
ProtectKernelTunables=no
ProtectSystem=no
RestrictNamespaces=no
MemoryDenyWriteExecute=no
# In a rootless container the kernel's uid_map describes the container, not
# the one-UID namespace the broker's ownership check translates for, so every
# root-owned desktop entry would read as foreign. Undo only that translation.
ExecStart=
ExecStart=/usr/bin/python3 -c "import luma_semantic_broker.desktop as d; d._visible_owner_uid = lambda uid, **kw: uid; from luma_semantic_broker.service import main; raise SystemExit(main())"
EOF

install -m 0644 "$I/fixtures/bga_sample.py" "$PY/bga_sample.py"
install -d /usr/share/luma/background /etc/luma-background
for spec in Counter:widget-data Chatter:communication; do
  name=${spec%%:*}; category=${spec#*:}; lower=$(printf '%s' "$name" | tr 'A-Z' 'a-z')
  printf '#!/usr/bin/python3\nimport sys\nfrom bga_sample import main\nsys.exit(main("org.example.%s"))\n' "$name" >"/usr/bin/luma-$lower"
  chmod 0755 "/usr/bin/luma-$lower"
  cat >"/usr/share/applications/org.example.$name.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$name
Icon=org.example.$name
Exec=luma-$lower
EOF
  cat >"/usr/share/luma/background/org.example.$name.toml" <<EOF
[application]
id = "org.example.$name"
name = "$name"

[background]
agent = "org.example.$name.Agent"
exec = "luma-$lower --agent"
category = "$category"
wake = ["login", "network", "resume", "schedule"]
publishes = ["count", "fraction", "wakes", "live-extension:org.example.$name.Count"]
EOF
done
cat >/etc/luma-background/defaults.toml <<'EOF'
[apps]
"org.example.Chatter" = "on"
EOF
# Chatter's own fallback for sessions without luma-background: an autostart
# entry naming its agent. With the service it must stay masked.
install -d /etc/xdg/autostart
cat >/etc/xdg/autostart/org.example.Chatter.Agent.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Chatter
Exec=/usr/bin/touch /tmp/bga-chatter-fallback-ran
NoDisplay=true
X-Luma-Background-Agent=org.example.Chatter.Agent
EOF

# A Flatpak-style app: its desktop entry is exported the way Flatpak exports
# it, and "flatpak" records how it was asked to run the sandboxed command.
install -d /var/lib/flatpak/exports/share/applications
for name in Flatchat Flatquiet; do
  lower=$(printf '%s' "$name" | tr 'A-Z' 'a-z')
  cat >"/var/lib/flatpak/exports/share/applications/org.example.$name.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$name
Icon=org.example.$name
Exec=/usr/bin/flatpak run --branch=stable --arch=x86_64 --command=$lower org.example.$name
X-Flatpak=org.example.$name
EOF
done
cat >/usr/bin/flatpak <<'EOF'
#!/bin/bash
# Test stand-in for flatpak: record the invocation, then behave like a
# long-running sandboxed program.
mkdir -p "$XDG_RUNTIME_DIR/bga-control"
printf '%s\n' "$*" >>"$XDG_RUNTIME_DIR/bga-control/flatpak-argv"
exec sleep infinity
EOF
chmod 0755 /usr/bin/flatpak

# The stand-in Shell is the Python interpreter under the Shell's executable
# name, so /proc/<pid>/exe names /usr/bin/gnome-shell as the checks require.
cp "$(readlink -f /usr/bin/python3)" /usr/bin/gnome-shell

install -d -o luma -g luma /home/luma/.config/systemd/user/default.target.wants \
  /home/luma/.config/systemd/user/luma-background.service.d
cat >/home/luma/.config/systemd/user/bga-system-bus.service <<EOF
[Service]
Type=exec
ExecStart=/usr/bin/dbus-daemon --session --nofork --nopidfile --address=unix:path=%t/bga-system-bus
EOF
cat >/home/luma/.config/systemd/user/bga-fake-system.service <<EOF
[Unit]
Requires=bga-system-bus.service
After=bga-system-bus.service
[Service]
ExecStartPre=/usr/bin/sleep 1
ExecStart=/usr/bin/python3 $I/fakes/fake_system.py unix:path=%t/bga-system-bus
EOF
cat >/home/luma/.config/systemd/user/bga-fake-notifications.service <<EOF
[Service]
Type=dbus
BusName=org.freedesktop.Notifications
ExecStart=/usr/bin/python3 $I/fakes/fake_notifications.py
EOF
cat >/home/luma/.config/systemd/user/luma-background.service.d/50-test.conf <<'EOF'
[Unit]
Wants=bga-fake-system.service bga-fake-notifications.service
After=bga-fake-system.service bga-fake-notifications.service
[Service]
ExecStartPre=/usr/bin/sleep 2
Environment=DBUS_SYSTEM_BUS_ADDRESS=unix:path=%t/bga-system-bus
Environment=XDG_DATA_DIRS=/usr/local/share:/usr/share:/var/lib/flatpak/exports/share
EOF
# No graphical session in a container: start the service with the user
# manager itself, which is what a login does here.
ln -sfn /usr/lib/systemd/user/luma-background.service \
  /home/luma/.config/systemd/user/default.target.wants/luma-background.service
chown -R luma:luma /home/luma/.config
echo "installed"
