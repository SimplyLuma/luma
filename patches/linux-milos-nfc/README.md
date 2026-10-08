# Fairphone 6 S3NRN4V NFC series

These seven `git format-patch` files preserve the exact NFC branch merged into
Catcrafts `milos-linux` `combined-stable` at
`af49850e65bdf5dc6f4a14fb9e8e5c5814522d33`. They apply from the Milos
`v7.1.2-milos` base and add:

- the FP6 Samsung S3NRN4V node on I2C1;
- the S3NRN4V variant of the existing s3fwrn5 NCI driver;
- the FP6 reference-clock and PVDD behavior; and
- version-gated DUAL_OPTION calibration upload.

The driver requests two calibration payloads:
`samsung/s3nrn4v/hwreg.bin` and `samsung/s3nrn4v/swreg.bin`. Those payloads are
not in Project Luma and their hashes remain unset in
`config/mobile/fp6-nfc.env`. Enumeration without those files is not reader-mode
acceptance, and reader-mode acceptance would not imply NFC writing, secure
element access, payments, or card emulation.

Source: <https://forgejo.catcrafts.net/Catcrafts/milos-linux/src/branch/combined-stable>
