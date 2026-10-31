# Devices

One directory per supported device, `devices/<vendor>/<codename>/`, each a
strict manifest checked by `tools/check-devices.py`. Shared chip-family
configuration is in `../socs/`. See [docs/devices.md](../docs/devices.md) for
the layout, support tiers and how to add a device.

| Device | Directory | SoC | Tier |
|---|---|---|---|
| Fairphone 6 | [fairphone/fp6](fairphone/fp6) | Qualcomm SM7635 (milos) | supported |
| Pixel 9 Pro Fold | [google/comet](google/comet) | Google Tensor G4 (zumapro) | experimental |
