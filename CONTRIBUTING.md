# Contributing to Luma

Changes to OS services, bundled apps, shared UI and hardware profiles belong in this repository. Separate Creative, Office and Imager products have their own scope.

Before opening an issue, search existing reports. Include the app or component, Luma build, CPU architecture, device, steps to reproduce, expected behavior and observed behavior. Remove personal data from logs and screenshots. A device profile is not a promise of hardware support.

For code changes, keep the scope focused, explain the resulting behavior, and run the relevant component tests. Include the commands and results in your pull request. UI changes should cover desktop and narrow layouts, keyboard and touch input, and light and dark appearance where applicable. Do not replace working integrations with cosmetic placeholders.

Preserve upstream notices and license terms. New original source follows the defaults in LICENSE.md; add an SPDX license identifier and the correct copyright attribution. Contributors must have the right to submit their changes. Do not add someone else's sign-off or claim tests you did not run.

Never commit credentials, user databases, private device captures, VM disks, installer images, generated packages or caches. Build and release signing credentials stay outside the source tree. See SECURITY.md for reporting sensitive problems.

The main branch starts with one public baseline commit. Subsequent changes use ordinary reviewable commits and pull requests; source history is not rewritten to conceal later changes.
