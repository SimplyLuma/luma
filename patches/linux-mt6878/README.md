# Unihertz Titan 2 Elite kernel patches

Apply the series in order to [MT6878-mainline/linux](https://github.com/MT6878-mainline/linux)
commit `12f2e9f040d0049f3f5ce5a7e53bf5ef27b46422` with
`git am --committer-date-is-author-date`, committing as the patch author, and
build with `config/mobile/titan2e-kernel.config` and `LLVM=1`.
`scripts/packages/build-titan2e-kernel.sh` does all of this; the kernel release
is then `7.2.0-luma-titan2e-m2-g7083190aafdc` on every build.

| Patches | What |
| --- | --- |
| 0001-0004 | Fixes: MT6878 EINT map overrun, peri_ao clock parents, T-PHY role-switch work, SPMI PMIF AP channel. |
| 0005 | MT6369 PMIC regulators (microSD, touch). |
| 0006-0007 | simpledrm connector type from DT and frame notifiers; the Titan's command-mode panel refresh. |
| 0008-0010 | AW9523B keyboard matrix, FocalTech FT3383 SPI touchscreen, MT6363 PMIC keys. |
| 0011-0012 | Built-in device tree (the bootloader passes an Android one) and the board device tree. |
| 0013-0016 | GPUEB mailbox and GPUFreq reply fixes; MT6878 GPUEB and MFG power domain for the Mali-G615. |
| 0017-0018 | Wi-Fi/Bluetooth (CONNSYS) device tree nodes. |
| 0019-0023 | Audio: MT6878 AFE, MT6369 codec, FourSemi FS18xx/FS19xx amplifiers, the sound card and its device tree. |
| 0024-0033 | Rear camera: Sony IMX766, MT6878 camera power domains, seninf and CAMSV raw capture, MM M4U. |
| 0034-0036 | simpledrm vblank from the panel and page flips paced to the refresh. |
| 0037-0039 | MT6363 AUXADC VIN1 channel and PMIC child nodes. |
| 0040-0047 | OmniVision OV32B40 front and OV50D40 telephoto cameras; seninf on CSI ports 0-2. |
| 0048-0050 | MT6363/MT6373 AUXADC fix; MT6369 and AFE rebind fixes. |
| 0051-0053 | The modem PCM interface, hostless speech front end and speech links. |
| 0054-0056 | NFC VEN kept asserted for SIM access and reserved from CONNSYS; GPIO hogs for MT6878 pinctrl. |
| 0057-0063 | MT6375 charger, MT6369 VIBR vibration motor, AW36404 keyboard backlight. |
| 0064 | Panel reset and re-init when the tearing signal does not return after wake. |

0019 and 0020 port MediaTek's GPL-2.0 MT6878 AFE and MT6369 codec drivers from
[motorola-mt6878/kernel_device_modules](https://github.com/motorola-mt6878/kernel_device_modules)
`6586afaa0622648a6e740fb4a83f4b8057346bc2`; 0021 ports FourSemi's GPL frsm
amplifier framework from the same tree (`sound/soc/codecs/fs1815`) and adds
FS19xx operations. Their copyright notices are kept. Every other patch is new
code under GPL-2.0, or GPL-2.0 OR MIT for device trees.

The Wi-Fi/Bluetooth drivers are out of tree; see `packaging/titan2e-connsys`.
