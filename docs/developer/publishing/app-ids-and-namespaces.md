---
title: App IDs and namespaces
summary: Choosing a reverse-DNS app ID, the syntax luma lint enforces, how you will prove you control its namespace by domain or code forge, reserved namespaces, and why IDs never change after publication.
order: 3
section: Publishing
---

# App IDs and namespaces

Your **app ID** is a reverse-DNS name such as `com.example.Notebook`. It names
your app everywhere: the Flatpak, the desktop entry, the icon, AppStream, the
D-Bus name, your data directory (`~/.var/app/com.example.Notebook`), granted
permissions, and your Depot listing.

## Choose it once

**App IDs are permanent once your app is public.** Changing one breaks updates
for everyone who installed your app, separates their data from the new app, and
resets its permissions, ratings and install counts. Choose carefully, and choose
before your first public release.

## Syntax

`luma lint` accepts an ID that:

- has **at least three** dot-separated components;
- starts with a letter;
- uses only ASCII letters, digits and underscores in each component;
- has **lowercase** first and second components (the reversed domain);
- does not end in `.desktop`.

Recommended style: the reversed domain in lowercase, then the app name in
UpperCamelCase: `com.example.Notebook`, `io.github.alex_doe.Weather`. Hyphens
are not allowed: Flatpak and D-Bus handle them inconsistently. Replace a hyphen
in a domain with an underscore (`my-company.com` becomes `com.my_company.App`).

## Proving your namespace

You may publish an ID only in a namespace you control, the same rule Flathub
uses.

> **Status: not open yet.** Namespace checks run in **Your apps** in the
> developer portal, which opens after developer applications do. Choose your
> ID now so it is ready.

### Your own domain

To publish `com.example.Notebook`, prove control of `example.com` with the same
check used for organization verification, in one of two ways:

```text
DNS TXT record on example.com:
luma-developer-verification=<token>
```

or a file at:

```text
https://example.com/.well-known/luma-developer.txt
```

containing the token the portal gives you. Keep the record or file in place
after the check passes.

### A code forge account

To publish under `io.github.<username>` or `io.gitlab.<username>`, prove you own
that GitHub or GitLab account. The portal shows you how when you add the app.

For example, `io.github.alex_doe.Weather` belongs to the GitHub account
`alex-doe`: write a hyphen in a username as an underscore in the ID.

### Reserved namespaces

`org.projectluma.*` is reserved for Luma’s own apps. Luma may also refuse IDs
that impersonate another project or organization, even in a namespace you
control.

## If an ID must change

Avoid it. Flatpak can mark an old ID end of life and point installs at a new
one, but the ratings, install counts and granted permissions described above
still start again. Ask Luma before you publish anything under a new ID; whether
a move is possible for a public app is decided case by case.

## Checklist

- [ ] The ID is in a namespace you control and can prove.
- [ ] It passes `luma lint`.
- [ ] The same ID is used in `luma-app.toml`, the Flatpak manifest, the desktop
      entry filename and `Icon=`, the icon filename, and AppStream `<id>` and
      `<launchable>`.
- [ ] `Gtk.Application` or `Adw.Application` is created with the same
      `application_id`.
