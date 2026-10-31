# MT6878 clock correction

`0065-clk-mediatek-mt6878-preserve-independent-PLL-gates.patch` applies after
patches 0001–0064 from the `devices/titan2elite` branch, whose kernel source
profile pins MT6878-mainline/Linux commit
`12f2e9f040d0049f3f5ce5a7e53bf5ef27b46422`. That branch currently contains the
remaining Titan kernel profile and build inputs.

The patch assigns each set/clear PLL its declared enable bit and the correct
register operations. In the previous descriptors, all these clocks defaulted
to bit zero (APLL2), allowing another PLL's shutdown to stop active audio.
The helper preserves audio tuner handling and does not change other providers'
operation selection. The fence PLL descriptors also use their shared-register
bits (MAINPLL6, UNIVPLL5, MMPLL3), matching the vendor table, rather than a
per-PLL bit zero.

On the development Titan, direct ALSA music cuts out while the PCM keeps running;
APLL2's enable bit is off during the gaps. An internal DL0→UL0 test matched 5,000
sampled generated audio blocks at constant delay. Holding ordinary CCF references
to the other set/clear PLLs kept amplifier PLL lock in324/324 samples; releasing
them restored intermittent lock. This is electrical evidence, not listening
acceptance: a later longer comparison was reported silent. The corrected full kernel
has now built and booted on the development Titan. Existing config, DTB,
built-in initramfs and exported symbols were preserved. The owner accepted current Tide playback and requested that this setup be
retained. Individual physical loudspeaker/receiver outputs have not been
independently isolated; Doom and call-path listening acceptance remain untested.
This is a development installation, not a published kernel or
OS update.

## Controller-based touch wake candidate

`0066-Input-focaltech-spi-low-power-double-tap-wake.patch` adds FT3383
controller gesture mode, a separate `KEY_WAKEUP` input device, serialized SPI access, system
sleep handling, and the standard `wakeup-source` opt-in on the Titan device tree.
It applies to the retained running driver after the existing Titan kernel series.
Its generated driver matches the compiled module input. No full replacement
kernel is required for the live development test because this driver is a module.

Package `scripts/mobile/touch-wake-policy.py` as
`/usr/libexec/luma-touch-wake-policy` and the matching unit from
`config/mobile/luma-touch-wake-policy.service` in the device image. Enable the
service only on profiles that provide the driver's `wake_gesture` capability.
The policy follows the active seat's compositor display-power events, including
lock screen and greeter, and arms hardware recognition while the panel is blank.
A monitor runs with the active user's bus credentials; only the small privileged
parent writes boolean kernel attributes. There is no touch or motion polling.
Stopping the service restores normal touch, including after a process failure.
The controller stays in low-power mode after recognition until display policy
observes an actual unblank. The shared handheld power handler explicitly handles
`XF86WakeUp` as wake-only in all shell modes; it never unlocks or toggles the
screen off. Ordinary keyboard activity alone was inconsistent after manual blank.

The development Titan currently runs a source-backed module override with its
packaged module retained as fallback. Physical taps increment the controller's
recognition counter, but the initial candidate did not wake the screen. Merely
separating the wake input device did not resolve that failure. After the explicit
shell wake handler was added, three consecutive injected wake events on the
actual driver input turned the display on, disarmed gesture mode and retained
the lock. Driver build eb790b7e and patch/source provenance check6f85f52e pass.
The owner confirmed physical double-tap wake on October30. The recognition
counter advanced5→6 and normal touch resumed (gesture mode0); both live services
remain active/enabled. A fresh boot, real system suspend/resume and longer
battery measurements remain pending. The policy has no
periodic work; earlier idle checks measured zero service CPU time.

Lift-to-wake is not implemented: this build exposes a PMIC ADC but no usable
accelerometer/motion event interface. It requires sensor/hub support rather than
adding a host-side polling loop.
