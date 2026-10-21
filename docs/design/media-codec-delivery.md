# Platform media playback

Status: investigated proposal, 2026-10-21. No codec delivery change is enabled
by this document.

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

## Recommended implementation boundary

Ship Luma's integration, trusted download manifest, notices and Settings controls.
For the Cisco route, fetch the unmodified official binary directly on each end
device during connected setup. Use cryptographic content verification, atomic
installation, ABI checks and retained known-good updates. A vendor-provided MD5
file is not a sufficient production trust mechanism. Preserve source and binary
notices and use the required attribution at the control.

Deliver the decoder plugin and binary to the OS and the Luma runtime through
owned, versioned interfaces. Do not broaden app filesystem permissions or assume
a host library is visible to WebKit's nested sandbox. Keep enable/disable state
consistent and explain when a new app session is needed. Test AAC/container
support independently; H.264 is only the video codec.

A codec download failure must remain retryable and must not block OS installation,
rollback, unrelated app updates or unrelated package installation. Retain a
working installed decoder during an outage. First-time offline setup must show
an honest unavailable/waiting state. The nightly qualification observed an
optional Cisco repository HTTP404 blocking an unrelated package installation;
a later request succeeded. Upstream also records [extra-data availability problems](https://gitlab.com/freedesktop-sdk/freedesktop-sdk/-/issues/1834).

Before enabling production delivery, qualify native and sandboxed WebKit,
Baseline/Main/High, AAC audio, seeking, malformed input, architecture variants,
hardware/software fallback, disabled codecs, offline setup and interrupted
updates. Confirm the precise Cisco delivery/use arrangement, or obtain Luma's
own rights for a fully bundled codec. No such independent commercial arrangement
has been established here.
