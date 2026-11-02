---
type: fix
component: Saved passwords
packages: [luma-keyring]
---
Summary: No keyring password prompts with automatic sign-in
Details: Accounts that sign in automatically unlock their saved passwords with a copy of the account password this device keeps sealed, so apps no longer ask. The password still unlocks them. Devices set up earlier ask for it once.
