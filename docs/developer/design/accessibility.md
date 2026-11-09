---
title: Accessibility
summary: The accessibility requirements every Luma app meets, what LumaUI does for you, how to label custom widgets, and a test plan covering keyboard, screen readers, contrast, text size, motion and right-to-left.
order: 8
section: Design
---

# Accessibility

Luma apps work for people who use a keyboard only, a screen reader, a switch,
large text, high contrast, reduced motion, touch, or a right-to-left language.
These are requirements for Luma tier apps in Depot and strongly expected of
every app. The same accessible names and roles are also how assistants and
automation understand your app.

## What LumaUI does for you

- LumaUI components set accessible names and roles: `IconButton` and `AlignCluster`
  buttons are labeled; `NavigationRow` speaks its title, subtitle and count;
  `FieldRow` speaks “label: value”; `Avatar` speaks “name avatar”; `EmptyState`
  hides its decorative glyph; `NumericField` describes a mixed value;
  `LumaAudioMeter` updates its description in steps rather than every frame.
- Menus built from commands are keyboard complete, expose checked state, and
  open from the **Menu** key and **Shift+F10**.
- `reduced_motion()` and `motion_duration()` (Python) and
  `luma_ui_reduced_motion()` (C) report the motion setting, and LumaUI’s own
  animations follow it.
- Tokens switch to high contrast colors automatically.
- GTK widgets expose themselves to AT-SPI, which Orca and other assistive
  technologies use.

Anything you build yourself is your responsibility.

## Requirements

### Names, roles and states

- Every interactive element has an **accessible name**. Icon-only buttons need one
  even though they have a tooltip:
  ```python
  button.update_property([Gtk.AccessibleProperty.LABEL], ["Add note"])
  ```
- Custom widgets set an **accessible role** (`accessible_role=Gtk.AccessibleRole.BUTTON`
  in the constructor, or `gtk_widget_class_set_accessible_role()` in C).
- Toggles, expanders and selections expose **state** with `update_state()`
  (`CHECKED`, `EXPANDED`, `SELECTED`, `DISABLED`).
- Related elements declare **relationships**: a field and its label
  (`LABELLED_BY`), a control and its description (`DESCRIBED_BY`).
- Purely decorative images are hidden
  (`update_state([Gtk.AccessibleState.HIDDEN], [True])`).
- Live changes that matter, such as a failed save, are announced
  (`widget.announce("Couldn’t save", Gtk.AccessibleAnnouncementPriority.MEDIUM)`).

### Keyboard

- Every function is reachable and operable by keyboard alone.
- Focus order follows reading order. Do not trap focus except in modal dialogs.
- Focus is always visible. Do not remove focus indicators.
- Escape closes popovers, menus, sheets and inline editors.
- Custom drawing areas that people interact with, such as a canvas or timeline,
  provide keyboard alternatives for pointer gestures (arrow keys to move,
  shortcuts to select).

### Pointer and touch

- Targets are at least 44 by 44px with touch input.
- No function depends on hover, double-click, or a precise drag alone.
- Every context menu also opens by long press.

### Vision

- Text contrast is at least 4.5:1, large text and essential glyphs 3:1. Use
  [tokens](color.md).
- Color is never the only way information is shown.
- Layouts survive the large text setting without clipping or overlapping.
- The app works in high contrast.

### Motion and time

- Custom animations stop when animations are off.
- Nothing flashes more than three times a second.
- Nothing important disappears on a timer without a way to get it back.

### Language

- All strings are translatable and not assembled from fragments.
- Layouts mirror in right-to-left languages; use `start` and `end`.
- Content in another language or direction is marked so it renders correctly.

## Test plan

Run this before every release that changes the interface.

| Test | How |
| --- | --- |
| Keyboard only | Unplug the mouse. Complete every main task with Tab, arrows, Enter, Space, Escape and shortcuts. |
| Screen reader | Turn on the Orca screen reader. Navigate every screen; every control should say what it is, its state and its value. |
| Accessibility tree | Inspect with Accerciser, or run with `GTK_DEBUG=interactive` and use the Accessibility tab of GTK Inspector. |
| High contrast | Turn on high contrast in Accessibility settings. |
| Large text | Turn on Large Text in Accessibility settings, and check 360px width with it on. |
| Reduced motion | Turn on Reduce Animation in Accessibility settings. |
| Transparency | Choose Frost or Glass in Appearance settings, then turn on reduce transparency. |
| Right-to-left | Run with `LANGUAGE=ar` or `LANGUAGE=he` and a translation, or GTK Inspector’s text direction switch. |
| Touch | Run with `LUMA_INPUT_MODE=touch` and on a touch device if you have one. |
| Long strings | Use a pseudo-translation that lengthens strings by 40%. |

Record results as the `accessibility`, `keyboard` and `reduced_motion` lanes in
your [evidence file](../getting-started/luma-app-toml.md#evidence-file).
