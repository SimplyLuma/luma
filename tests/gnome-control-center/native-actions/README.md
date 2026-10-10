# Native Settings actions

These regressions accompany Settings53. Run them in a disposable build
container against the matching patched Settings source and configured Meson
build. Each group documents its dependencies and isolation requirements:

- `assigned-tests`: application/cache/Depot and manual-time actions.
- `keyboard-search-privacy-tests`: layouts, search writes and cleanup dialogs.
- `qualification-tests`: GSettings, portal permissions, defaults, Shelf,
  tiling and a mapped Sound view using a private audio server.

The shared pane-retention regression is compiled into `shell/test-luma-view`:

```sh
xvfb-run -a dbus-run-session -- env GSK_RENDERER=cairo \
  GSETTINGS_BACKEND=memory LUMA_SETTINGS_TEST_FIXTURE=/path/settings-v70.json \
  /path/settings-build/shell/test-luma-view -p /settings/view/live-failure-keeps-pane
```

It checks unsupported writes, policy refusal, asynchronous errors and legacy
unavailable actions without changing panes or claiming the value changed.
The companion Shell bridge regression is `tests/gnome-shell/settings-input-source.js`.

These are native adapter and UI boundary tests. Successful privileged account
operations, physical printing, external-provider authentication, physical
audio capture and keyboard activation in an installed Shell session require
separate runtime qualification.
