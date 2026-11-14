---
type: fix
component: Android apps
packages: [luma-android-runtime]
---
Summary: Android starts reliably on phones without binderfs
Details: Binder devices on kernels without binderfs now carry the SELinux label the Android container is allowed to use. Android launches no longer time out there, and the container no longer floods the journal with denials.
