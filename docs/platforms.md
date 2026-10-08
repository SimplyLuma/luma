# Platforms and device profiles

Luma uses a shared OS platform and application codebase across desktop, tablet and mobile. Architecture and hardware differences belong in configuration, packaging and narrowly scoped upstream patches rather than duplicated app repositories.

- `config/desktop/`: desktop package inputs and composition.
- `config/mobile/` and `scripts/mobile/`: mobile userspace, boot and device work.
- `patches/linux-*`, `patches/gnome-mobile/`, `patches/phosh/` and `patches/phoc/`: kernel and mobile integration where required.
- `packaging/`: component recipes and architecture constraints.
- `src/luma-platform/`: shared UI and application services.

x86_64 and AArch64 need separately built artifacts. Phone boot images, firmware and kernel support are device-specific; never flash an image merely because another device has the same CPU architecture. Device configuration in this source tree includes development and bring-up work and is not a supported-device certification.

The beta desktop installer is an x86_64 artifact. Viola's current native host builder is also x86_64-specific and consumes an external engine archive. Do not infer a complete ARM desktop or phone release from shared source availability. Future wearable or kiosk profiles can use the same shared platform without duplicating it; they are not released products in this baseline.
