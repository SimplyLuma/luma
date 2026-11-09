# Devices

One directory per supported device, `devices/<vendor>/<codename>/`, each a
strict manifest checked by `tools/check-devices.py`. Shared chip-family
configuration is in `../socs/`. See [docs/devices.md](../docs/devices.md) for
the layout, support tiers and how to add a device.

| Device | Directory | SoC | Tier |
|---|---|---|---|
| Fairphone 6 | [fairphone/fp6](fairphone/fp6) | Qualcomm SM7635 (milos) | supported |
| OnePlus 6T | [oneplus/fajita](oneplus/fajita) | Qualcomm SDM845 | experimental |
| Raspberry Pi 5 | [raspberrypi/rpi5](raspberrypi/rpi5) | Broadcom BCM2712 | experimental |
