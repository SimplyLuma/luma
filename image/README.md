# Image definitions

[`luma-desktop/Containerfile`](luma-desktop/Containerfile) defines the Fedora Atomic
desktop image used by the current OS build pipeline. Component RPM inputs,
offline application baseline contracts and release pins are maintained in
`config/desktop/` and `config/os/`.

The [public build guide](../BUILDING.md) describes component packaging, atomic
image construction, OSTree export, qualification and Atlas installation-media
composition. Image construction requires independently prepared package and
signed offline baseline inputs; this directory does not contain a complete
prebuilt release or private signing credentials.

[`inputs.env`](inputs.env) retains the Track A OSTree inputs, while
[`track-b/`](track-b) contains the alternative container-image definition. They are development/test definitions,
not the release installation workflow. Test provisioning must stay isolated
from images delivered to users, and no personal account or authentication key
belongs in the published source or a shipping image.

Desktop and handheld profiles share native application/platform source but need
separate architecture and device artifacts. See [platforms](../docs/platforms.md)
for those limits. A successful image build does not qualify a physical device or
publish a release.
