# Remove the upstream donation notification

- Owner: Codex.
- Status: source validation complete; shared package rebuild pending.
- Scope: settings-daemon housekeeping on desktop, tablet and mobile, both CPU architectures.

The donation reminder is removed from the compiled housekeeping sources and
startup/shutdown hooks. Its compatible settings key defaults to false; a saved
true value cannot activate code absent from the executable. Normal notifications,
disk cleanup, thumbnail cleanup and memory monitoring retain their hooks.

Validation: the Fedora 50.1 source RPM passes its pinned SHA-256; all three
source patches and the Fedora spec patch apply with zero fuzz. Checks confirm
the producer is absent from the build and service hooks, the schema default is
false, and cleanup/monitoring remain. Package-script syntax and diff checks pass.
The package verifier rejects the campaign title, body and URL in the resulting
binary. Full RPM build and target-device qualification remain with the builder.

Build gnome-settings-daemon 50.1-1.luma.3 for both architectures, then promote
verified artifacts and pins. Existing running services require a restart or next
login to discard the old reminder timer. This source change does not publish OS
updates to existing installations.
