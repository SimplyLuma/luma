---
type: improvement
component: Viola
packages: [viola-browser-stable]
highlight: false
---
Summary: Updated Viola to 151.0.7922.72
Details: With two Viola windows open, the windows no longer redraw their sidebars dozens of times a second when nothing changes, which kept several CPU cores busy. Each window now updates only when something changes, at most once per frame.
