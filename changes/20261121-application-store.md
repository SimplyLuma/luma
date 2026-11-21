---
type: improvement
component: Valet
packages: [luma-application-installer]
highlight: true
---
Summary: Apps from a file install smaller and start faster
Details: Apps you install from .deb, .rpm, AppImage and archive files are now kept in one shared store on your device instead of one container each. Files that several apps have in common are stored once, apps start without a container engine, and removing an app leaves nothing behind. Apps you already installed are moved over in the background and keep their settings and sign-ins. An app that does not work the new way stays installed the old way.
