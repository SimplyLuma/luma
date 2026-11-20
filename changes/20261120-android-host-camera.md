---
type: security
component: Android
packages: [luma-android-runtime]
highlight: false
---
Summary: Internal laptop cameras stay with Luma
Details: Android no longer receives the raw camera devices of laptops with an Intel IPU or similar camera, which it cannot use. They are no longer left writable by every account on the computer, so only Luma's camera service and the apps you allow reach them.
