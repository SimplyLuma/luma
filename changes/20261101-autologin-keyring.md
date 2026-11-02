---
type: fix
component: Saved passwords
packages: [luma-keyring]
---
Summary: No keyring password prompts with automatic sign-in
Details: Accounts that sign in automatically keep saved passwords in a keyring this device unlocks, so apps no longer ask to unlock it. An existing password-locked keyring switches over after its password is typed once.
