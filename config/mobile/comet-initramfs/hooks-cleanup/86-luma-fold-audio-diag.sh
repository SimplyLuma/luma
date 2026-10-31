#!/bin/sh
# Fold bring-up: with luma.fold_debug=1, log the audio stack (AOC, amplifiers,
# ALSA, UCM, PipeWire) to the journal once the speakers sink exists and 20 s after the
# tests, and in between play a short tone and record three seconds from the
# microphones. Otherwise remove
# the service.
s=/sysroot/etc/systemd/system
[ -d $s ] || exit 0
if grep -q "luma.fold_debug=1" /proc/cmdline; then
	cat > $s/luma-fold-audio-diag.sh <<"SH"
#!/bin/sh
export XDG_RUNTIME_DIR=/run/user/1000 ALSA_CONFIG_UCM2=/var/lib/luma-fold/ucm2
a=/var/lib/luma-fold/audio
f=/var/lib/luma-fold/firmware
dump() {
	echo "== audio diag $1"
	cat /proc/asound/cards 2>&1
	aplay -l 2>&1
	arecord -l 2>&1
	echo "firmware path: $(cat /sys/module/firmware_class/parameters/path 2>&1)"
	ls -l $f/cirrus $f/google/zumapro 2>&1
	alsaucm -c hw:0 list _verbs 2>&1
	alsaucm -c hw:0 list _devices/HiFi 2>&1
	ls -l /sys/kernel/debug/aoc 2>&1
	head -c 3000 /sys/kernel/debug/aoc/services 2>&1
	amixer -c 0 contents 2>&1 | grep -A2 -E "PCM Source|Digital PCM Volume|Analog PCM Volume|DSP1 Firmware|DSP1 Preload|Mic " | head -100
	wpctl status 2>&1
	wpctl inspect @DEFAULT_AUDIO_SINK@ 2>&1 | head -40
	dmesg | grep -i -E "aoc|cs35l41|cs_dsp|wm_adsp|asoc|spi13|10cd0000|usi13|Direct firmware load" | tail -100
}
sleep 20
n=0
until wpctl status 2>/dev/null | grep -q "Built-in Audio Speakers"; do
	n=$((n + 2)); [ $n -ge 150 ] && break; sleep 2
done
echo "== speakers sink: waited ${n}s"
dump sink
U="runuser -u luma -- env XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"
$U wpctl set-volume @DEFAULT_AUDIO_SINK@ 1.0 2>&1
tone() {
	l=$1; shift
	echo "== tone $l: pw-play as luma $*"
	timeout 10 $U pw-play "$@" $a/tone.wav 2>&1 &
	p=$!
	sleep 1
	cat /proc/asound/card0/pcm0p/sub0/hw_params /proc/asound/card0/pcm0p/sub0/status 2>&1
	wait $p
	echo "tone $l rc=$?"
	dmesg | grep -E "aoc-audio|ring:" | tail -8
	sleep 2
}
tone default
$U wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.5 2>&1
echo "== mic: pw-record 3 s from the default source"
rm -f $a/mic.wav /tmp/luma-fold-mic.wav
timeout -s INT 4 $U pw-record --rate=48000 --channels=2 --format=s16 /tmp/luma-fold-mic.wav 2>&1 &
p=$!
sleep 2
cat /proc/asound/card0/pcm1c/sub0/hw_params /proc/asound/card0/pcm1c/sub0/status 2>&1
wait $p
echo "pw-record rc=$?"
mv /tmp/luma-fold-mic.wav $a/mic.wav 2>&1
ls -l $a/mic.wav 2>&1
python3 - $a/mic.wav <<"PY" 2>&1
import math, struct, sys, wave
with wave.open(sys.argv[1]) as w:
    n, ch = w.getnframes(), w.getnchannels()
    data = w.readframes(n)
s = struct.unpack("<%dh" % (len(data) // 2), data)
for c in range(ch):
    x = s[c::ch]
    peak = max(abs(v) for v in x) if x else 0
    rms = math.sqrt(sum(v * v for v in x) / len(x)) if x else 0
    db = lambda v: 20 * math.log10(v / 32768) if v else -999
    print("mic ch%d: %d frames, peak %.1f dBFS, rms %.1f dBFS" % (c, len(x), db(peak), db(rms)))
PY
sleep 20
dump after
SH
	chmod 755 $s/luma-fold-audio-diag.sh
	printf "[Unit]\nDescription=Fold bring-up audio diagnostics\n[Service]\nExecStart=/bin/sh /etc/systemd/system/luma-fold-audio-diag.sh\n[Install]\nWantedBy=multi-user.target\n" > $s/luma-fold-audio-diag.service
	mkdir -p $s/multi-user.target.wants
	ln -sf ../luma-fold-audio-diag.service $s/multi-user.target.wants/luma-fold-audio-diag.service
else
	rm -f $s/luma-fold-audio-diag.service $s/luma-fold-audio-diag.sh $s/multi-user.target.wants/luma-fold-audio-diag.service
fi
# PipeWire debug logging from earlier bring-up images
rm -f /sysroot/etc/systemd/user/pipewire.service.d/99-luma-fold-audio-debug.conf \
      /sysroot/etc/systemd/user/wireplumber.service.d/99-luma-fold-audio-debug.conf
