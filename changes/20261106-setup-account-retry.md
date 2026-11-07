---
type: fix
component: Setup
packages: [luma-setup]
highlight: false
---
Summary: Fixed first-boot setup getting stuck when the account could not be created
Details: If creating the account fails, setup now lets you try again instead of saying this Luma already has an account, and the setup window can no longer be closed by mistake.
