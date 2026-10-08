# Fairphone 6 FocalTech fingerprint control shim

This directory carries the GPL-2.0 FocalTech input subsystem published in
Fairphone's official `kernel/qcom` repository at commit
`80da648332ffb13f3415224058d286e47429eca7`, followed by six narrow Linux
7.1 API, resource-lifetime, and compatibility patches.

The original Gitiles source archive is:

- URL: `https://gerrit-public.fairphone.software/plugins/gitiles/kernel/qcom/+archive/80da648332ffb13f3415224058d286e47429eca7/drivers/input/finger.tar.gz`
- SHA-256: `4f4bbe30774a1af4cc0242ac45359007dc35a58043c1498557f9a51db22ea6cd`

Fairphone's Android 15 and 16 branches publish byte-identical driver files.
The source declares version `V3.1.0-20220701`; that version, its retained
source path, GPIO properties, module author, and GPL-v2 metadata match the
stock `focaltech_fp.ko` recovered from the lab phone.

The second patch updates changed class/platform/SPI removal and poll APIs,
makes the subsystem opt-in, and closes partial IRQ and driver-removal cleanup
paths. The third converts the removed OF integer-GPIO interface to descriptor
GPIOs owned for the platform-device lifetime. The fourth excludes the optional
Android framebuffer blank notifier when the Linux framebuffer event ABI is not
present. The fifth replaces a non-exported IRQ-descriptor debug lookup with the
driver's own IRQ-enabled state. The sixth removes an inapplicable IRQF_ONESHOT
flag from the driver's non-threaded primary interrupt request. They do not enable
direct SPI access, implement
the FocalTech trusted application protocol, load a trustlet, or make biometric
authentication ready.

All six patches apply after `patches/linux-qseecom/` to the pinned Milos 7.1.2
kernel. The device-tree overlay remains disabled until an enumeration-only
candidate is compiled and reviewed.
