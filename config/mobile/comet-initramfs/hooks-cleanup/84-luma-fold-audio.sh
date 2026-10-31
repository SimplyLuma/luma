#!/bin/sh
# Fold bring-up: speaker audio through the AOC. Its firmware and the CS35L41
# amplifiers' ship with the rest in /luma-fold/firmware (hook 85). This
# installs what is not firmware under /var/lib/luma-fold (/usr is immutable):
# the UCM profile for the aoc-audio card and the diagnostics' test tone, a
# UCM tree for WirePlumber (ALSA_CONFIG_UCM2) of links to /usr/share/alsa/ucm2
# plus conf.d/aoc-audio, a WirePlumber rule for the card's sinks, and a user
# service that applies callaudiod's speaker/earpiece choice to the amplifiers
# and holds the telephony endpoint open in call mode (one card profile, so
# PipeWire never swaps the sink; see luma-fold-callaudio.py).
[ -d /sysroot/var ] && [ -f /luma-fold/audio/MANIFEST ] || exit 0
a=/sysroot/var/lib/luma-fold/audio
mkdir -p /sysroot/var/lib/luma-fold
if ! cmp -s /luma-fold/audio/MANIFEST $a/MANIFEST; then
	rm -rf $a.new
	cp -a /luma-fold/audio $a.new && rm -rf $a && mv $a.new $a
fi
u=/sysroot/var/lib/luma-fold/ucm2
rm -rf $u
mkdir -p $u/conf.d
for e in /sysroot/usr/share/alsa/ucm2/*; do
	n=${e##*/}
	[ "$n" = conf.d ] || ln -s /usr/share/alsa/ucm2/$n $u/$n
done
for e in /sysroot/usr/share/alsa/ucm2/conf.d/*; do
	ln -s /usr/share/alsa/ucm2/conf.d/${e##*/} $u/conf.d/${e##*/}
done
ln -s /var/lib/luma-fold/audio/ucm2/conf.d/aoc-audio $u/conf.d/aoc-audio
mkdir -p /sysroot/etc/environment.d
printf "ALSA_CONFIG_UCM2=/var/lib/luma-fold/ucm2\n" > /sysroot/etc/environment.d/84-luma-fold-audio.conf
# The AOC drains its ring in chunks: give the sink a fixed period and some
# headroom, and keep it open when idle rather than suspending it. No Pro Audio
# profile: building it probes every PCM of the card, and opening the telephony
# endpoint (hw:N,4) switches the AOC's DSP to telephony and starts the call mic;
# the card is driven through UCM only, and luma-fold-callaudio owns hw:N,4.
mkdir -p /sysroot/etc/wireplumber/wireplumber.conf.d
cat > /sysroot/etc/wireplumber/wireplumber.conf.d/52-luma-fold-aoc-audio.conf <<"WP"
monitor.alsa.rules = [
  {
    matches = [ { device.name = "alsa_card.platform-aoc-sound" } ]
    actions = {
      update-props = {
        api.acp.disable-pro-audio = true
      }
    }
  }
  {
    matches = [ { node.name = "~alsa_output.platform-aoc-sound.*" } ]
    actions = {
      update-props = {
        api.alsa.period-size = 1024
        api.alsa.headroom = 1024
        session.suspend-timeout-seconds = 0
      }
    }
  }
]
WP
# callaudiod's choices as card profiles, for every user session
s=/sysroot/etc/systemd/user
mkdir -p $s/default.target.wants
cat > $s/luma-fold-callaudio.service <<"UNIT"
[Unit]
Description=Fold bring-up: callaudiod speaker, earpiece and call route
After=pipewire.service wireplumber.service
[Service]
ExecStart=/usr/bin/python3 /var/lib/luma-fold/audio/bin/luma-fold-callaudio.py monitor
Restart=on-failure
RestartSec=3
[Install]
WantedBy=default.target
UNIT
ln -sf ../luma-fold-callaudio.service $s/default.target.wants/luma-fold-callaudio.service
