# Settings native action checks

Run `python3 run.py --source /path/to/settings/shell --build /path/to/configured/build` inside the Settings build environment.

These checks compile the actual production action code. They use temporary directories and a private session bus/display; they do not delete installed apps or user accounts, operate a real printer or set the machine's clock.

- Cache: clear nested cache files, unlink a symlink into app data without following it, preserve documents, preserve contents on cancellation.
- Apps: launch the registered desktop app when the Settings identifier is normalized differently, send its exact identity to Depot, retain an error when the app is missing.
- Manual time: intercept the real writer's DBus call, assert the exact timestamp and interactive authorization, reject enabled automatic time, invalid values and missing services without writes.

Users authorization and printer operations retain their existing AccountsService/CUPS implementations. Those operations still require system-service/hardware qualification in addition to these checks. Their native action callbacks no longer navigate to a different Settings pane.

Additional hotspot and wired-adapter checks: see [NETWORK.md](NETWORK.md).
