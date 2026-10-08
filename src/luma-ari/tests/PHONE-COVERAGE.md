# Ari phone scenario coverage

The conform scenario includes every desktop state in `phone_states` (36 each).
`test_every_scenario_state_has_phone_coverage` compares the complete sets, so a
new desktop state cannot silently lose phone coverage.

`approval-working` holds the reference approval completion long enough to
capture the running email step. At phone width it opens Places, selects
Activity, closes Places if needed, opens Places again, selects Budget for Theo,
and closes Places if needed through the same visible toggle. The final capture
asserts Working, the step spinner, no approve/decline/Undo controls, a closed
drawer, and receipt bounds inside the phone window. Desktop follows the same
Activity/chat round trip without drawer actions. No screen substitutes for or
omits the pending-approval interaction.

The reference's `ariClick` rebuilds the sidebar through `arAll` before the later
document listener checks the clicked element's window. That detached element
can leave `navopen` set (both phone captures in 20260926-004221 stopped at the
drawer assertion). The scenario therefore uses the public Places toggle to
finish closing the drawer; it does not edit reference behavior or remove the
assertion. Ari closes its phone drawer when selecting a chat or New chat.

The private runtime test exercises Ari's real Places toggle and sidebar rows
at 390px, navigates Activity and back while approval is running, checks the
spinner/actions and allocated receipt bounds, and checks New chat closes the
drawer. All 26 staged native assertions passed on 8cf8dfd9 through the approved
host queue, including these actual drawer actions and fresh native composition
of every one of the 36 states at both desktop and phone widths. Sidebar navigation uses
row activation: GTK focus selection alone must not navigate or close Places.
Those historical broad-state teardown criticals were subsequently fixed by
releasing sheet focus before field replacement/removal and window closure.
Fresh c88ec6c8 staged native checks passed all27 tests in actual normal and high
contrast with no GTK criticals, using the authorized direct host helper and a
private display/bus. Full log: LumaDesign/reports/ari-daytime-20260926/
native-private-wayland-listen.log. Shared391-vs390 toolbar warnings remain
unsuppressed and unaccepted. Passing assertions do not establish conform PASS.

Receipt/catalog metrics, the shared sheet shell, small action controls and the
memory segment bar still depend on their assigned kit requests. Failures remain
failures until a fresh complete four-variant run passes; no deviations or
threshold changes were added.

`provider-error` also has desktop and phone coverage. It renders v70’s declared
`AP.test="bad"` feedback and checks that retry remains available. The native
runtime test drives a refused existing SetCloudKey callback and retries without
connecting an account. This expands coverage without omitting earlier states.
