# Initial public source baseline

This repository starts with one root commit. It publishes the OS and bundled application source from the sealed source snapshot associated with beta test build `20261008.8`, rather than importing the private development repository's Git history.

- Build version: `1.0.0-beta.1`.
- Installer build time: `2026-10-08T20:20:35Z`.
- Source manifest SHA-256: `6fd2a4fccad0a356c8659c70c75ae5a014cbaa56852170ac56472276290a594a`.
- Compressed source snapshot SHA-256: `c3a831b7767996a3d469dbd16e22f11c89fb60a120f3cafbda3a5e2db13c4445`.
- Installer SHA-256: `cbaaa31ca1e97abfe9333ce8e05ee50bbb754a53b1096f112f4cba0d1b14c1f1`.

The public export excludes private evidence, lab provisioning, generated artifacts, historical development reports and the separate Creative, Office and Imager products. It adds public navigation, contributor guidance and canonical license texts. Selected repository links and lab-only defaults are adapted for public use. Publication does not rebuild or alter the distributed ISO.

Component source is supplied with its package recipes, tests and applicable upstream patches. Building the entire image also requires the named upstream source versions, package artifacts and release services. The current Viola host sources are present, but its full Chromium engine is an external input: the native host builder requires an engine archive with SHA-256 `2760bcadbb3864ffb637888b06e9717642c1add31656c2b81c6d0fb7fd5b1028` and accepts x86_64. This public export is not a claim that the complete installer can be rebuilt solely from a fresh clone without those external inputs.

Some shared catalogues and historical component test harnesses reference separately maintained products or private qualification infrastructure. Their presence documents integration contracts; those dependencies are not silently bundled here. Use the component build guide and applicable tests rather than assuming every private release gate is a public CI target.

Google OAuth is excluded from the first Charlie release pending approval. Public desktop OAuth client metadata in the implementation is not a user access or refresh token. Account credentials, signing private keys and personal device state are excluded from this source publication.
