---
title: Writing style
summary: How Luma apps write: voice, capitalization, labels, buttons, menus, empty states, errors, confirmations, numbers and punctuation.
order: 9
section: Design
---

# Writing style

Words are most of the interface. Luma apps write the way a helpful person
talks, in plain words about what the reader is trying to do.

## Voice

- **Plain.** Use everyday words. “Delete” not “purge”; “can’t connect” not
  “connection establishment failure”.
- **Brief.** Say it once. Cut words that do not change the meaning.
- **Calm.** No exclamation marks in interface text, no jokes in errors, no
  blame.
- **Direct.** Address the reader as “you” when needed; refer to your app by its
  name, not “we”.
- **Honest.** Say what happened and what will happen. Never overstate security,
  speed or certainty.

Avoid filler words that praise the app, such as “simply” and “easily”. Show the
benefit instead.

## Capitalization

Luma apps use **sentence case** for interface text: capitalize the first word
and proper nouns only.

| Element | Example |
| --- | --- |
| Menu items and commands | “New note”, “Move to Trash”, “About Notebook” |
| Buttons | “Save”, “Add account”, “Open in Depot” |
| Window, page and section titles | “All notes”, “Keyboard shortcuts” |
| Sidebar sections | Written in sentence case; `SectionLabel` displays them uppercase |
| Tooltips and descriptions | “Show the sidebar” |

Product and feature names keep their capitals: Luma, Depot, Filer, Ari, Luma
Sync, Luma Connect, Trash. Write “Luma” with a capital L everywhere.

## Labels

- **Buttons and menu items are verbs** or verb phrases: “Save”, “Rename”,
  “Share link”. Name the object when the verb alone is ambiguous: “Delete note”.
- **Add an ellipsis (…)** only when the command asks for more input before it
  acts: “Open…”, “Rename…”, “Export…”. Use the single ellipsis character.
- **Toggles describe the state they turn on**: “Show grid”, not “Toggle grid”.
- **Never use “OK”, “Yes” or “No”** as button labels. Say what the button does.
- **Keep labels short** so they survive translation; plan for text up to 40%
  longer.

## Empty states

Say what belongs here and how to add it.

| Title | Description | Action |
| --- | --- | --- |
| “No notes yet” | “Notes you create appear here.” | “New note” |
| “No results” | “Try a different word.” | none |
| “Nothing selected” | “Select a layer to see its properties.” | none |

## Errors

An error message says what went wrong, why if it helps, and what to do next.

- Good: “Couldn’t save ‘Groceries’. The disk is full. Free up space and try
  again.”
- Bad: “Error 28: write failed.”

Put technical detail in a details area or tooltip, not the headline. When the
app will retry on its own, say so: “Couldn’t save · Retrying…”.

## Confirmations

Confirm only consequential or destructive actions, and make the confirmation
specific.

- Title: a question naming the object. “Delete 3 notes?”
- Body: the consequence, in one sentence. “They will be moved to Trash.”
- Buttons: “Cancel” and a verb matching the action, “Delete”, marked
  destructive.

Prefer undo to confirmation where you can offer it: “Note moved to Trash ·
Undo”.

## Numbers, dates and units

- Use numerals: “3 notes”, not “three notes”. Handle singular and plural with
  your translation framework’s plural forms, not by adding “s”.
- Format dates, times, numbers and sizes with the person’s locale (GLib’s
  `GDateTime` formatting and `g_format_size()` do this).
- Use relative times for recent events (“2 minutes ago”) and absolute dates for
  older ones.
- Put a space between a number and its unit (“12 MB”), except percentages
  (“50%”).

## Punctuation

- Use curly quotes and apostrophes (“ ” ’) in interface text, not straight ones.
- Use the ellipsis character (…), not three periods.
- Use a middle dot (·) to join short status fragments.
- No period at the end of a label, button, title or single-sentence tooltip.
  Use periods in descriptions of one or more full sentences.
- Avoid dashes as sentence punctuation; use a comma, colon or new sentence.

## Spelling

Write interface text in American English as the source language and translate
from there.
