# Night Light native checks

Build Settings with its normal package producer first. `compile-native.py`
accepts the prepared upstream source, its completed Meson build and a private
output directory. It reuses that build's product objects and compiles only this
GUI wrapper; it does not overlay a different implementation.

Run the resulting `test-night-light` with the actual candidate Luma GTK,
libadwaita and developer-platform packages installed in a disposable fixture:

```sh
LUMA_SETTINGS_TEST_FIXTURE="$PWD/tests/fixtures/settings-v70.json" \
LUMA_NIGHT_TEST_OUT=/absolute/private/output GSETTINGS_BACKEND=memory \
GTK_A11Y=none GSK_RENDERER=cairo G_DEBUG=fatal-criticals GIO_USE_VFS=local \
xvfb-run -a -s '-screen 0 1600x1200x24' dbus-run-session -- \
/absolute/private/output/test-night-light
```

The test uses real time controls and checks 390/1040px widths, 12/24-hour
presentation, minute precision, midnight/noon conversion and dismissing an
uncommitted edit. Screenshots are generated only in the private output folder.
The normal package also checks all 1,440 minute values and the live GSettings
adapter. These GUI checks do not establish physical color-temperature support.
