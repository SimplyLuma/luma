---
type: fix
component: Saved passwords
packages: [luma-keyring]
---
Summary: No keyring password prompts with automatic sign-in
Details: Accounts that sign in automatically unlock their saved passwords with a key this device keeps sealed, so apps no longer ask. The account password recovers them on other hardware and is never stored. Devices set up earlier ask for it once.
