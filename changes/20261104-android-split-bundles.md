---
type: fix
component: Android apps
packages: [luma-android-runtime]
---
Summary: Split app bundles install reliably
Details: APKM, XAPK and APKS bundles install through one Android install session. Bundles that only carry another language's split for a feature now install, and a bundle for another processor architecture is reported as such.
