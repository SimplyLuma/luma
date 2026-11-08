---
type: improvement
component: Desktop
packages: [luma-vitals, luma-vitals-memory-guard]
---
Summary: Smarter handling when memory runs low
Details: An app using a lot of memory is named in one notification with Close and Keep Open buttons. When memory is nearly full, the app running away or the largest one not in use is named again before it is closed, never the app you are using. Swap holds up to twice as much on computers with 16 GB of memory or more.
