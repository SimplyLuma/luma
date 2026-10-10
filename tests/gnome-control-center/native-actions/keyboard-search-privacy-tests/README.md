# Native Settings actions

These regressions exercise actual Settings helpers and adapters, using an in-memory GSettings backend, temporary search folders/backups, and a private D-Bus mock for Housekeeping. They never clear the user's real recent files, trash or temporary files, and never use the user's dconf or session bus.

Build against a matching Settings source/build with its `test-luma-view` and `test-luma-live-desk` objects already built:

```sh
python3 build-tests.py /absolute/settings-source /absolute/settings-build /absolute/test-output
```

Run the resulting binaries:

```sh
GSETTINGS_BACKEND=memory /absolute/test-output/test-native-keyboard
/absolute/test-output/test-search-write-failure
xvfb-run -a /absolute/test-output/test-native-search-folders
LUMA_PRIVATE_TEST_BUS=1 GIO_USE_VFS=local dbus-run-session -- xvfb-run -a /absolute/test-output/test-native-privacy
```

Coverage:

- Keyboard: adding a real XKB layout, saving/reordering/removing its actual source tuple, reopening the adapter, rejecting unknown layouts, preserving unrelated XKB options while changing Compose.
- Search: production asynchronous folder binding, add/readback/remove, rejecting nonlocal folders, and retaining the current model when provider/folder writes fail. The old provider helper fails the rejected-write test because its shallow copy changes the live model before the writer rejects the change.
- Privacy: Luma confirmation and cancellation, both actual Housekeeping method names, stale confirmation refusal, surfaced server errors and no delegated panel navigation.

The Housekeeping daemon is mocked, so the Privacy test proves method dispatch and failure handling, not filesystem cleanup by the real daemon. It uses the same methods as the native upstream usage panel. The keyboard tests do not prove input activation in a running patched Shell; the production owner invokes the restricted `org.gnome.Shell.SelectInputSource(ss)` bridge and reports refusals/errors in place. The Shell method has a separate behavior/security regression.

The search folders test creates a private `/tmp/luma-search-write-*` directory with backup files. This data is disposable test output.
