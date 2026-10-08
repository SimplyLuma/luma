#!/bin/sh
# lumaui-conform image: the capture stack as it ships. The Luma packages come at exactly the
# nightly's pinned NEVRAs (/tmp/rpms, from its packages.txt); everything from Fedora at the
# candidate build's versions (ship-rpmqa.txt, its packages-installed.tsv). thinkpad-rpmqa.txt only names the package set
# (which fonts, which typelib owners), not versions. Runs inside `podman build`.
set -eu
export LC_ALL=C; tp=/tmp/tp.sorted; sort /tmp/thinkpad-rpmqa.txt > $tp
# The ThinkPad has every language; the capture runs in en_US.UTF-8, which reads en_US and en.
echo "%_install_langs en:en_US" > /etc/rpm/macros.image-language-conf
dnf -y -q install --setopt=install_weak_deps=False fedora-repos-archive
want="mutter mutter-common dbus-broker dbus-daemon systemd python3 python3-gobject python3-gobject-base
 mesa-dri-drivers mesa-vulkan-drivers mesa-libEGL mesa-libgbm vulkan-loader tzdata
 adwaita-icon-theme hicolor-icon-theme librsvg2 glycin-loaders glycin-libs glycin-gtk4-libs bubblewrap
 gsettings-desktop-schemas procps-ng util-linux rpm fontconfig
 gstreamer1-plugin-gtk4 evolution-data-server libcamera-gstreamer
 nss nspr atk at-spi2-atk at-spi2-core cups-libs libdrm libxkbcommon libXcomposite libXdamage libXfixes libXrandr
 alsa-lib libX11 libxcb libXext expat dbus-libs rsync tar findutils
 accountsservice-libs appstream atk at-spi2-core colord-libs evince-libs evolution-data-server flatpak-libs gcr3 gcr-libs
 gdk-pixbuf2 geoclue2-libs geocode-glib glib2 gnome-autoar gnome-bluetooth-libs gnome-desktop3 gnome-desktop4 gnome-menus
 gnome-online-accounts-libs gobject-introspection graphene gsound gspell gssdp gst-editing-services gstreamer1
 gstreamer1-plugins-bad-free-libs gstreamer1-plugins-base gstreamer1-rtsp-server gtksourceview4 gtksourceview5 gupnp gupnp-av
 gupnp-dlna gupnp-igd harfbuzz javascriptcoregtk4.1 javascriptcoregtk6.0 json-glib libappindicator-gtk3 libblockdev
 libcloudproviders libdbusmenu libdbusmenu-gtk3 libfprint libgee libgexiv2 libgsf libgtop2 libgudev libgusb libgweather libgxps
 libical libical-glib libmanette libmbim libmediaart libmodulemd libnice libnma libnma-gtk4 libnotify libosinfo libportal
 libportal-gtk4 libproxy libqmi libqrtr-glib libreofficekit librsvg2 libsecret libshumate libsoup3 libspelling libtinysparql
 libudisks2 libwnck3 libxmlb malcontent-libs malcontent-ui-libs ModemManager-glib msgraph NetworkManager-libnm ostree-libs
 PackageKit-glib pango passim polkit-libs poppler-glib rest rpm-ostree-libs rygel totem-pl-parser upower-libs vte291 vte291-gtk4
 webkit2gtk4.1 webkitgtk6.0 wireplumber-libs
 gstreamer1-plugins-good gstreamer1-plugins-good-gtk gstreamer1-plugins-bad-free gstreamer1-plugins-bad-free-extras
 gstreamer1-plugins-ugly-free gstreamer1-plugin-libav gstreamer1-plugin-dav1d gstreamer1-plugin-openh264 pipewire-gstreamer
 ffmpeg-free libavcodec-free glycin-thumbnailer"
# (Every typelib the ThinkPad has, by owning package, so any app's gi imports resolve; its codecs, minus Qt.)
# Not on the ThinkPad: Node for the spec side (Playwright); dbus-daemon, as dbus-broker-launch
# needs a journal, and a container has none (the bus does not touch rendering).
extra="nodejs npm dbus-daemon"
fonts=$(awk '$1 ~ /fonts?$/ && $1 !~ /^(fonts-srpm-macros|ghostscript-tools-fontutils|libfontenc|libXfont2)$/ {print $1}' "$tp")
local_names=$(for f in /tmp/rpms/*.rpm; do rpm -qp --nosignature --qf '%{NAME}\n' "$f"; done)
names=$(for n in $want $fonts; do echo "$local_names" | grep -qx "$n" && continue; awk -v n="$n" '$1 == n {print $1}' "$tp"; done)
dnf -y -q upgrade --setopt=install_weak_deps=False
dnf -y -q install --setopt=install_weak_deps=False /tmp/rpms/*.rpm $names glibc-langpack-en $extra
# Converge every package the candidate OS also has onto its version (ship-rpmqa.txt).
sort /tmp/ship-rpmqa.txt > /tmp/ship.sorted
for pass in 1 2 3; do
  diff=$(rpm -qa --qf '%{NAME} %{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' | sort | join - /tmp/ship.sorted | awk '$2 != $3 {print $3}')
  [ -z "$diff" ] && break
  echo "pass $pass: $(echo $diff | wc -w) packages to pin"
  dnf -y -q install --allowerasing --setopt=install_weak_deps=False --enablerepo=updates-archive $diff || \
    for q in $diff; do dnf -y -q install --allowerasing --setopt=install_weak_deps=False --enablerepo=updates-archive "$q" || echo "cannot pin $q"; done
done
# Nothing may have replaced a shipped Luma package.
for f in /tmp/rpms/*.rpm; do rpm -q "$(rpm -qp --nosignature --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}' "$f")" >/dev/null || { echo "not the shipped NEVRA: $f" >&2; exit 3; }; done
# The base image was installed with en_US only; put the en translations back.
dnf -y -q reinstall --skip-unavailable $(rpm -qa --qf "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n" | grep -v ^gpg-pubkey) >/dev/null
mkdir -p /usr/share/lumaui-conform
rpm -qa --qf '%{NAME} %{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' | sort > /usr/share/lumaui-conform/rpmqa.txt
for f in /tmp/rpms/*.rpm; do rpm -qp --nosignature --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' "$f"; done | sort > /usr/share/lumaui-conform/shipped-luma.txt
join /usr/share/lumaui-conform/rpmqa.txt /tmp/ship.sorted | awk '$2 != $3 {print "differs " $2 " (ships " $3 ")"}' > /usr/share/lumaui-conform/parity.txt
echo "packages: $(wc -l < /usr/share/lumaui-conform/rpmqa.txt), shipped Luma NEVRAs: $(wc -l < /usr/share/lumaui-conform/shipped-luma.txt), differing from the candidate: $(grep -c ^differs /usr/share/lumaui-conform/parity.txt || true)"
