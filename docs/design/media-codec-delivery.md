# Platform media playback

Status: investigated 2026-10-21; delivery implemented 2026-11-19 (section
"Delivery" below).

Luma should provide working media playback as a maintained platform capability:
connected setup prepares it once, applications use shared APIs, and updates
maintain it. Users should not have to discover package repositories or repair
individual applications.

## Product references

Windows exposes a shared Media Foundation H.264 decoder supporting Baseline,
Main and High profiles. Android documents platform H.264 and AAC support and
exposes codecs through MediaCodec. Apple's VideoToolbox provides shared video
compression and decompression services. These are useful product and API
references; their vendors' licensing arrangements do not establish Luma's
redistribution rights. Sources: [Microsoft](https://learn.microsoft.com/en-us/windows/win32/medfound/h-264-video-decoder),
[Android](https://developer.android.com/media/platform/supported-formats),
[Apple](https://developer.apple.com/documentation/videotoolbox).

## Observed Luma behavior

Nightly 20261020.1's native image includes the GStreamer OpenH264 plugin, linked
to `noopenh264` 2.6.0, a placeholder implementation. Its published Platform 44
runtime has neither `openh264dec` nor `avdec_h264`. Both include an AAC decoder.
Installing a host library alone does not supply a decoder inside Flatpak.
Shared additions belong in the runtime's extension mechanism, which is also
available to applications' nested sandbox processes. See [Flatpak extensions](https://docs.flatpak.org/en/latest/extension.html).

A disposable installation produced a self-owned two-second, 320×240 H.264/AAC
MP4. Playback with the shipped native and runtime stacks reached EOS with
96 audio buffers but **zero video buffers**. Checking only exit status or EOS
would incorrectly report success.

A separately downloaded Cisco 2.6.0 binary, used only through a test process's
library path, decoded all 60 video frames and 96 audio buffers natively.
An isolated runtime invocation with the matching GStreamer plugin also decoded
60/96. Native WebKit 2.54.0 played the muted test video beyond one second and
presented 29 frames. These establish a viable software path for this sample;
they do not establish Main/High, hardware acceleration, every browser, or
production delivery. The initial WebKit probes exposed fixture environment,
application identity and autoplay-policy errors; they were not codec failures.
The runtime WebKit delivery path remains unqualified.

## Distribution choices

OpenH264 source is BSD-2-Clause; its copyright permission alone does not settle
AVC patent licensing. Cisco's binary terms add conditional patent coverage:
the binary must reach the end device separately, controls must permit enabling,
disabling and re-enabling it, and the control location must show Cisco's exact
attribution. The complete terms must be available with licensing information.
The grant describes particular uses and is not universal clearance for all
content or commercial services. Baking the binary into a Luma ISO would not
satisfy its separate-download condition. See [source license](https://github.com/cisco/openh264/blob/master/LICENSE),
[binary terms](https://www.openh264.org/BINARY_LICENSE.txt), and
[Cisco FAQ](https://www.openh264.org/faq.html).

Fedora documents a separately negotiated build/sign/distribute arrangement
with Cisco. Luma cannot infer an equivalent agreement merely from the source
license or Fedora's packaging. See [Fedora distribution explanation](https://fedoraproject.org/wiki/Non-distributable-rpms).

## Decision

OpenH264 is on by default. Each device downloads Cisco's unmodified binary
from Cisco; Luma does not ship, build or redistribute the codec. This is the
model Flathub (`org.freedesktop.Platform.openh264`) and Firefox use under
Cisco's public binary license: separate download to the device, Cisco's
attribution and an on/off control where the user manages it, and the license
terms linked from there. The control is Settings' "OpenH264 video codec"
switch, which shows the attribution, links the terms and runs
`luma-openh264 enable|disable`.

A codec download failure stays retryable and never blocks OS installation,
rollback or unrelated updates. A working installed decoder is kept during an
outage. No separate commercial arrangement with Cisco exists; a codec bundled
in the image would need one.

## Delivery

Hardware encode is Luma's own drivers: `intel-media-driver-free` with VDEnc
H.264/H.265 (`packaging/rpm/intel-media-driver-free.spec`) and Mesa built with
`video-codecs=all_free,h264enc,h265enc` (`patches/mesa`), on the OS and in
`org.projectluma.Platform.GL.default`. GStreamer's va plugin registers
`vah264lpenc`/`vah265lpenc` (Intel) and `vah264enc`/`vah265enc` (AMD) from
the driver's entrypoints. Hardware decode of H.264/H.265/VC-1 stays off.

Software H.264 is Cisco's own unmodified binary
(`src/luma-openh264/cisco-openh264.conf`: Cisco's generic Linux 2.6.0 build for
x86_64 and aarch64, URL, size and SHA-256 of the download and of the
library). Fedora's RPMs from the fedora-cisco-openh264 repository are not
used: they are covered by Fedora's own arrangement with Cisco, and on an image
system they could only be layered, which makes every update depend on that
repository.

- OS: `luma-openh264.service` (timer: a minute after boot, then every six
  hours) downloads the file from ciscobinary.openh264.org over HTTPS, checks
  both SHA-256s, and renames it into `/var/lib/openh264/libopenh264.so.8`.
  Luma's `noopenh264` (`patches/noopenh264`) forwards to that file when it
  exists and is the stub otherwise, so `openh264enc` works in every process
  started afterwards, with no registry rebuild and nothing in `/usr` changed.
  A failed download keeps the installed file and is retried; it never blocks
  an update.
- Flatpak: `org.projectluma.Platform.openh264` is an extra-data extension
  (`scripts/depot/runtime/openh264-extension.sh`). Depot publishes only its
  metadata; flatpak downloads the same Cisco file onto the device and
  `apply_extra` unpacks it into `lib64/openh264/extra`, which the Platform's
  extension point puts on the library path ahead of the runtime's noopenh264.
  `luma-openh264.service` installs it whenever the Platform is installed.
- Default: on. The package enables `luma-openh264.timer` (its own
  `timers.target.wants` link and `60-luma-openh264.preset`), so a fresh
  install downloads the codec with no user action.
- Control: `luma-openh264 status|enable|disable` shows Cisco's attribution
  and switches both sides; disable removes the file, uninstalls the extension
  and masks it. Applications already running keep what they loaded until they
  restart. Settings › Apps shows the switch with the attribution and a
  link to Cisco's terms; polkit (`50-luma-openh264.rules`) lets the active
  local session run exactly `luma-openh264 enable` and `disable` without a
  password. `/usr/share/luma-openh264/NOTICE` is the licensing notice.

Encoders in order of preference: VA H.264 (`vah264lpenc`, `vah264enc`), then
`openh264enc`, then AV1. Applications choose by starting each encoder on a
test frame, since a missing driver entrypoint or the noopenh264 stub fails at
start, not at lookup (`src/luma-streamy/luma_streamy/engine.py`).
