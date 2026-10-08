# Luma core application agent contract

- Every visible core app is one responsive source tree and one binary shared by
  desktop and handheld. Do not create device-specific implementations.
- Use `luma_appkit` components and semantic tokens. App CSS may map app roles;
  it may not redefine the global design system or patch GTK globally.
- Implement real persistent behavior. A launcher, mock, dead control, delayed
  boot script, or extension overlay is not a completed application feature.
- Save transactionally, expose failures, flush pending edits at close, and
  preserve a recoverable deletion path where the product supports deletion.
- Update the exact `prairie-core-apps` pin for desktop and mobile composition,
  run unit/runtime/package tests, and record honest readiness evidence.
