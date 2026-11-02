# Viola Linux changes

## 2026-11-02

- Sign-in no longer stalls at a passkey step. Chromium's passkey dialog is drawn
  on the hidden engine display, so the engine no longer advertises WebAuthn
  (`PublicKeyCredential` is undefined) and sites offer their other methods.
  Real passkey support returns when the host draws that dialog.

## 2026-10-08

- Restored the shared floating page toolbar, sidebar surfaces, address suggestions,
  downloads, sharing and Mini controls from the qualified native application.
  Native release 36 and independent app version 0.2.10.36 include the current
  pointer-and-rings artwork.
- Retained the Chromium engine, signed host service, application identity and
  profile paths. Added a regression covering bookmark and committed SQLite
  history preservation, including later changes inside the app profile.
