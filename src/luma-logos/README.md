# luma-logos

Luma's system logos (ADR-040). It replaces Fedora's `fedora-logos`: every
program that asks the system for its logo (Settings › About through
os-release `LOGO=luma-logo`, the login screen, Plymouth's fallback watermark,
`start-here` menus) gets Luma's, and no Fedora mark ships in the image.
Fedora's trademark guidelines do not allow a remix to use Fedora's marks.

Everything is rendered at package build from the canonical wordmark,
`website/public/brand/luma-wordmark.svg`, by `render.py`:

| Icon name | Use |
| --- | --- |
| `luma-logo` | square logo: the wordmark on an Ink tile |
| `luma-logo-dark` | the same tile for dark surfaces (Slate) |
| `luma-logo-text` | the wordmark in Ink, for light surfaces |
| `luma-logo-text-dark` | the wordmark in Paper, for dark surfaces |
| `start-here` | the square logo |

The package `Provides` and `Obsoletes` `fedora-logos` and `fedora-logos-httpd`
and provides `system-logos`, as Fedora's own `generic-logos` does, so
installing it removes Fedora's logos.
