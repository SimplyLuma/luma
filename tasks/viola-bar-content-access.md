# Viola bottom chrome access

Owner: luma-dev-2, root integration agent; worktree arm-desktop.
Status: source and mapped GTK component checks complete; packaged browser validation pending.

The desktop bar has an explicit small frame-edge handle. Ordinary page pointer movement no longer reopens it over bottom web inputs. Same-tab history changes preserve its hidden state; tab changes and address commands reveal it. Reload follows the address field.

Actual validation: normal job bb72dcf8, four mapped GTK component tests passed (6.638 seconds). Tests cover hidden-bar pointer motion, handle/address focus, same-tab history versus tab selection, and reload placement with exactly one command. This does not qualify Chromium rendering, background playback, or release packages. The first harness attempt failed before tests because its Wayland socket path exceeded the platform limit; the corrected short private runtime path passed. Private logs are retained in the task artifact store.

Remaining: include the changed host files in the next explicit package manifest and verify the packaged browser with real pages before claiming these fixes delivered.

Packaging follow-up: native host release37 explicitly includes the new address-focus, startup-navigation and shared-frame modules plus their regressions. Both RPM specs and the common desktop package selection advance together; existing engine inputs remain unchanged at this source checkpoint. Manifest byte checks and both architecture RPM spec expansions passed. Integrated normal job ea27153a passed15 mapped GTK/startup tests after combining the focus and bar changes. Actual RPM/Flatpak builds and browser-engine qualification remain pending.
