---
type: fix
component: Android apps
packages: [luma-android-runtime]
---
Summary: Phones that cannot run Android apps start up cleanly
Details: On a phone whose hardware is not cleared for Android apps, Luma no longer tries to start Android at every sign-in, so the system no longer reports failed services there.
