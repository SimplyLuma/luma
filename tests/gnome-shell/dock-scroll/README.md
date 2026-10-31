# Dock wheel reachability

Run `run-native.sh` in the disposable native Shell fixture described by
`../status-polish/run-native.sh`. The fixture uses private settings and app data.
It pins 32 synthetic entries and a real GTK probe app, sends actual pointer
wheel events, and activates the last app after it becomes visible.

Nine checks cover ordinary and reverse wheel input, horizontal input applied
once against a native ScrollView control, smooth vertical input, visible bounds, activation and close. Offset and
allocation observations remain in `result.json`. This component test does not
qualify an Android APK, the whole OS, physical input hardware or performance.
