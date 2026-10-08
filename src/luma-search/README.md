# Luma Search service

This package owns the form-factor-neutral provider discovery, privacy filter,
ranking, and activation contract for the public **Search** surface. GNOME Shell
and Phosh are presentation clients. Content remains owned and indexed by its
application or standard `org.gnome.Shell.SearchProvider2` provider; this
service has no crawler and no persistent result database.

`org.projectluma.Search1.Query(query, generation, limit)` returns normalized
results in the ten public
kinds: App, Setting, File, Mail, Contact, Calendar, Message, Photo, Music, and
Place. Provider descriptors may declare `X-Luma-Kind`; known conventional
providers receive a compatibility classification. Unknown providers remain
App results rather than being silently dropped.

The session bus activates the process on demand. It exits after thirty idle
seconds and performs no polling. Queries run outside the shell process and
have a bounded per-provider D-Bus deadline. Mail and Message snippets are
removed unless the explicit privacy setting allows them.

Settings remain compatible with `org.gnome.desktop.search-providers`; the
shell clients also consume `org.projectluma.search` for result-kind and privacy
policy. The shared `luma-search-settings` GTK surface edits both sources and
shows installed-provider descriptions without creating a renderer-specific
preference store. The first physical release gate must inventory the provider
descriptor set on both images and verify identical enabled providers and
ordering.
