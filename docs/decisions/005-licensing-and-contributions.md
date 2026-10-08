# ADR-005: Licensing and contribution policy

- **Status:** Accepted
- **Date:** 2026-08-06
- **Owner:** Project Luma
- **Review basis:** open adoption goal, Fedora/GNOME license diversity, and complete planning wiki
- **Revisit trigger:** first upstream fork, public trademark use, or material incompatibility discovered by legal review

## User problem

People must be able to use, study, modify, redistribute, and adopt Luma's original standards while the project respects all upstream obligations and preserves contribution provenance.

## Constraints

Luma will combine original work with components under GPL, LGPL, permissive, Creative Commons, firmware, font, and other terms. One blanket relicense cannot override upstream files. Specifications should be easy for other platforms to adopt.

## Decision

- Original code, build definitions, configuration, schemas, and code samples default to `Apache-2.0`.
- Original documentation and specifications default to `CC-BY-4.0`.
- Original visual/audio/media assets default to `CC-BY-SA-4.0`.
- Derived and third-party files retain their controlling licenses and notices.
- Contributions require Developer Certificate of Origin 1.1 sign-off. No CLA is required.
- SPDX identifiers and REUSE-style annotations are required before public distribution.
- Copyright licenses grant no trademark permission.

## Tradeoffs

Apache-2.0 offers a clear patent grant and low-friction reuse, supporting voluntary adoption. CC-BY allows specification reuse with attribution; ShareAlike keeps modified creative assets open. This policy does not guarantee compatibility with every upstream file, so exact revision-level review remains mandatory.

## Migration and reversal

Relicensing accepted contributions later may require every copyright holder's consent. Therefore exceptions should be explicit from their first commit. Upstream-derived work is never migrated by policy fiat.

## Verification

Before public distribution, vendor canonical license texts, annotate every file, run a REUSE compliance check, generate a component/license inventory, satisfy source/notice obligations, and obtain legal review appropriate to distribution scope.
