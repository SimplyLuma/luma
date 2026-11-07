---
type: improvement
component: Desktop
packages: [mutter, mutter-common]
---
Summary: Smoother dock and menus on large displays
Details: Small changes on screen, such as hovering over the dock or opening a menu, now redraw only the part of the screen that changed instead of the whole display, most noticeably on a Raspberry Pi driving a 4K TV.
