# Luma Energy

Luma Energy applies a conservative background-resource policy while a computer
is running on battery. It uses application visibility and activity to reduce
background resource use while leaving focused applications outside that policy.
Battery savings and responsiveness depend on the workload; the policy is not a
guarantee of a particular performance or energy result.

## How it knows

On Wayland there is no protocol that tells a client it is completely covered by
another window; a client only learns it has been unmapped. So a Chromium or
Electron window buried under a maximised window believes it is visible and
keeps painting. The compositor is the only component that knows otherwise, and
Luma ships the compositor: the `energy@project-luma.local` Shell extension
reports which applications are focused, visible, occluded or hidden, and this
service decides what that should cost.

## What it does

| State | What happens |
|---|---|
| Focused | Nothing at all. The foreground is never touched. |
| Visible | Nothing. |
| Occluded for 10s | Lower share of processor and disk; told it does not need a high clock. |
| Hidden for 30s | Lower still. |
| Hidden for 5 min, quiet | Paused, if pausing is turned on. Off by default. |
| Anything with work to do | Nothing, whatever its windows are doing. |

"Work to do" means sound, camera or video hardware in use, an alarm set, real
processor work in the last minute, or the person's own "keep running".

On mains nothing is clamped and nothing is ever paused. Plugging in gives
everything back at once.

## Undoing it

    luma-energy status          # every application, its state, and why
    luma-energy wake            # wake everything paused, now
    luma-energy keep-running C  # never limit this one
    luma-energy off             # off, and every limit undone

Nothing survives a reboot: every property is runtime-scoped. Stopping the
service undoes everything on the way out, and so does turning it off. A machine
without this running behaves exactly as it did before it was installed.
