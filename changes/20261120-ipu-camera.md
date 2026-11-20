---
type: fix
component: Camera
packages: [libcamera, libcamera-ipa]
highlight: false
---
Summary: Built-in cameras no longer stop the media service
Details: On laptops with an Intel IPU camera, a frame the camera hardware rejected could stop the service behind sound and cameras, and the camera disappeared for every app until it restarted.
