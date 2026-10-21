# Applications drawer native checks

In an owned disposable native Shell fixture, install the candidate Shell and
its normal runtime dependencies. The fixture must have the same private `/run`
and `luma-media-test` UID1000 used by the status-polish runner. Then run:

```sh
LUMA_NATIVE_SHELL_DISPOSABLE=1 DEV_ARTIFACT_DIR=/absolute/private/output \
bash tests/gnome-shell/applications-drawer/run-native.sh
```

This checks the real AppSystem catalogue, keyboard opening/search/launch,
light/dark search contrast, monitor bounds and preservation of custom or empty
folder lists. Its small fixture catalogue is not a substitute for reviewing
screenshots with the full desktop app collection. Generated images stay outside
Git. Applications drawer release requires the owner's screenshot approval.
