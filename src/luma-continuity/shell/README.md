# Existing-owner notification export candidate

These modules are an uncomposed source candidate for GNOME Shell's existing
notification owner, not a separate service. `notificationExport.js` policy is
Node-tested. `notificationExportBridge.js` uses real GJS/Gio/GLib interfaces but
has not been run in Shell. Do not enable until the following integration and
security gates are closed.

Bridge ABI (private proposed): existing Shell session-bus identity,
`/org/projectluma/Connect/Notifications`,
`org.projectluma.Connect.NotificationExport1`. The currently approved local
`org.projectluma.Connect1` broker's unique owner is required for every call.
Broker ownership policy and sandbox/SELinux denial for other applications must
be reviewed before enablement; possession of an arbitrary well-known name alone
is not sufficient protection against a same-user hostile process.

Local source consent uses real authoritative Source objects and is denied by
default. Settings must supply source read/action opt-in, never infer it from
remote-supplied names or payload hints. Strong producer sensitivity/opt-in
schema is still absent upstream; the candidate local source gate is not proof
that this release requirement is closed. Lock/greeter exports nothing and
invalidates all action capabilities. No inline reply token, callback, local
path, activation token or raw action identifier is exported.

Integration points still required and untested:

1. Copy through maintained source packaging into Shell `js/ui`, include resources,
   instantiate in existing owner after MessageTray creation, and destroy on exit.
2. Call `notificationReplaced()` **before** FDO and GTK replacement paths,
   including same-object/same-property replacement. Signal observation alone is
   insufficient. Authenticate producer identities using native source ownership.
3. Local Settings source-consent storage, live grant changes and source-policy
   changes must synchronously call the bridge invalidation hook. Exports/actions
   fail closed until this is composed. Reconcile source lifetimes and disconnect
   observation handles to bound long-session resource usage.
4. Current prototype supports one paired peer session; additional peers require
   independent policies, grants, stream and action maps, not shared capabilities.
5. GJS D-Bus ABI/runtime tests, race/replacement/lock tests, malicious sender/name
   acquisition tests, accessibility/source Settings tests and packaged desktop/
   Phosh parity are release blocking. Node policy tests do not establish these.

The phone uses Phosh: its equivalent authoritative notification-owner adapter
must obey the same contract below the shared Settings/UI. A GNOME-only bridge
is not phone notification implementation.
