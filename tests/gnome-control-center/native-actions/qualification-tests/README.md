# Native Settings writer qualification

Run `sh run.sh PATCHED_SETTINGS_SOURCE MATCHING_SETTINGS_BUILD` as an ordinary user in a disposable build environment with GCC, pkg-config, Python, GTK4, Json-GLib, PulseAudio utilities, Xvfb and D-Bus. The Settings build must contain matching `shell/test-luma-view` objects and Gvc. No OS/image rebuild is required.

These regressions compile current production adapters and the current model write gate. They exercise:

- 56 GSettings mappings: native readback, fresh-adapter persistence, invalid JSON types, unsupported keys, schema ranges and a backend policy lock.
- Camera, microphone and location PermissionStore writes: actual desktop identity mapped to the real portal ID, authoritative readback, access denial surfaced through `write-error`, unknown IDs and disposal during a pending write.
- Dynamic app notification permissions and Apps portal permissions, including denial and invalid types.
- Six MIME defaults, four removable-media preference transactions and seven supported Shelf presets, with persistence and unsupported-choice refusal.
- Tiling through a private Shell D-Bus service, extension schema values, reopening, refused service responses and invalid gaps. Global extension pause reports Off and refuses enable without changing the user’s pause setting.
- A mapped Sound view against a private mixer: output selection, a playing app's volume, disappearance refusal, microphone selection/volume/reopening, unsupported configuration refusal and zero panel delegations.

The test uses private temporary data/config directories, a memory settings backend, a private session bus and a PulseAudio Unix socket with only virtual sinks/sources. It does not connect to the user's mixer or alter real display, account, printer, hostname or system service configuration. Metadata/ready-gate callbacks are stubbed in standalone writer tests; the Sound case uses the existing matching GTK view objects. Hardware-specific speaker profiles, actual tiling window placement and successful polkit authentication remain separate integration checks.
