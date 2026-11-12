---
type: fix
component: LumaUI
packages: [luma-developer-platform]
highlight: false
---
Summary: Tooltips let go of buttons that close
Details: A LumaUI tooltip stayed attached to its button after the button was gone, which could crash an app such as Depot later on. Tooltips now leave with their button, and touching or scrolling with a finger no longer shows them.
