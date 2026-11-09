---
title: Design principles
summary: The ideas behind every Luma app, what they mean in practice, and how the design guidelines are organized.
order: 1
section: Design
---

# Design principles

These guidelines describe how a Luma app looks, behaves and reads. Depot
reviews Luma tier apps against them, and any app that follows them will feel at
home on Luma. LumaUI implements most of them for you; the guidelines explain
the choices so you can make good ones where LumaUI stops.

## 1. Calm

The interface recedes so the work stands out. Surfaces are quiet neutrals, one
accent color marks what matters, and nothing moves or badges without a reason.
Density is comfortable, not cramped.

*In practice:* one accent per view, no decorative gradients or shadows of your
own, no attention-seeking animation, notifications only for things people
asked to know.

## 2. One app, every screen

The same app runs on a phone, a tablet and a desktop. Width decides layout,
presentation decides chrome, and input decides target sizes. A small screen
rearranges; it never removes.

*In practice:* design the 360px layout and the wide layout together, and check
the widths where LumaUI changes tier: under 560px is phone, 560 to 900px is
compact, and over 900px is regular. See [adaptive layouts](../kit/adaptive-layouts.md).

## 3. Structure you can see

A window is a title row, a navigation sidebar on the window frame, and one or
more islands. Each island does one job, such as showing the content or the
details of a selection. People learn the structure once and find it in every
app.

*In practice:* put controls in the island they affect, keep the details pane on
the trailing side, and never nest islands. See [windows and islands](windows-and-islands.md).

## 4. One answer to each question

LumaUI draws the frame and the components, so every Luma app answers “what
does a button look like” the same way, and a change to the shared design
reaches every app that uses it. An app that redraws a shared component gives people a second
answer and makes the whole system feel less trustworthy.

*In practice:* use LumaUI components and tokens; if LumaUI is missing
something, ask for the component instead of drawing a local version.

## 5. Every command, every way

Anything people can do has one name and can be reached by pointer, touch,
keyboard and assistive technology. Menus, buttons, shortcuts and semantic
actions all point at the same command.

*In practice:* one stable action ID per operation; every function is
keyboard-reachable; every context menu opens by right-click, long press, the
Menu key and Shift+F10.
See [commands and menus](../kit/commands-and-menus.md).

## 6. Honest and private

Apps say what they are doing and ask only for what they need. Saving is
confirmed quietly, failures are explained with a way forward, and access to
files and devices goes through portals that the person controls.

*In practice:* no fake progress, no dark patterns, no data leaving the device
without a clear reason. See [permissions and privacy](../permissions-and-privacy.md).

## 7. Accessible from the start

Design contrast, focus, accessible names, text size, reduced motion and
right-to-left layouts in the same sketch as everything else, rather than in a
pass at the end. See [accessibility](accessibility.md).

## The guidelines

| Page | Covers |
| --- | --- |
| [Windows and islands](windows-and-islands.md) | Title rows, identity, islands, sidebars, the details pane, dialogs |
| [Typography](typography.md) | Figtree, type roles, numbers, long text |
| [Color](color.md) | Tokens, light, dark, Frost, Glass, high contrast, accent use |
| [Spacing and sizing](spacing-and-sizing.md) | The spacing scale, radii, control and target sizes |
| [Icons](icons.md) | App icons, symbolic icons, sizes |
| [Motion](motion.md) | Durations, what should move, reduced motion |
| [Accessibility](accessibility.md) | Requirements and how to test them |
| [Writing style](writing-style.md) | Voice, capitalization, labels, errors |
