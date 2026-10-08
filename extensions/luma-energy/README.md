# Luma Energy attention state

Tells Luma Energy which applications the person can actually see.

A mapped Wayland client does not necessarily know whether another window
completely covers it. Covered applications can continue rendering or running
timers. This extension uses the compositor's window-stacking information to
report attention state to the Luma Energy service.

It reports and nothing else. It holds no policy, applies no limit, and if
luma-energy is not installed or not running it does nothing at all. Windows are
associated with applications using the window process's cgroup rather than a
hard-coded table of package names. Correct association depends on the available
process and cgroup identity information.

Occlusion is judged conservatively: a window counts as covered only when a
single window above it covers it outright. Accumulating a region would catch
more cases and would also be the kind of cleverness that eventually decides a
window the person is reading is invisible. Being wrong in that direction costs
a little battery; being wrong in the other costs the person's trust.

It also wakes applications before they are needed — when the overview opens,
when Alt-Tab is pressed, when a window is created, when an application demands
attention — so that nothing the person picks has to be woken while they watch.
