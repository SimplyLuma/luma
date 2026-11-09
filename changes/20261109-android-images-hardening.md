---
type: fix
component: Android apps
packages: [luma-android-images]
---
Summary: Android no longer stops when the system declines a request
Details: A request the system declines, such as posting a notification, now fails quietly instead of restarting Android. Android also stops filling the system log with scheduling errors it cannot act on.
