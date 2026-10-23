<!-- SPDX-License-Identifier: Apache-2.0 -->
# Native eSIM broker

This component provides `org.projectluma.ESim1` below shared Luma Settings.
The same service source builds for desktop and handheld. Linux owns the modem
and eUICC; this component has no Android dependency.

The system bus activates the hardened root service on demand. Polkit authorizes
each operation. Public profile handles are opaque, sender scoped and bound to
an observed inventory generation. Activation codes arrive through an anonymous
file descriptor. Protected lpac selectors never enter process arguments.
Mutation completion requires inventory readback; an uncertain operation is
not retried automatically. Cancel is scoped to the initiating sender.

Public rows also provide an optional `identifier-suffix` display hint containing
the last four selector characters, so Settings can distinguish similarly named
plans without exposing the full ICCID. Suffixes can collide: clients must never
use this hint as a selector or treat it as a stable identity. All mutations still
require the current sender-bound generation and opaque profile handle.

The provider must declare `luma-lpac-protected-profile-selectors = 1`.
The current provider candidate is the exact-source lpac package with Luma's
four protected-input patches. Hardware absence must report unavailability.

This source integration is not release qualification. Linux package tests,
both architecture builds, cold activation, physical profile operations,
atomic reservation against IMS calls, carrier confirmation-code handling and
the observed debug GIO shutdown failure remain open. See
`docs/research/prairie-core-app-readiness.md` in the repository.
